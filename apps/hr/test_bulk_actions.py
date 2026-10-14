"""Aksi massal di daftar karyawan: checkbox + pilih semua → hapus (soft delete) / ubah status. Hanya HRD/Superadmin; pratinjau + konfirmasi; yang tidak memenuhi syarat dilewati."""
from datetime import date
from unittest import mock
from cryptography.fernet import Fernet
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from apps.core.models import AuditLog, Role, User
from .emp_views import _parse_ids
from .models import ChangeRequest, Department, Employee, EmployeeHistory

PW = "Sandi-Uji-12345"


@override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode())
class BulkActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d = Department.objects.create(code="PRD", name="Produksi")
        cls.emps = [Employee.objects.create(nik=f"N{i:03d}", name=f"Karyawan {i:03d}", gender="L", department=cls.d, join_date=date(2024, 1, 1)) for i in range(1, 6)]
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.adm = User.objects.create_user("adm", password=PW, role=Role.DEPT_ADMIN, department=cls.d)
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI)

    def login(self, name): self.client.force_login(User.objects.get(username=name))

    def ids(self, *emps): return ",".join(str(e.pk) for e in emps)

    def post(self, **data): return self.client.post("/employees/bulk/", data)

    def msgs(self, r): return [str(m) for m in get_messages(r.wsgi_request)]

    # ---- izin akses ----
    def test_only_hrd_and_superadmin(self):
        for name in ("adm", "poli"):
            self.login(name)
            self.assertEqual(self.post(action="delete", ids=self.ids(self.emps[0]), reason="x", confirm="1").status_code, 403)
            self.assertEqual(self.client.get("/api/employees/ids/").status_code, 403)
        self.assertEqual(Employee.objects.count(), 5)
        self.client.logout()
        self.assertIn(self.post(action="delete", ids=self.ids(self.emps[0]), reason="x", confirm="1").status_code, (302, 401))
        self.assertEqual(Employee.objects.count(), 5)
        for name in ("hrd", "su"):
            self.login(name); self.assertEqual(self.post(action="delete", ids=self.ids(self.emps[0])).status_code, 200)

    def test_get_redirects_to_list(self):
        self.login("hrd"); self.assertRedirects(self.client.get("/employees/bulk/"), "/employees/", fetch_redirect_response=False)

    def test_checkboxes_only_rendered_for_hrd(self):
        self.login("hrd"); r = self.client.get("/employees/"); self.assertContains(r, 'class="bulkbar"'); self.assertContains(r, "selall"); self.assertContains(r, "const HRD=true")
        for name in ("adm", "poli"):
            self.login(name); r = self.client.get("/employees/"); self.assertNotContains(r, 'class="bulkbar"'); self.assertContains(r, "const HRD=false")

    # ---- endpoint "pilih semua hasil filter" ----
    def test_ids_endpoint_follows_filters(self):
        Employee.objects.filter(pk__in=[self.emps[0].pk, self.emps[1].pk]).update(status="nonaktif")
        self.login("hrd")
        d = self.client.get("/api/employees/ids/").json()
        self.assertEqual((d["count"], sorted(d["ids"]), d["truncated"]), (5, sorted(e.pk for e in self.emps), False))
        d = self.client.get("/api/employees/ids/", {"status": "nonaktif"}).json()
        self.assertEqual(sorted(d["ids"]), sorted([self.emps[0].pk, self.emps[1].pk]))
        self.assertEqual(self.client.get("/api/employees/ids/", {"q": "Karyawan 004"}).json()["ids"], [self.emps[3].pk])

    def test_ids_endpoint_excludes_deleted_and_flags_truncation(self):
        self.emps[4].soft_delete(self.hrd, "uji"); self.login("hrd")
        with mock.patch("apps.hr.views.BULK_MAX", 2):
            d = self.client.get("/api/employees/ids/").json()
        self.assertEqual((d["count"], len(d["ids"]), d["truncated"], d["limit"]), (4, 2, True, 2))

    # ---- parsing ----
    def test_parse_ids(self):
        self.assertEqual(_parse_ids("3,1,3, x ,²,-4,,٣,07"), [3, 1, 7]); self.assertEqual(_parse_ids(""), []); self.assertEqual(_parse_ids(None), [])

    # ---- hapus ----
    def test_delete_preview_changes_nothing(self):
        self.login("hrd"); r = self.post(action="delete", ids=self.ids(*self.emps[:3]))
        self.assertContains(r, "Hapus karyawan: 3 karyawan"); self.assertContains(r, "Karyawan 002"); self.assertEqual(Employee.objects.count(), 5)

    def test_delete_requires_reason(self):
        self.login("hrd"); r = self.post(action="delete", ids=self.ids(*self.emps[:2]), confirm="1", reason="   ")
        self.assertContains(r, "Alasan wajib diisi."); self.assertEqual(Employee.objects.count(), 5)

    def test_delete_soft_deletes_selected_with_audit(self):
        self.login("hrd"); r = self.post(action="delete", ids=self.ids(*self.emps[:3]), confirm="1", reason="Efisiensi")
        self.assertRedirects(r, "/employees/", fetch_redirect_response=False)
        self.assertEqual(sorted(Employee.objects.values_list("nik", flat=True)), ["N004", "N005"])
        e = Employee.all_objects.get(nik="N001"); self.assertIsNotNone(e.deleted_at); self.assertEqual((e.delete_reason, e.deleted_by), ("Efisiensi", self.hrd))
        self.assertEqual(AuditLog.objects.filter(action="employee_delete", after__bulk=True).count(), 3)
        s = AuditLog.objects.get(action="employee_bulk"); self.assertEqual((s.after["requested"], s.after["done"], s.after["skipped"]), (3, 3, 0))
        self.assertIn("3 karyawan dihapus", " ".join(self.msgs(r)))

    def test_delete_skips_employees_with_active_requests(self):
        busy, free = self.emps[0], self.emps[1]
        ChangeRequest.objects.create(type="cuti", employee=busy, department=self.d, status="pending", requested_by=self.adm)
        ChangeRequest.objects.create(type="cuti", employee=free, department=self.d, status="executed", requested_by=self.adm)  # selesai: tidak menghalangi
        self.login("hrd")
        self.assertContains(self.post(action="delete", ids=self.ids(busy, free)), "dilewati: ada pengajuan berjalan")
        r = self.post(action="delete", ids=self.ids(busy, free), confirm="1", reason="Uji")
        self.assertTrue(Employee.objects.filter(pk=busy.pk).exists()); self.assertFalse(Employee.objects.filter(pk=free.pk).exists())
        m = " ".join(self.msgs(r)); self.assertIn("1 karyawan dihapus", m); self.assertIn("1 dilewati", m); self.assertIn("N001", m)

    def test_delete_nothing_eligible_reports_warning(self):
        ChangeRequest.objects.create(type="cuti", employee=self.emps[0], department=self.d, status="approved", requested_by=self.adm)
        self.login("hrd"); r = self.post(action="delete", ids=self.ids(self.emps[0]), confirm="1", reason="Uji")
        self.assertEqual(Employee.objects.count(), 5); self.assertIn("Tidak ada karyawan yang diubah.", self.msgs(r))

    def test_delete_ignores_unknown_and_already_deleted_ids(self):
        self.emps[1].soft_delete(self.hrd, "dulu"); self.login("hrd")
        r = self.post(action="delete", ids=f"{self.emps[0].pk},{self.emps[1].pk},999999", confirm="1", reason="Uji")
        m = " ".join(self.msgs(r)); self.assertIn("1 karyawan dihapus", m); self.assertIn("2 karyawan terpilih sudah tidak ada", m)
        self.assertEqual(Employee.all_objects.get(pk=self.emps[1].pk).delete_reason, "dulu")  # alasan lama tidak ditimpa

    # ---- ubah status ----
    def test_status_change_with_history_and_audit(self):
        self.login("hrd")
        self.assertContains(self.post(action="status", ids=self.ids(*self.emps[:2])), "Status baru")
        r = self.post(action="status", ids=self.ids(*self.emps[:2]), confirm="1", status="nonaktif", effective_date="2026-11-01")
        self.assertRedirects(r, "/employees/", fetch_redirect_response=False)
        self.assertEqual(Employee.objects.filter(status="nonaktif").count(), 2)
        h = EmployeeHistory.objects.get(employee=self.emps[0], field="status")
        self.assertEqual((h.old_value, h.new_value, h.effective_date, h.changed_by), ("aktif", "nonaktif", date(2026, 11, 1), self.hrd))
        self.assertEqual(AuditLog.objects.filter(action="employee_update", after__bulk=True).count(), 2)
        self.assertIn("Status 2 karyawan diubah menjadi nonaktif", " ".join(self.msgs(r)))

    def test_status_skips_those_already_in_target_status(self):
        Employee.objects.filter(pk=self.emps[0].pk).update(status="nonaktif"); self.login("hrd")
        r = self.post(action="status", ids=self.ids(*self.emps[:2]), confirm="1", status="nonaktif")
        self.assertEqual(EmployeeHistory.objects.filter(field="status").count(), 1)
        m = " ".join(self.msgs(r)); self.assertIn("Status 1 karyawan diubah", m); self.assertIn("1 dilewati karena sudah berstatus nonaktif", m)

    def test_status_validation(self):
        self.login("hrd")
        self.assertContains(self.post(action="status", ids=self.ids(self.emps[0]), confirm="1", status="pensiun"), "Pilih status tujuan.")
        self.assertContains(self.post(action="status", ids=self.ids(self.emps[0]), confirm="1", status="nonaktif", effective_date="31-02-2026"), "Tanggal efektif tidak valid.")
        self.assertEqual(Employee.objects.filter(status="nonaktif").count(), 0); self.assertEqual(EmployeeHistory.objects.count(), 0)

    def test_status_change_allowed_even_with_active_request(self):  # sama seperti ubah status satuan di form karyawan
        ChangeRequest.objects.create(type="cuti", employee=self.emps[0], department=self.d, status="pending", requested_by=self.adm)
        self.login("hrd"); self.post(action="status", ids=self.ids(self.emps[0]), confirm="1", status="nonaktif")
        self.assertEqual(Employee.objects.get(pk=self.emps[0].pk).status, "nonaktif")

    # ---- masukan buruk ----
    def test_bad_input_is_rejected_without_changes(self):
        self.login("hrd")
        for data in ({"action": "hack", "ids": "1"}, {"action": "delete", "ids": ""}, {"action": "delete", "ids": "abc,,"}, {"action": "delete"}):
            r = self.post(confirm="1", reason="x", **data); self.assertRedirects(r, "/employees/", fetch_redirect_response=False)
        self.assertEqual(Employee.objects.count(), 5); self.assertEqual(AuditLog.objects.filter(action__startswith="employee_").count(), 0)

    def test_limit_per_action(self):
        self.login("hrd")
        with mock.patch("apps.hr.emp_views.BULK_MAX", 2):
            r = self.post(action="delete", ids=self.ids(*self.emps[:3]), confirm="1", reason="x")
        self.assertRedirects(r, "/employees/", fetch_redirect_response=False); self.assertIn("maksimal 2", " ".join(self.msgs(r))); self.assertEqual(Employee.objects.count(), 5)

    # ---- skala (±3.000 karyawan) ----
    def test_large_selection_spans_multiple_query_chunks(self):
        Employee.objects.bulk_create([Employee(nik=f"B{i:05d}", name=f"Massal {i:05d}", gender="P", department=self.d, join_date=date(2024, 1, 1)) for i in range(1200)])
        self.login("hrd"); d = self.client.get("/api/employees/ids/", {"q": "Massal"}).json(); self.assertEqual((d["count"], len(d["ids"])), (1200, 1200))
        r = self.post(action="delete", ids=",".join(map(str, d["ids"])), confirm="1", reason="Uji skala")
        self.assertIn("1200 karyawan dihapus", " ".join(self.msgs(r))); self.assertEqual(Employee.objects.count(), 5)
        self.assertEqual(Employee.all_objects.filter(delete_reason="Uji skala").count(), 1200)
