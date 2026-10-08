from django.urls import reverse
from apps.core.models import Role
from .models import Aid, CateringOrder, MaternityLeave, Project, ProjectWork, WarningLetter
from .test_base import HrdBase


class AccessTests(HrdBase):
    """Operasional HRD hanya untuk HRD & Superadmin. Admin Dept & Poli ditolak di SEMUA halaman, termasuk aksi POST."""

    def setUp(self):
        self.aid = Aid.objects.create(employee=self.e1, kind="kematian", event_date=self.today(), amount=1000)
        self.mat = MaternityLeave.objects.create(employee=self.e2, due_date=self.days(30), start_date=self.days(-15), end_date=self.days(75))
        self.prj = Project.objects.create(code="P1", name="Gudang baru", start_date=self.days(-30))
        self.plog = ProjectWork.objects.create(project=self.prj, work_date=self.days(-1), worker_name="Slamet", activity="Cor lantai", wage=100000)
        self.sp = WarningLetter.objects.create(employee=self.e1, number="0001/SP-1/HRD/X/2026", level=1, issue_date=self.today(), valid_until=self.days(100), violation="Terlambat")
        self.cat = CateringOrder.objects.create(date=self.today(), meal="siang", qty_large=5)

    def gets(self):
        return [reverse("hrd_hub"), reverse("hrd_bpjs"), reverse("hrd_bpjs_detail", args=[self.e1.pk]), reverse("hrd_aids"), reverse("hrd_aid_new"),
                reverse("hrd_aid_edit", args=[self.aid.pk]), reverse("hrd_maternity"), reverse("hrd_maternity_new"),
                reverse("hrd_maternity_detail", args=[self.mat.pk]), reverse("hrd_maternity_edit", args=[self.mat.pk]), reverse("hrd_projects"),
                reverse("hrd_project_new"), reverse("hrd_project_detail", args=[self.prj.pk]), reverse("hrd_project_edit", args=[self.prj.pk]),
                reverse("hrd_project_work_new", args=[self.prj.pk]), reverse("hrd_project_work_edit", args=[self.prj.pk, self.plog.pk]),
                reverse("hrd_warnings"), reverse("hrd_warning_new"), reverse("hrd_warning_detail", args=[self.sp.pk]), reverse("hrd_warning_pdf", args=[self.sp.pk]),
                reverse("hrd_catering"), reverse("hrd_catering_new"), reverse("hrd_catering_edit", args=[self.cat.pk])]

    def posts(self):
        return [reverse("hrd_aid_delete", args=[self.aid.pk]), reverse("hrd_maternity_action", args=[self.mat.pk, "cancel"]), reverse("hrd_catering_delete", args=[self.cat.pk]),
                reverse("hrd_project_work_delete", args=[self.prj.pk, self.plog.pk]), reverse("hrd_warning_revoke", args=[self.sp.pk])]

    def test_anonymous_redirected_to_login(self):
        for u in self.gets(): self.assertRedirects(self.client.get(u), f"/login/?next={u}", msg_prefix=u)

    def test_dept_admin_and_poli_forbidden_everywhere(self):
        for who in ("adm", "poli"):
            self.login(who)
            for u in self.gets(): self.assertEqual(self.client.get(u).status_code, 403, f"{who} {u}")
            for u in self.posts(): self.assertEqual(self.client.post(u, {"reason": "x"}).status_code, 403, f"{who} {u}")
        self.assertTrue(Aid.objects.filter(pk=self.aid.pk).exists() and CateringOrder.objects.filter(pk=self.cat.pk).exists())  # tidak terhapus
        self.sp.refresh_from_db(); self.assertIsNone(self.sp.revoked_at)

    def test_hrd_and_superadmin_can_open_all(self):
        for who in ("hrd", "su"):
            self.login(who)
            for u in self.gets(): self.assertEqual(self.client.get(u).status_code, 200, f"{who} {u}")

    def test_action_urls_are_post_only(self):
        self.login()
        for u in self.posts(): self.assertEqual(self.client.get(u).status_code, 405, u)

    def test_unknown_action_404(self):
        self.login()
        self.assertEqual(self.client.post(reverse("hrd_maternity_action", args=[self.mat.pk, "x"])).status_code, 404)

    def test_missing_objects_404(self):
        self.login()
        for u in ("/hrd/aids/999/edit/", "/hrd/maternity/999/", "/hrd/projects/999/", "/hrd/bpjs/999/", "/hrd/catering/999/edit/", "/hrd/warnings/999/"):
            self.assertEqual(self.client.get(u).status_code, 404, u)

    def test_hub_and_nav(self):
        self.login()
        r = self.client.get("/hrd/")
        self.assertContains(r, "/hrd/bpjs/"); self.assertContains(r, "/hrd/catering/")
        self.assertContains(self.client.get("/"), 'href="/hrd/"')
        self.login("adm"); self.assertNotContains(self.client.get("/"), 'href="/hrd/"')

    def test_dashboard_keys_hrd_only(self):
        self.login(); d = self.client.get("/api/dashboard/").json()
        for k in ("maternity_active", "warnings_active", "bpjs_inactive"): self.assertIn(k, d)
        self.assertEqual(d["warnings_active"], 1); self.assertEqual(d["maternity_active"], 1)
        self.login("adm"); self.assertNotIn("warnings_active", self.client.get("/api/dashboard/").json())
