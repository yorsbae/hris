from datetime import date
from django.test import TestCase
from apps.core.models import AuditLog, Notification, Role, User
from .models import ChangeRequest, Department, Employee, EmployeeHistory, Position, Shift


class WorkflowUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.pos = Position.objects.create(name="Staff"); cls.pos2 = Position.objects.create(name="Leader")
        mk = lambda nik, d: Employee.objects.create(nik=nik, name=f"Emp {nik}", gender="L", department=d, position=cls.pos, join_date=date(2024, 1, 1))
        cls.e1, cls.e2 = mk("001", cls.d1), mk("002", cls.d2)
        pw = "kata-sandi-panjang-123"
        cls.hrd = User.objects.create_user("hrd", password=pw, role=Role.HRD)
        cls.adm1 = User.objects.create_user("adm1", password=pw, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.adm2 = User.objects.create_user("adm2", password=pw, role=Role.DEPT_ADMIN, department=cls.d2)
        cls.poli = User.objects.create_user("poli", password=pw, role=Role.POLI)

    def login(self, name): self.client.force_login(User.objects.get(username=name))

    def post_leave(self, nik="001", start="2026-11-02", end="2026-11-04", submit=True):
        data = {"type": "cuti", "nik": nik, "start_date": start, "end_date": end, "reason": "Keperluan keluarga"}
        if submit: data["submit"] = "1"
        return self.client.post("/requests/new/", data)

    def test_dept_admin_submits_leave_and_hrd_notified(self):
        self.login("adm1"); r = self.post_leave()
        req = ChangeRequest.objects.get(); self.assertRedirects(r, f"/requests/{req.pk}/")
        self.assertEqual((req.status, req.department_id, req.requested_by_id), ("pending", self.d1.pk, self.adm1.pk))
        self.assertTrue(Notification.objects.filter(user=self.hrd, kind="request").exists())
        self.assertTrue(AuditLog.objects.filter(action="request_submit").exists())

    def test_draft_then_submit_from_detail(self):
        self.login("adm1"); self.post_leave(submit=False)
        req = ChangeRequest.objects.get(); self.assertEqual(req.status, "draft")
        self.client.post(f"/requests/{req.pk}/submit/"); req.refresh_from_db(); self.assertEqual(req.status, "pending")

    def test_dept_admin_cannot_use_other_department_employee(self):
        self.login("adm1"); r = self.post_leave(nik="002")
        self.assertEqual(r.status_code, 200); self.assertFalse(ChangeRequest.objects.exists())
        self.assertContains(r, "tidak ditemukan")

    def test_scope_url_tampering_is_404(self):
        self.login("adm1"); self.post_leave(); req = ChangeRequest.objects.get()
        self.login("adm2")
        self.assertEqual(self.client.get(f"/requests/{req.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/requests/{req.pk}/submit/").status_code, 404)
        self.assertNotContains(self.client.get("/requests/"), "Emp 001")

    def test_poli_forbidden_and_anonymous_blocked(self):
        self.login("poli"); self.assertEqual(self.client.get("/requests/").status_code, 403)
        self.client.logout(); self.assertIn(self.client.get("/requests/").status_code, (302, 401))

    def test_dept_admin_cannot_decide_or_change_status(self):
        self.login("adm1"); self.post_leave(); req = ChangeRequest.objects.get()
        self.client.post(f"/requests/{req.pk}/approved/"); req.refresh_from_db(); self.assertEqual(req.status, "pending")
        r = self.client.post("/requests/new/", {"type": "status", "nik": "001", "status": "nonaktif", "effective_date": "2026-11-01", "reason": "x"})
        self.assertEqual(r.status_code, 200)  # tipe 'status' tidak ada di pilihan Admin Departemen
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_overlapping_leave_rejected(self):
        self.login("adm1"); self.post_leave(); self.post_leave(start="2026-11-03", end="2026-11-05")
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_end_before_start_rejected(self):
        self.login("adm1"); self.post_leave(start="2026-11-05", end="2026-11-01"); self.assertFalse(ChangeRequest.objects.exists())

    def test_reject_requires_note_then_approve_execute_mutation(self):
        self.login("adm1")
        self.client.post("/requests/new/", {"type": "mutasi_dept", "nik": "001", "department": self.d2.pk, "effective_date": "2026-11-01", "reason": "Kebutuhan", "submit": "1"})
        req = ChangeRequest.objects.get(); self.assertEqual(req.status, "pending")
        self.login("hrd")
        self.client.post(f"/requests/{req.pk}/rejected/", {"note": ""}); req.refresh_from_db(); self.assertEqual(req.status, "pending")
        self.client.post(f"/requests/{req.pk}/approved/", {"note": "OK"})
        self.client.post(f"/requests/{req.pk}/executed/")
        req.refresh_from_db(); self.e1.refresh_from_db()
        self.assertEqual((req.status, self.e1.department_id), ("executed", self.d2.pk))
        h = EmployeeHistory.objects.get(); self.assertEqual((h.field, h.old_value, h.new_value), ("department", "Produksi", "Gudang"))
        self.assertTrue(Notification.objects.filter(user=self.adm1, kind="approval").exists())

    def test_invalid_transition_and_unknown_action(self):
        self.login("hrd"); self.post_leave()
        req = ChangeRequest.objects.get()
        self.client.post(f"/requests/{req.pk}/executed/"); req.refresh_from_db(); self.assertEqual(req.status, "pending")
        self.assertEqual(self.client.post(f"/requests/{req.pk}/hapus/").status_code, 302)

    def test_pages_render(self):
        self.login("hrd")
        self.assertContains(self.client.get("/requests/new/"), "Buat pengajuan")
        self.post_leave(); req = ChangeRequest.objects.get()
        self.assertContains(self.client.get(f"/requests/{req.pk}/"), "Setujui")
        self.assertContains(self.client.get("/requests/?status=pending"), "Emp 001")
