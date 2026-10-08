from datetime import date
from django.urls import reverse
from . import services
from .models import Aid, MaternityLeave
from apps.hr.models import ChangeRequest
from .test_base import HrdBase


class AidTests(HrdBase):
    """Bantuan = rekap (putaran 20): tanpa status/persetujuan; ubah & hapus boleh, semuanya tercatat di audit."""
    def setUp(self): self.login()

    def new(self, **kw):
        d = {"nik": "001", "kind": "kematian", "event_date": self.today().isoformat(), "amount": 1_000_000, "description": ""}; d.update(kw)
        return self.client.post(reverse("hrd_aid_new"), d)

    def test_create_and_audit_without_status(self):
        self.assertRedirects(self.new(description="Ayah meninggal"), reverse("hrd_aids"))
        a = Aid.objects.get(); self.assertEqual((a.employee_id, a.kind, a.amount, a.created_by_id), (self.e1.pk, "kematian", 1_000_000, self.hrd.pk))
        self.assertFalse(hasattr(a, "status")); self.assertEqual(self.last_audit("aid_create").after["amount"], 1_000_000)

    def test_rejects_bad_input(self):
        self.assertContains(self.new(nik="999"), "tidak ditemukan"); self.assertContains(self.new(nik="009"), "tidak ditemukan")
        for bad in ({"amount": 0}, {"amount": -5}, {"amount": "abc"}, {"kind": "zzz"}, {"event_date": "bukan-tanggal"}):
            self.assertEqual(self.new(**bad).status_code, 200)
        self.assertEqual(Aid.objects.count(), 0)

    def test_deleted_employee_not_found(self):
        self.e3.soft_delete(self.hrd, "uji"); self.assertContains(self.new(nik="003"), "tidak ditemukan")

    def test_duplicate_same_employee_kind_date_blocked(self):
        self.new(); self.assertContains(self.new(), "sudah tercatat"); self.assertEqual(Aid.objects.count(), 1)
        self.new(kind="pernikahan"); self.assertEqual(Aid.objects.count(), 2)

    def test_edit_any_time_audited_and_delete_audited(self):
        self.new(); a = Aid.objects.get()
        r = self.client.post(reverse("hrd_aid_edit", args=[a.pk]), {"kind": "kematian", "event_date": self.today().isoformat(), "amount": 2_500_000, "description": "koreksi"})
        self.assertRedirects(r, reverse("hrd_aids")); a.refresh_from_db(); self.assertEqual(a.amount, 2_500_000)
        au = self.last_audit("aid_update"); self.assertEqual((au.before["amount"], au.after["amount"]), (1_000_000, 2_500_000))
        self.assertEqual(self.client.get(reverse("hrd_aid_delete", args=[a.pk])).status_code, 405)  # hapus hanya POST
        self.client.post(reverse("hrd_aid_delete", args=[a.pk])); self.assertEqual(Aid.objects.count(), 0)
        self.assertEqual(self.last_audit("aid_delete").before["amount"], 2_500_000)

    def test_list_filter_totals_and_xss(self):
        Aid.objects.create(employee=self.e1, kind="kematian", event_date=date(2026, 1, 5), amount=1000, description="<script>x</script>")
        Aid.objects.create(employee=self.e2, kind="pernikahan", event_date=date(2025, 3, 5), amount=500)
        r = self.client.get(reverse("hrd_aids")); self.assertEqual((r.context["total"], r.context["count"]), (1500, 2)); self.assertNotContains(r, "<script>x</script>")
        r = self.client.get(reverse("hrd_aids") + "?kind=kematian"); self.assertEqual(r.context["total"], 1000)
        r = self.client.get(reverse("hrd_aids") + "?year=2025"); self.assertEqual(r.context["total"], 500)
        self.assertEqual(self.client.get(reverse("hrd_aids") + "?year=abc&q=%27").status_code, 200)


class MaternityTests(HrdBase):
    def setUp(self): self.login()

    def new(self, **kw):
        d = {"nik": "002", "due_date": self.days(60).isoformat(), "start_date": "", "end_date": "", "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_maternity_new"), d, follow=True)

    def test_defaults_computed_from_due_date(self):
        self.new(); m = MaternityLeave.objects.get()
        self.assertEqual((m.start_date, m.end_date), (self.days(60 - services.MATERNITY_DAYS_BEFORE), self.days(60 + services.MATERNITY_DAYS_AFTER)))
        self.assertEqual(self.last_audit("maternity_create").after["employee"], "002")

    def test_explicit_dates_win(self):
        self.new(start_date=self.days(40).isoformat(), end_date=self.days(130).isoformat()); m = MaternityLeave.objects.get()
        self.assertEqual((m.start_date, m.end_date), (self.days(40), self.days(130)))

    def test_only_active_female_employees(self):
        self.assertContains(self.new(nik="001"), "jenis kelamin")     # laki-laki
        self.assertContains(self.new(nik="009"), "tidak ditemukan")   # nonaktif
        self.assertContains(self.new(nik="999"), "tidak ditemukan")
        self.assertEqual(MaternityLeave.objects.count(), 0)

    def test_end_before_start_rejected(self):
        self.assertContains(self.new(start_date=self.days(50).isoformat(), end_date=self.days(40).isoformat()), "Tidak boleh sebelum")
        self.assertEqual(MaternityLeave.objects.count(), 0)

    def test_overlap_blocked_but_allowed_after_cancel(self):
        self.new(); self.assertContains(self.new(due_date=self.days(70).isoformat()), "tumpang tindih"); self.assertEqual(MaternityLeave.objects.count(), 1)
        services.maternity_cancel(MaternityLeave.objects.get().pk, "salah input")
        self.new(due_date=self.days(70).isoformat()); self.assertEqual(MaternityLeave.objects.count(), 2)

    def test_adjacent_ranges_do_not_overlap(self):
        self.new(start_date="2030-01-01", end_date="2030-03-31", due_date="2030-02-15")
        self.new(start_date="2030-04-01", end_date="2030-06-30", due_date="2030-05-15"); self.assertEqual(MaternityLeave.objects.count(), 2)

    def test_warns_on_overlapping_leave_request_but_saves(self):
        ChangeRequest.objects.create(type="cuti", employee=self.e2, department=self.d2, status="approved", requested_by=self.hrd,
                                     payload={"start_date": self.days(55).isoformat(), "end_date": self.days(58).isoformat()})
        r = self.new(); self.assertEqual(MaternityLeave.objects.count(), 1)
        self.assertTrue(any("tumpang tindih dengan rentang cuti hamil" in m for m in self.msgs(r)))

    def test_edit_keeps_employee_and_is_audited(self):
        self.new(); m = MaternityLeave.objects.get(); url = reverse("hrd_maternity_edit", args=[m.pk])
        self.client.post(url, {"nik": "003", "due_date": self.days(65).isoformat(), "start_date": "", "end_date": "", "note": "geser"})
        m.refresh_from_db(); self.assertEqual((m.employee, m.due_date, m.note), (self.e2, self.days(65), "geser"))   # NIK tidak bisa diganti
        a = self.last_audit("maternity_update"); self.assertNotEqual(a.before["due_date"], a.after["due_date"])

    def test_edit_ignores_itself_in_overlap_check(self):
        self.new(); m = MaternityLeave.objects.get()
        self.client.post(reverse("hrd_maternity_edit", args=[m.pk]), {"nik": "002", "due_date": m.due_date.isoformat(), "start_date": m.start_date.isoformat(), "end_date": m.end_date.isoformat(), "note": "tetap"})
        m.refresh_from_db(); self.assertEqual(m.note, "tetap")

    def test_finish_and_cancel_flow(self):
        self.new(); m = MaternityLeave.objects.get(); act = lambda x, **d: self.client.post(reverse("hrd_maternity_action", args=[m.pk, x]), d, follow=True)  # noqa: E731
        r = act("finish", delivery_date=self.days(1).isoformat()); self.assertIn("masa depan", " ".join(self.msgs(r)))   # tanggal lahir di masa depan ditolak
        m.refresh_from_db(); self.assertEqual(m.state, "aktif")
        act("finish", delivery_date=self.today().isoformat()); m.refresh_from_db(); self.assertEqual((m.state, m.delivery_date), ("selesai", self.today()))
        r = act("cancel", reason="x"); self.assertIn("berstatus aktif", " ".join(self.msgs(r)))       # sudah selesai
        r = act("finish"); self.assertIn("berstatus aktif", " ".join(self.msgs(r)))                    # tidak bisa dua kali

    def test_cancel_requires_reason(self):
        self.new(); m = MaternityLeave.objects.get(); url = reverse("hrd_maternity_action", args=[m.pk, "cancel"])
        r = self.client.post(url, {"reason": " "}, follow=True); self.assertIn("Alasan", " ".join(self.msgs(r))); m.refresh_from_db(); self.assertEqual(m.state, "aktif")
        self.client.post(url, {"reason": "Keguguran/ditunda atas permintaan"}); m.refresh_from_db(); self.assertEqual((m.state, bool(m.cancel_reason)), ("batal", True))
        self.assertEqual(self.client.get(reverse("hrd_maternity_edit", args=[m.pk])).status_code, 302)   # tidak bisa diubah lagi

    def test_phase_labels(self):
        m = MaternityLeave(start_date=self.days(5), end_date=self.days(95), state="aktif"); t = self.today()
        self.assertEqual(m.phase(t), "Belum mulai"); self.assertEqual(m.phase(self.days(10)), "Sedang cuti"); self.assertIn("Lewat", m.phase(self.days(100)))
        m.state = "selesai"; self.assertEqual(m.phase(t), "Selesai")

    def test_no_medical_fields_on_model(self):
        names = {f.name for f in MaternityLeave._meta.get_fields()}
        self.assertFalse(names & {"diagnosis", "diagnosa", "medical", "complication", "kondisi"})

    def test_list_and_dashboard_counts_running_only(self):
        MaternityLeave.objects.create(employee=self.e2, due_date=self.days(30), start_date=self.days(-10), end_date=self.days(80))   # berjalan
        MaternityLeave.objects.create(employee=self.e3, due_date=self.days(300), start_date=self.days(250), end_date=self.days(340))  # belum mulai
        self.assertEqual(self.client.get("/api/dashboard/").json()["maternity_active"], 1)
        r = self.client.get("/hrd/maternity/"); self.assertEqual(sorted(m.phase_label for m in r.context["page"]), ["Belum mulai", "Sedang cuti"])
