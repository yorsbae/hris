"""Putaran 20: pekerja harian proyek (bukan karyawan, dengan upah) dan rekap katering (pesan/diterima; jam 09/12/18/02)."""
from django.urls import reverse
from .models import CateringOrder, Project, ProjectWork
from .test_base import HrdBase


class ProjectTests(HrdBase):
    def setUp(self):
        self.login()
        self.p = Project.objects.create(code="PRJ1", name="Gedung A", start_date=self.days(-30), end_date=self.days(30))

    def add(self, **kw):
        d = {"work_date": self.days(-1).isoformat(), "worker_name": "Slamet", "activity": "Pasang bekisting", "wage": 120000, "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_project_work_new", args=[self.p.pk]), d)

    def test_create_project_uppercases_code_and_rejects_duplicate(self):
        self.client.post(reverse("hrd_project_new"), {"code": "prj-2", "name": "B", "start_date": self.days(0).isoformat(), "status": "aktif"})
        self.assertTrue(Project.objects.filter(code="PRJ-2").exists())
        r = self.client.post(reverse("hrd_project_new"), {"code": "prj1", "name": "dobel", "start_date": self.days(0).isoformat(), "status": "aktif"})
        self.assertContains(r, "sudah dipakai")

    def test_worker_is_free_text_not_employee_and_wage_saved(self):
        self.assertRedirects(self.add(worker_name="  Pak   Darto "), reverse("hrd_project_detail", args=[self.p.pk]))
        w = ProjectWork.objects.get(); self.assertEqual((w.worker_name, w.wage, w.created_by_id), ("Pak Darto", 120000, self.hrd.pk))
        self.assertEqual(self.last_audit("project_work_create").after["wage"], "120000")

    def test_validation_rules(self):
        for bad in ({"worker_name": " "}, {"activity": ""}, {"wage": -1}, {"wage": "abc"}, {"wage": 200_000_000}, {"work_date": self.days(2).isoformat()},
                    {"work_date": self.days(-60).isoformat()}):
            self.assertEqual(self.add(**bad).status_code, 200, bad)
        self.assertEqual(ProjectWork.objects.count(), 0)
        self.add(wage=0); self.assertEqual(ProjectWork.objects.count(), 1)  # upah 0 boleh (belum ditentukan)

    def test_same_worker_same_day_different_job_allowed_exact_duplicate_blocked(self):
        self.add(); self.assertContains(self.add(), "sudah ada"); self.add(activity="Cor lantai"); self.assertEqual(ProjectWork.objects.count(), 2)

    def test_save_and_add_again_keeps_date(self):
        r = self.add(again="1", work_date=self.days(-3).isoformat())
        self.assertRedirects(r, reverse("hrd_project_work_new", args=[self.p.pk]) + f"?date={self.days(-3).isoformat()}")
        self.assertContains(self.client.get(r.url), self.days(-3).isoformat())

    def test_new_blocked_for_non_active_project_but_edit_allowed(self):
        self.add(); w = ProjectWork.objects.get(); self.p.status = "selesai"; self.p.save()
        self.assertEqual(self.add(worker_name="Baru").status_code, 302); self.assertEqual(ProjectWork.objects.count(), 1)
        r = self.client.post(reverse("hrd_project_work_edit", args=[self.p.pk, w.pk]), {"work_date": w.work_date.isoformat(), "worker_name": "Slamet", "activity": "Pasang bekisting", "wage": 150000, "note": ""})
        self.assertEqual(r.status_code, 302); w.refresh_from_db(); self.assertEqual(w.wage, 150000)
        au = self.last_audit("project_work_update"); self.assertEqual((au.before["wage"], au.after["wage"]), ("120000", "150000"))

    def test_delete_post_only_audited_and_other_project_404(self):
        self.add(); w = ProjectWork.objects.get(); other = Project.objects.create(code="PRJ9", name="x", start_date=self.days(-10))
        self.assertEqual(self.client.post(reverse("hrd_project_work_delete", args=[other.pk, w.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("hrd_project_work_delete", args=[self.p.pk, w.pk])).status_code, 405)
        self.client.post(reverse("hrd_project_work_delete", args=[self.p.pk, w.pk])); self.assertEqual(ProjectWork.objects.count(), 0)
        self.assertEqual(self.last_audit("project_work_delete").before["worker"], "Slamet")

    def test_detail_totals_per_worker_and_filters(self):
        for d, n, wage in ((-1, "Slamet", 100000), (-2, "Slamet", 100000), (-2, "Darto", 150000)):
            ProjectWork.objects.create(project=self.p, work_date=self.days(d), worker_name=n, activity="Cor", wage=wage)
        r = self.client.get(reverse("hrd_project_detail", args=[self.p.pk]))
        self.assertEqual((r.context["agg"]["t"], r.context["agg"]["n"], r.context["agg"]["days"]), (350000, 3, 2))
        pw = {x["worker_name"]: (x["days"], x["total"]) for x in r.context["per_worker"]}; self.assertEqual(pw, {"Slamet": (2, 200000), "Darto": (1, 150000)})
        r = self.client.get(reverse("hrd_project_detail", args=[self.p.pk]) + f"?q=darto&from={self.days(-3).isoformat()}"); self.assertEqual(r.context["agg"]["t"], 150000)
        self.assertEqual(self.client.get(reverse("hrd_project_detail", args=[self.p.pk]) + "?from=xx&to=%27").status_code, 200)

    def test_text_is_escaped(self):
        ProjectWork.objects.create(project=self.p, work_date=self.days(-1), worker_name="<b>x</b>", activity="<script>1</script>", wage=1)
        r = self.client.get(reverse("hrd_project_detail", args=[self.p.pk])); self.assertNotContains(r, "<script>1</script>"); self.assertNotContains(r, "<b>x</b>")


class CateringTests(HrdBase):
    def setUp(self): self.login()

    def new(self, **kw):
        d = {"date": self.today().isoformat(), "meal": "1200", "qty_large": 50, "qty_small": 10, "received_large": "", "received_small": "", "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_catering_new"), d)

    def test_only_four_meal_times_and_fields_are_minimal(self):
        self.assertEqual([m[0] for m in CateringOrder.MEALS], ["0900", "1200", "1800", "0200"])
        names = {f.name for f in CateringOrder._meta.get_fields()}
        self.assertFalse(names & {"department", "status", "vendor", "price_large", "price_small"})
        for meal in ("0900", "1200", "1800", "0200"): self.assertRedirects(self.new(meal=meal), reverse("hrd_catering"))
        self.assertEqual(CateringOrder.objects.count(), 4)
        for bad in ("sarapan", "snack", "1500"): self.assertEqual(self.new(meal=bad, date=self.days(-5).isoformat()).status_code, 200)

    def test_needs_at_least_one_tepak_and_no_negative(self):
        self.assertContains(self.new(qty_large=0, qty_small=0), "minimal satu tepak")
        self.assertEqual(self.new(qty_large=-1).status_code, 200); self.assertEqual(CateringOrder.objects.count(), 0)

    def test_one_row_per_date_and_meal(self):
        self.new(); self.assertContains(self.new(), "sudah ada"); self.assertEqual(CateringOrder.objects.count(), 1)

    def test_received_both_or_none_and_edit_records_actuals(self):
        self.new(); o = CateringOrder.objects.get()
        self.assertFalse(o.received)
        base = {"date": o.date.isoformat(), "meal": "1200", "qty_large": 50, "qty_small": 10, "note": ""}
        self.assertContains(self.client.post(reverse("hrd_catering_edit", args=[o.pk]), {**base, "received_large": 48, "received_small": ""}), "KEDUA ukuran")
        self.client.post(reverse("hrd_catering_edit", args=[o.pk]), {**base, "received_large": 48, "received_small": 0})
        o.refresh_from_db(); self.assertEqual((o.received_large, o.received_small, o.received), (48, 0, True))
        au = self.last_audit("catering_update"); self.assertEqual(au.after["received_large"], "48")

    def test_delete_post_only_audited(self):
        self.new(); o = CateringOrder.objects.get()
        self.assertEqual(self.client.get(reverse("hrd_catering_delete", args=[o.pk])).status_code, 405)
        self.client.post(reverse("hrd_catering_delete", args=[o.pk])); self.assertEqual(CateringOrder.objects.count(), 0)
        self.assertEqual(self.last_audit("catering_delete").before["meal"], "1200")

    def test_summary_pending_and_filters(self):
        CateringOrder.objects.create(date=self.today(), meal="0900", qty_large=10, qty_small=5, received_large=9, received_small=5)
        CateringOrder.objects.create(date=self.today(), meal="1800", qty_large=20, qty_small=0)
        r = self.client.get(reverse("hrd_catering") + f"?from={self.today().isoformat()}&to={self.today().isoformat()}")
        self.assertEqual(r.context["t"], {"ol": 30, "os": 5, "rl": 9, "rs": 5}); self.assertEqual(r.context["pending"], 1)
        r = self.client.get(reverse("hrd_catering") + f"?meal=1800&from={self.today().isoformat()}&to={self.today().isoformat()}"); self.assertEqual(r.context["t"]["ol"], 20)
        self.assertEqual(self.client.get(reverse("hrd_catering") + "?from=zz&meal=%27").status_code, 200)

    def test_note_is_escaped(self):
        CateringOrder.objects.create(date=self.today(), meal="0200", qty_large=1, note="<script>1</script>")
        self.assertNotContains(self.client.get(reverse("hrd_catering")), "<script>1</script>")
