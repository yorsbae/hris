from django.urls import reverse
from .models import CateringOrder, Project, ProjectDailyLog
from . import services
from .test_base import HrdBase


class ProjectTests(HrdBase):
    def setUp(self):
        self.login()
        self.p = Project.objects.create(code="PRJ1", name="Gedung A", start_date=self.days(-30), end_date=self.days(30))

    def add(self, **kw):
        d = {"work_date": self.days(-1).isoformat(), "activity": "Pasang bekisting", "worker_niks": "", "headcount": 0, "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_project_log_new", args=[self.p.pk]), d)

    def test_create_project_uppercases_code_and_rejects_duplicate(self):
        d = {"code": " prj2 ", "name": "Gedung B", "location": "Tegal", "start_date": self.days(-5).isoformat(), "end_date": "", "status": "aktif", "note": ""}
        self.client.post(reverse("hrd_project_new"), d); self.assertTrue(Project.objects.filter(code="PRJ2").exists())
        self.assertContains(self.client.post(reverse("hrd_project_new"), {**d, "code": "prj1"}), "sudah dipakai")
        self.assertContains(self.client.post(reverse("hrd_project_new"), {**d, "code": "X", "end_date": self.days(-10).isoformat()}), "Tidak boleh sebelum")
        self.assertEqual(Project.objects.count(), 2)

    def test_log_with_workers_sets_headcount_from_niks(self):
        r = self.add(worker_niks="001, 002\n003 001", headcount=99); self.assertEqual(r.status_code, 302)
        l = ProjectDailyLog.objects.get(); self.assertEqual(sorted(l.workers.values_list("nik", flat=True)), ["001", "002", "003"]); self.assertEqual(l.headcount, 3)  # duplikat dibuang, NIK menimpa angka manual
        a = self.last_audit("project_log_create"); self.assertEqual(a.after["workers"], ["001", "002", "003"])

    def test_log_headcount_manual_when_no_niks(self):
        self.add(headcount=12); self.assertEqual(ProjectDailyLog.objects.get().headcount, 12)

    def test_unknown_or_inactive_worker_rejected_and_listed(self):
        r = self.add(worker_niks="001 999 009"); self.assertContains(r, "999"); self.assertContains(r, "009"); self.assertEqual(ProjectDailyLog.objects.count(), 0)

    def test_too_many_workers_rejected(self):
        self.assertContains(self.add(worker_niks=" ".join(str(i) for i in range(1000, 1400))), "Maksimal 300")

    def test_date_rules(self):
        self.assertContains(self.add(work_date=self.days(1).isoformat()), "masa depan")
        self.assertContains(self.add(work_date=self.days(-31).isoformat()), "Sebelum proyek dimulai")
        self.p.end_date = self.days(-5); self.p.save()
        self.assertContains(self.add(work_date=self.days(-2).isoformat()), "Setelah proyek berakhir")
        self.assertEqual(ProjectDailyLog.objects.count(), 0)
        self.assertEqual(self.add(work_date=self.days(-5).isoformat()).status_code, 302)     # batas akhir inklusif

    def test_multiple_logs_same_day_allowed(self):
        self.add(activity="Regu 1"); self.add(activity="Regu 2"); self.assertEqual(ProjectDailyLog.objects.count(), 2)

    def test_activity_required(self):
        self.assertEqual(self.add(activity="").status_code, 200); self.assertEqual(ProjectDailyLog.objects.count(), 0)

    def test_new_log_blocked_for_non_active_project_but_edit_allowed(self):
        self.add(); l = ProjectDailyLog.objects.get(); self.p.status = "selesai"; self.p.save()
        self.assertRedirects(self.client.get(reverse("hrd_project_log_new", args=[self.p.pk])), reverse("hrd_project_detail", args=[self.p.pk]))
        self.assertEqual(self.client.get(reverse("hrd_project_log_edit", args=[self.p.pk, l.pk])).status_code, 200)   # koreksi tetap boleh

    def test_edit_log_audited_with_before_after(self):
        self.add(worker_niks="001"); l = ProjectDailyLog.objects.get()
        self.client.post(reverse("hrd_project_log_edit", args=[self.p.pk, l.pk]), {"work_date": l.work_date.isoformat(), "activity": "Revisi", "worker_niks": "001 002", "headcount": 0, "note": ""})
        l.refresh_from_db(); self.assertEqual((l.activity, l.headcount), ("Revisi", 2))
        a = self.last_audit("project_log_update"); self.assertEqual((a.before["activity"], a.after["activity"]), ("Pasang bekisting", "Revisi")); self.assertEqual(a.before["workers"], ["001"])

    def test_log_of_other_project_is_404(self):
        other = Project.objects.create(code="PRJ9", name="Lain", start_date=self.days(-30)); self.add(); l = ProjectDailyLog.objects.get()
        self.assertEqual(self.client.get(reverse("hrd_project_log_edit", args=[other.pk, l.pk])).status_code, 404)

    def test_activity_is_escaped(self):
        self.add(activity="<script>alert(1)</script>\nbaris dua")
        r = self.client.get(reverse("hrd_project_detail", args=[self.p.pk])); self.assertNotContains(r, "<script>alert(1)</script>"); self.assertContains(r, "&lt;script&gt;")

    def test_detail_date_filter_and_garbage(self):
        self.add(work_date=self.days(-10).isoformat()); self.add(work_date=self.days(-2).isoformat())
        url = reverse("hrd_project_detail", args=[self.p.pk])
        self.assertEqual(len(self.client.get(url, {"from": self.days(-5).isoformat()}).context["page"]), 1)
        self.assertEqual(self.client.get(url, {"from": "xx", "to": "yy", "page": "zz"}).status_code, 200)
        self.assertEqual(self.client.get(url).context["days"], 2)

    def test_list_shows_log_counts_and_is_ordered(self):
        self.add(); r = self.client.get("/hrd/projects/"); self.assertEqual(r.context["page"][0].n_logs, 1)


class CateringTests(HrdBase):
    def setUp(self): self.login()

    def new(self, **kw):
        d = {"date": self.today().isoformat(), "meal": "siang", "department": "", "qty_large": 10, "qty_small": 5, "price_large": 20000, "price_small": 12000, "vendor": "Bu Tini", "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_catering_new"), d)

    def test_create_with_large_and_small(self):
        self.assertRedirects(self.new(), reverse("hrd_catering"))
        o = CateringOrder.objects.get(); self.assertEqual((o.qty_large, o.qty_small, o.status, o.department), (10, 5, "dipesan", None)); self.assertEqual(o.total_cost, 10 * 20000 + 5 * 12000)
        self.assertEqual(self.last_audit("catering_create").after["qty_large"], "10")

    def test_needs_at_least_one_tepak_and_no_negative(self):
        self.assertContains(self.new(qty_large=0, qty_small=0), "minimal satu"); self.assertEqual(self.new(qty_large=-1).status_code, 200); self.assertEqual(self.new(qty_small="x").status_code, 200)
        self.assertEqual(CateringOrder.objects.count(), 0)
        self.assertEqual(self.new(qty_large=0, qty_small=3).status_code, 302)      # hanya kecil boleh

    def test_duplicate_same_slot_blocked_including_general(self):
        self.new(); self.assertContains(self.new(), "sudah ada")                                          # umum (tanpa departemen) — NULL tidak lolos unique DB
        self.new(department=self.d1.pk); self.assertContains(self.new(department=self.d1.pk), "sudah ada")
        self.new(meal="malam"); self.new(date=self.days(1).isoformat()); self.assertEqual(CateringOrder.objects.count(), 4)

    def test_slot_reusable_after_cancel(self):
        self.new(); o = CateringOrder.objects.get(); services.catering_cancel(o.pk, "libur"); self.new(); self.assertEqual(CateringOrder.objects.count(), 2)

    def test_receive_records_actuals_and_cost_uses_received(self):
        self.new(); o = CateringOrder.objects.get(); url = reverse("hrd_catering_action", args=[o.pk, "receive"])
        r = self.client.post(url, {"received_large": 8, "received_small": 5}, follow=True); o.refresh_from_db()
        self.assertEqual((o.status, o.received_large, o.received_small), ("diterima", 8, 5)); self.assertEqual(o.total_cost, 8 * 20000 + 5 * 12000)
        self.assertEqual(self.last_audit("catering_receive").after["large"], 8)
        r = self.client.post(url, {"received_large": 1, "received_small": 1}, follow=True); self.assertIn("berstatus 'Dipesan'", " ".join(self.msgs(r)))   # tidak bisa dua kali
        o.refresh_from_db(); self.assertEqual(o.received_large, 8)

    def test_receive_validation(self):
        self.new(); o = CateringOrder.objects.get(); url = reverse("hrd_catering_action", args=[o.pk, "receive"])
        for bad in ({"received_large": -1, "received_small": 0}, {"received_large": "a", "received_small": 0}, {"received_large": 1}):
            r = self.client.post(url, bad, follow=True); self.assertIn("angka 0", " ".join(self.msgs(r)))
        o.refresh_from_db(); self.assertEqual(o.status, "dipesan")
        self.client.post(url, {"received_large": 0, "received_small": 0}); o.refresh_from_db(); self.assertEqual((o.status, o.received_large), ("diterima", 0))   # 0 diterima sah

    def test_cancel_requires_reason_and_zero_cost(self):
        self.new(); o = CateringOrder.objects.get(); url = reverse("hrd_catering_action", args=[o.pk, "cancel"])
        r = self.client.post(url, {"reason": ""}, follow=True); self.assertIn("Alasan", " ".join(self.msgs(r))); o.refresh_from_db(); self.assertEqual(o.status, "dipesan")
        self.client.post(url, {"reason": "Pabrik libur"}); o.refresh_from_db(); self.assertEqual((o.status, o.total_cost), ("batal", 0)); self.assertIn("Pabrik libur", o.note)

    def test_edit_only_while_ordered_and_ignores_self_in_duplicate_check(self):
        self.new(); o = CateringOrder.objects.get(); url = reverse("hrd_catering_edit", args=[o.pk])
        self.client.post(url, {"date": o.date.isoformat(), "meal": "siang", "department": "", "qty_large": 20, "qty_small": 5, "price_large": 20000, "price_small": 12000, "vendor": "Bu Tini", "note": ""})
        o.refresh_from_db(); self.assertEqual(o.qty_large, 20); a = self.last_audit("catering_update"); self.assertEqual((a.before["qty_large"], a.after["qty_large"]), ("10", "20"))
        services.catering_receive(o.pk, 20, 5); self.assertRedirects(self.client.get(url), reverse("hrd_catering"))

    def test_summary_totals(self):
        self.new(qty_large=10, qty_small=5)                                                                           # dipesan: 10/5
        self.new(meal="malam", qty_large=4, qty_small=2); o = CateringOrder.objects.get(meal="malam"); services.catering_receive(o.pk, 3, 2)   # diterima: 3/2
        self.new(meal="snack", qty_large=100, qty_small=100); services.catering_cancel(CateringOrder.objects.get(meal="snack").pk, "batal")  # tidak dihitung
        r = self.client.get("/hrd/catering/"); self.assertEqual((r.context["sum_large"], r.context["sum_small"]), (13, 7))
        self.assertEqual(r.context["sum_cost"], (10 * 20000 + 5 * 12000) + (3 * 20000 + 2 * 12000))

    def test_filters_period_meal_department_and_garbage(self):
        self.new(); self.new(meal="malam", department=self.d1.pk); self.new(date=self.days(-60).isoformat())
        c = lambda **q: len(self.client.get("/hrd/catering/", q).context["page"])  # noqa: E731
        self.assertEqual(c(), 2)                                                               # bawaan: bulan berjalan s/d hari ini (data -60 hari di luar)
        self.assertEqual(c(**{"from": self.days(-90).isoformat(), "to": self.days(0).isoformat()}), 3)
        self.assertEqual(c(meal="malam"), 1); self.assertEqual(c(department=self.d1.pk), 1)
        for q in ({"from": "xx"}, {"to": "99-99"}, {"department": "abc"}, {"page": "x"}): self.assertEqual(self.client.get("/hrd/catering/", q).status_code, 200, q)

    def test_note_is_escaped(self):
        self.new(note="<b onmouseover=1>x</b>", vendor="<script>1</script>"); o = CateringOrder.objects.get(); services.catering_cancel(o.pk, "<i>y</i>")
        r = self.client.get("/hrd/catering/"); self.assertNotContains(r, "<script>1</script>"); self.assertNotContains(r, "<i>y</i>")
