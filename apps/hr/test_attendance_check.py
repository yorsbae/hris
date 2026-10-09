"""Putaran 26: P7 Validasi kehadiran (HRD → Admin Dept → verifikasi HRD)."""
from datetime import date, timedelta
from django.urls import reverse
from apps.core.models import AuditLog, Notification, User
from apps.hr.models import AttendanceCheck as AC, AttendanceCheckEvent as Ev, ChangeRequest
from apps.hr import attendance_check as svc
from apps.hrd.test_base import HrdBase


class Base(HrdBase):
    def ask(self, niks="001", d1=None, d2=None, **kw):
        d1 = d1 or self.days(-1); d = {"niks": niks, "date_from": d1.isoformat(), "date_to": (d2 or d1).isoformat(), "question": "Mohon konfirmasi"}; d.update(kw)
        return self.client.post(reverse("attcheck_new"), d)

    def make(self, nik="001", d=None):
        self.login("hrd"); self.ask(nik, d or self.days(-1)); return AC.objects.get(employee__nik=nik, date=d or self.days(-1))

    def act(self, name, route, pk, **data): self.login(name); return self.client.post(reverse(route, args=[pk]), data)


class CreateTests(Base):
    def test_create_range_notifies_dept_admin_only(self):
        self.login("hrd"); r = self.ask("001,003", self.days(-3), self.days(-1)); self.assertRedirects(r, reverse("attcheck_list"))
        self.assertEqual(AC.objects.count(), 6); self.assertEqual(Ev.objects.filter(action="minta").count(), 6)
        n = Notification.objects.filter(kind="absensi"); self.assertEqual(n.count(), 1); self.assertEqual(n[0].user, self.adm)
        self.assertEqual(AuditLog.objects.get(action="attendance_check_new").after["rows"], 6)

    def test_whole_department(self):
        self.login("hrd"); self.client.post(reverse("attcheck_new"), {"niks": "", "department": self.d1.pk, "date_from": self.days(-1).isoformat(), "date_to": ""})
        self.assertEqual(set(AC.objects.values_list("employee__nik", flat=True)), {"001", "003"})  # nonaktif (009) tidak ikut

    def test_rejections_and_all_or_nothing(self):
        self.login("hrd")
        for bad in (dict(d1=self.days(1)), dict(niks="999"), dict(niks="009"), dict(niks="002"), dict(niks="")):
            self.assertEqual(self.ask(**bad).status_code, 200, bad)
        self.assertEqual(self.ask("001,002").status_code, 200)  # 002: Gudang belum punya Admin → seluruh permintaan gagal
        self.assertEqual(AC.objects.count(), 0)
        self.assertContains(self.ask("002"), "belum punya Admin Departemen")

    def test_reversed_or_too_long_range_rejected(self):
        self.login("hrd"); self.assertEqual(self.ask("001", self.days(-1), self.days(-3)).status_code, 200); self.assertEqual(self.ask("001", self.days(-60), self.days(-1)).status_code, 200); self.assertEqual(AC.objects.count(), 0)

    def test_duplicate_active_rejected_but_cancelled_can_be_asked_again(self):
        a = self.make(); self.assertContains(self.ask("001"), "sudah ada permintaan aktif"); self.assertEqual(AC.objects.count(), 1)
        self.act("hrd", "attcheck_cancel", a.pk, reason="Salah tanggal"); self.assertEqual(self.ask("001").status_code, 302); self.assertEqual(AC.objects.count(), 2)

    def test_hint_from_executed_request_and_due_skips_sunday(self):
        d = self.days(-2); ChangeRequest.objects.create(type="cuti", employee=self.e1, department=self.d1, status="executed", payload={"start_date": (d - timedelta(1)).isoformat(), "end_date": d.isoformat()}, requested_by=self.adm)
        self.assertIn("cuti", self.make("001", d).hint)
        sat = date(2026, 10, 10); self.assertEqual(svc.due_from(sat, 2), date(2026, 10, 13))  # Sabtu +2 hari kerja, Minggu dilewati → Selasa


class AccessTests(Base):
    def test_poli_and_anonymous_blocked_everywhere(self):
        a = self.make(); self.client.logout()
        for url in (reverse("attcheck_list"), reverse("attcheck_new"), reverse("attcheck_detail", args=[a.pk])): self.assertIn(self.client.get(url).status_code, (401, 302), url)
        self.login("poli")
        for url in (reverse("attcheck_list"), reverse("attcheck_new"), reverse("attcheck_detail", args=[a.pk])): self.assertEqual(self.client.get(url).status_code, 403, url)
        for route in ("attcheck_answer", "attcheck_verify", "attcheck_cancel", "attcheck_correct"): self.assertEqual(self.client.post(reverse(route, args=[a.pk])).status_code, 403, route)

    def test_dept_admin_cannot_create_verify_cancel_correct(self):
        a = self.make(); self.login("adm"); self.assertEqual(self.client.get(reverse("attcheck_new")).status_code, 403)
        for route in ("attcheck_verify", "attcheck_cancel", "attcheck_correct"): self.assertEqual(self.client.post(reverse(route, args=[a.pk]), {"action": "terima"}).status_code, 403, route)

    def test_hrd_cannot_answer_in_place_of_admin(self):
        a = self.make(); self.assertEqual(self.act("hrd", "attcheck_answer", a.pk, answer="hadir").status_code, 404); a.refresh_from_db(); self.assertEqual(a.status, "diminta")

    def test_other_department_admin_gets_404_everywhere(self):
        a = self.make(); other = User.objects.create_user("adm2", password="kata-sandi-panjang-123", role="dept_admin", department=self.d2); self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("attcheck_detail", args=[a.pk])).status_code, 404); self.assertEqual(self.client.post(reverse("attcheck_answer", args=[a.pk]), {"answer": "hadir"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("attcheck_list")), "Budi"); a.refresh_from_db(); self.assertEqual(a.status, "diminta")

    def test_dept_admin_list_scoped_and_no_export(self):
        self.make("001"); self.login("adm"); r = self.client.get(reverse("attcheck_list")); self.assertContains(r, "Budi")
        x = self.client.get(reverse("attcheck_list"), {"export": "csv"}); self.assertIn("text/html", x["Content-Type"])  # ekspor hanya HRD

    def test_menu_entry_for_hrd_and_dept_admin_not_poli(self):
        for who, yes in (("hrd", True), ("adm", True), ("su", True), ("poli", False)):
            self.login(who); self.assertEqual('href="/validasi/"' in self.client.get("/").content.decode(), yes, who)


class FlowTests(Base):
    def test_answer_rules(self):
        a = self.make()
        for bad in ({"answer": ""}, {"answer": "libur"}, {"answer": "izin", "note": ""}, {"answer": "alfa", "note": "  "}, {"answer": "hadir", "note": "x" * 301}): self.act("adm", "attcheck_answer", a.pk, **bad)
        a.refresh_from_db(); self.assertEqual(a.status, "diminta")
        self.act("adm", "attcheck_answer", a.pk, answer="sakit"); a.refresh_from_db(); self.assertEqual((a.status, a.answer, a.answered_by), ("dijawab", "sakit", self.adm))
        self.assertTrue(Notification.objects.filter(user=self.hrd, kind="absensi").exists())
        self.act("adm", "attcheck_answer", a.pk, answer="hadir"); a.refresh_from_db(); self.assertEqual(a.answer, "sakit")  # sudah dijawab → tidak bisa timpa

    def test_verify_accept_locks_and_correction_is_append_only(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="hadir"); self.act("hrd", "attcheck_verify", a.pk, action="terima"); a.refresh_from_db()
        self.assertEqual((a.status, a.final_answer, a.result), ("diverifikasi", "hadir", "hadir"))
        self.act("adm", "attcheck_answer", a.pk, answer="alfa", note="x"); self.act("hrd", "attcheck_cancel", a.pk, reason="x"); a.refresh_from_db(); self.assertEqual((a.status, a.answer), ("diverifikasi", "hadir"))
        self.act("hrd", "attcheck_correct", a.pk, answer="alfa", note=""); self.assertEqual(Ev.objects.filter(action="koreksi").count(), 0)
        self.act("hrd", "attcheck_correct", a.pk, answer="alfa", note="Bukti mesin"); a.refresh_from_db()
        self.assertEqual((a.final_answer, a.answer, a.result), ("hadir", "hadir", "alfa"))  # asli utuh, hasil berlaku = koreksi
        self.act("hrd", "attcheck_correct", a.pk, answer="izin", note="Surat menyusul"); a.refresh_from_db(); self.assertEqual(a.result, "izin"); self.assertEqual(Ev.objects.filter(action="koreksi").count(), 2)

    def test_return_requires_note_and_reanswer_works(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="izin", note="Keperluan keluarga")
        self.act("hrd", "attcheck_verify", a.pk, action="kembalikan", note=""); a.refresh_from_db(); self.assertEqual(a.status, "dijawab")
        self.act("hrd", "attcheck_verify", a.pk, action="kembalikan", note="Mana suratnya?"); a.refresh_from_db(); self.assertEqual(a.status, "dikembalikan")
        self.assertTrue(Notification.objects.filter(user=self.adm, title__contains="dikembalikan").exists())
        self.act("adm", "attcheck_answer", a.pk, answer="izin", note="Surat ada di HRD"); self.act("hrd", "attcheck_verify", a.pk, action="terima"); a.refresh_from_db(); self.assertEqual(a.status, "diverifikasi")

    def test_change_requires_note_and_value(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="hadir")
        self.act("hrd", "attcheck_verify", a.pk, action="ubah", final="alfa", note=""); self.act("hrd", "attcheck_verify", a.pk, action="ubah", final="", note="x"); a.refresh_from_db(); self.assertEqual(a.status, "dijawab")
        self.act("hrd", "attcheck_verify", a.pk, action="ubah", final="alfa", note="Mesin tidak mencatat"); a.refresh_from_db(); self.assertEqual((a.answer, a.final_answer, a.verify_note), ("hadir", "alfa", "Mesin tidak mencatat"))

    def test_verify_only_from_answered_and_unknown_action(self):
        a = self.make(); self.act("hrd", "attcheck_verify", a.pk, action="terima"); a.refresh_from_db(); self.assertEqual(a.status, "diminta")
        self.act("adm", "attcheck_answer", a.pk, answer="hadir"); self.act("hrd", "attcheck_verify", a.pk, action="hapus"); a.refresh_from_db(); self.assertEqual(a.status, "dijawab")

    def test_cancel_needs_reason_and_notifies_admin(self):
        a = self.make(); self.act("hrd", "attcheck_cancel", a.pk, reason=""); a.refresh_from_db(); self.assertEqual(a.status, "diminta")
        self.act("hrd", "attcheck_cancel", a.pk, reason="Tidak perlu"); a.refresh_from_db(); self.assertEqual((a.status, a.cancel_reason), ("dibatalkan", "Tidak perlu"))
        self.assertTrue(Notification.objects.filter(user=self.adm, title__contains="dibatalkan").exists()); self.act("adm", "attcheck_answer", a.pk, answer="hadir"); a.refresh_from_db(); self.assertEqual(a.status, "dibatalkan")

    def test_alfa_conflicting_with_executed_leave_is_flagged_not_blocked(self):
        d = self.days(-2); ChangeRequest.objects.create(type="izin", employee=self.e1, department=self.d1, status="executed", payload={"start_date": d.isoformat(), "end_date": d.isoformat()}, requested_by=self.adm)
        a = self.make("001", d); self.act("adm", "attcheck_answer", a.pk, answer="alfa", note="Tidak masuk"); a.refresh_from_db(); self.assertIn("bertabrakan", a.conflict); self.assertEqual(a.status, "dijawab")
        self.login("hrd"); self.assertContains(self.client.get(reverse("attcheck_list")), "Konflik")

    def test_late_is_computed_and_filterable(self):
        a = self.make(); AC.objects.filter(pk=a.pk).update(due_date=self.days(-1)); a.refresh_from_db(); self.assertTrue(a.is_late())
        self.login("hrd"); self.assertContains(self.client.get(reverse("attcheck_list"), {"status": "late"}), "Terlambat"); self.act("adm", "attcheck_answer", a.pk, answer="hadir"); a.refresh_from_db(); self.assertFalse(a.is_late())


class IntegrityTests(Base):
    def test_events_immutable_and_unique_constraint(self):
        a = self.make(); e = a.events.first()
        with self.assertRaises(PermissionError): e.delete()
        e.note = "ubah"
        with self.assertRaises(PermissionError): e.save()
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic(): AC.objects.create(employee=self.e1, department=self.d1, date=a.date, due_date=a.date, requested_by=self.hrd)

    def test_audit_never_contains_notes_and_views_are_audited(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="izin", note="RAHASIA-KELUARGA-123"); self.login("hrd"); self.client.get(reverse("attcheck_detail", args=[a.pk]))
        self.assertNotIn("RAHASIA-KELUARGA-123", str(list(AuditLog.objects.values_list("before", "after")))); self.assertTrue(AuditLog.objects.filter(action="attendance_check_view").exists())

    def test_export_hrd_only_xlsx_csv_and_formula_safe(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="izin", note="=HYPERLINK(1)"); self.login("hrd")
        r = self.client.get(reverse("attcheck_list"), {"export": "csv"}); body = r.content.decode("utf-8-sig"); self.assertIn("001", body); self.assertIn("'=HYPERLINK", body)
        self.assertEqual(self.client.get(reverse("attcheck_list"), {"export": "xlsx"}).status_code, 200); self.assertTrue(AuditLog.objects.filter(action="attendance_check_export").exists())

    def test_garbage_filters_do_not_crash(self):
        self.make(); self.login("hrd"); self.assertEqual(self.client.get(reverse("attcheck_list"), {"status": "zzz", "dept": "x", "date": "bukan", "page": "abc"}).status_code, 200)
        self.assertEqual(self.client.post(reverse("attcheck_verify", args=[99999]), {}).status_code, 404)


class ReminderDashboardTests(Base):
    def test_reminder_due_tomorrow_late_and_no_pile_up(self):
        from apps.hr.management.commands.remind_attendance_checks import run
        from apps.core.models import Notification
        a = self.make(); Notification.objects.all().delete()
        AC.objects.filter(pk=a.pk).update(due_date=self.days(5)); self.assertEqual(run(), 0)  # masih lama
        AC.objects.filter(pk=a.pk).update(due_date=self.days(1)); self.assertEqual(run(), 1); self.assertEqual(run(), 0)  # tidak menumpuk
        n = Notification.objects.get(user=self.adm); self.assertIn("jatuh tempo besok", n.title)
        AC.objects.filter(pk=a.pk).update(due_date=self.days(-1)); self.assertEqual(run(), 2)  # Admin (terlambat) + HRD pembuat
        self.assertTrue(Notification.objects.filter(user=self.hrd, title__contains="belum dijawab Admin").exists())

    def test_reminder_skips_answered_and_other_department(self):
        from apps.hr.management.commands.remind_attendance_checks import run
        a = self.make(); AC.objects.filter(pk=a.pk).update(due_date=self.days(-1)); self.act("adm", "attcheck_answer", a.pk, answer="hadir")
        from apps.core.models import Notification; Notification.objects.all().delete(); self.assertEqual(run(), 0)

    def test_dashboard_counts_by_role_and_scope(self):
        a = self.make(); AC.objects.filter(pk=a.pk).update(due_date=self.days(-1))
        self.login("adm"); d = self.client.get("/api/dashboard/").json(); self.assertEqual((d["attcheck_todo"], d["attcheck_late"]), (1, 1))
        self.login("hrd"); d = self.client.get("/api/dashboard/").json(); self.assertEqual((d["attcheck_to_verify"], d["attcheck_late"]), (0, 1)); self.assertNotIn("attcheck_todo", d)
        other = User.objects.create_user("adm2", password="kata-sandi-panjang-123", role="dept_admin", department=self.d2); self.client.force_login(other)
        d = self.client.get("/api/dashboard/").json(); self.assertEqual((d["attcheck_todo"], d["attcheck_late"]), (0, 0))  # departemen lain tidak bocor (dicek selagi masih terbuka)
        self.act("adm", "attcheck_answer", a.pk, answer="hadir"); self.login("hrd"); self.assertEqual(self.client.get("/api/dashboard/").json()["attcheck_to_verify"], 1)
        self.login("poli"); self.assertNotIn("attcheck_late", self.client.get("/api/dashboard/").json())
