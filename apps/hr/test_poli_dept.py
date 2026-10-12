"""Putaran 48: akun Poli = admin departemen Poli. Poli mengajukan (cuti/izin, lembur/stand by, jadwal) HANYA untuk karyawan departemen Poli;
departemen lain "tidak ditemukan"/404; Perubahan Status tetap HRD saja; HRD memutuskan seperti biasa; menu "Pengajuan Departemen" bersubmenu."""
from datetime import date
from django.test import TestCase
from django.utils import timezone
from apps.core.models import Role, User
from .models import ChangeRequest, Department, Employee, LeaveLedger, Position


class PoliDeptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pol, cls.prd = Department.objects.create(code="POL", name="Poliklinik"), Department.objects.create(code="PRD", name="Produksi")
        pos = Position.objects.create(name="Staff")
        mk = lambda n, d: Employee.objects.create(nik=n, name=f"Emp {n}", gender="P", department=d, position=pos, join_date=date(2024, 1, 1))
        cls.p1, cls.p2, cls.x = mk("P01", cls.pol), mk("P02", cls.pol), mk("X01", cls.prd)
        for e in (cls.p1, cls.p2, cls.x): LeaveLedger.objects.create(employee=e, year=2026, kind="grant", days=12)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.poli = User.objects.create_user("poli", password="x", role=Role.POLI, department=cls.pol)
        cls.adm = User.objects.create_user("admprd", password="x", role=Role.DEPT_ADMIN, department=cls.prd)
        cls.today = timezone.localdate()

    def leave(self, nik, submit=True):
        d = {"type": "cuti", "nik": nik, "start_date": "2026-11-02", "end_date": "2026-11-03", "reason": "Keperluan keluarga"}
        if submit: d["submit"] = "1"
        return self.client.post("/requests/new/", d)

    def test_poli_submits_leave_for_own_department_and_hrd_decides(self):
        self.client.force_login(self.poli); self.leave("P01")
        r = ChangeRequest.objects.get(); self.assertEqual((r.status, r.department_id, r.requested_by_id), ("pending", self.pol.pk, self.poli.pk))
        self.client.force_login(self.hrd); self.client.post(f"/requests/{r.pk}/approved/"); r.refresh_from_db(); self.assertEqual(r.status, "approved")

    def test_poli_cannot_submit_for_other_department(self):
        self.client.force_login(self.poli); resp = self.leave("X01")
        self.assertEqual(resp.status_code, 200); self.assertContains(resp, "tidak ditemukan"); self.assertFalse(ChangeRequest.objects.exists())

    def test_poli_sees_only_poli_requests_and_cannot_open_others(self):
        other = ChangeRequest.objects.create(type="cuti", employee=self.x, department=self.prd, payload={"start_date": "2026-11-02", "end_date": "2026-11-03"}, requested_by=self.adm, status="pending")
        self.client.force_login(self.poli); self.leave("P01")
        h = self.client.get("/requests/").content.decode(); self.assertIn("Emp P01", h); self.assertNotIn("Emp X01", h)
        self.assertEqual(self.client.get(f"/requests/{other.pk}/").status_code, 404); self.assertEqual(self.client.post(f"/requests/{other.pk}/cancelled/", {"note": "x"}).status_code, 404)
        other.refresh_from_db(); self.assertEqual(other.status, "pending")

    def test_poli_cannot_decide_or_change_status(self):
        self.client.force_login(self.poli); self.leave("P01"); r = ChangeRequest.objects.get()
        self.client.post(f"/requests/{r.pk}/approved/"); r.refresh_from_db(); self.assertEqual(r.status, "pending")
        self.assertEqual(self.client.post("/requests/new/", {"type": "status", "nik": "P01", "status": "nonaktif", "effective_date": "2026-11-01", "reason": "x"}).status_code, 200)
        self.assertEqual(self.client.get("/requests/g/status/").status_code, 404)
        self.assertEqual(ChangeRequest.objects.filter(type="status").count(), 0)

    def test_poli_overtime_own_department_only_and_hrd_approves_batch(self):
        self.client.force_login(self.poli)
        d = {"kind": "lembur", "date": self.today.isoformat(), "start": "16:00", "end": "18:00", "reason": "Jaga IGD", "niks": ["P01", "P02"]}
        self.assertEqual(self.client.post("/requests/g/lembur/new/", d).status_code, 302); self.assertEqual(ChangeRequest.objects.filter(type="lembur", department=self.pol).count(), 2)
        self.assertEqual(self.client.post("/requests/g/lembur/new/", {**d, "niks": ["P01", "X01"]}).status_code, 200); self.assertEqual(ChangeRequest.objects.count(), 2)
        batch = ChangeRequest.objects.first().payload["batch"]
        self.assertEqual(self.client.post(f"/requests/batch/{batch}/approved/").status_code, 403)  # Poli tidak memutuskan
        self.client.force_login(self.hrd); self.client.post(f"/requests/batch/{batch}/approved/")
        self.assertEqual(ChangeRequest.objects.filter(status="executed").count(), 2)

    def test_poli_without_department_fails_closed(self):
        nodept = User.objects.create_user("poli2", password="x", role=Role.POLI)
        self.client.force_login(nodept); self.leave("P01"); self.assertFalse(ChangeRequest.objects.exists())

    def test_nav_has_department_menu_with_submenus_for_poli_only_poli_dept(self):
        self.client.force_login(self.poli); h = self.client.get("/").content.decode()
        for u in ("/requests/", "/requests/g/izin/", "/requests/g/lembur/", "/requests/g/jadwal/", "/validasi/"): self.assertIn(f'href="{u}"', h, u)
        self.assertIn("Pengajuan Departemen", h); self.assertNotIn('href="/hrd/"', h)

    def test_poli_account_counts_as_department_admin_for_warning_and_validation(self):
        from apps.core.user_services import departments_without_admin
        self.assertNotIn(self.pol, departments_without_admin())  # departemen Poli dianggap punya admin (akun Poli)
        self.adm.is_active = False; self.adm.save(); self.assertIn(self.prd, departments_without_admin())


class SinglePoliAccountTests(TestCase):
    """Putaran 49: hanya SATU akun Poli aktif (mewakili 3 perawat); dashboard Poli memuat tugas departemennya."""
    @classmethod
    def setUpTestData(cls):
        cls.pol = Department.objects.create(code="POL", name="Poliklinik")
        cls.su = User.objects.create_user("su", password="kata-sandi-panjang-123", role=Role.SUPERADMIN)
        cls.poli = User.objects.create_user("poli", password="kata-sandi-panjang-123", role=Role.POLI, department=cls.pol)

    def payload(self, **kw):
        return {"username": "poli2", "first_name": "Dua", "last_name": "", "email": "", "role": Role.POLI, "department": self.pol.pk,
                "password1": "kata-sandi-panjang-456", "password2": "kata-sandi-panjang-456", **kw}

    def test_second_active_poli_rejected_and_allowed_after_deactivation(self):
        self.client.force_login(self.su)
        self.assertContains(self.client.post("/users/new/", self.payload()), "satu akun Poli aktif"); self.assertFalse(User.objects.filter(username="poli2").exists())
        self.poli.is_active = False; self.poli.save(); self.client.post("/users/new/", self.payload()); self.assertTrue(User.objects.filter(username="poli2", role=Role.POLI).exists())

    def test_reactivating_old_poli_blocked_while_another_active(self):
        other = User.objects.create_user("poli_baru", password="x", role=Role.POLI, department=self.pol)
        self.poli.is_active = False; self.poli.save(); self.client.force_login(self.su)
        self.client.post(f"/users/{self.poli.pk}/activate/"); self.poli.refresh_from_db(); self.assertFalse(self.poli.is_active)

    def test_dashboard_api_has_department_tasks_for_poli(self):
        e = Employee.objects.create(nik="P01", name="Perawat 1", gender="P", department=self.pol, position=Position.objects.create(name="Perawat"), join_date=date(2024, 1, 1))
        ChangeRequest.objects.create(type="izin", employee=e, department=self.pol, payload={"start_date": "2026-11-02"}, requested_by=self.poli, status="pending")
        self.client.force_login(self.poli); j = self.client.get("/api/dashboard/").json()
        self.assertEqual((j["dept_req_pending"], j["attcheck_todo"]), (1, 0))
