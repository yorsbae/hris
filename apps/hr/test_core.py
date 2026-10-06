import json
from datetime import date, time
from io import StringIO
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from apps.core.crypto import decrypt, encrypt, is_encrypted
from apps.core.models import AuditLog, Role, User
from .models import ChangeRequest, Contract, Department, Employee, EmployeeHistory, Position, Shift

KEY = Fernet.generate_key().decode()
KTP = "3326010101010001"


def raw(emp_id, col):
    with connection.cursor() as c:
        c.execute(f"SELECT {col} FROM hr_employee WHERE id = %s", [emp_id]); return c.fetchone()[0]


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class CoreBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.p1, cls.p2 = Position.objects.create(name="Staff"), Position.objects.create(name="Leader")
        cls.s1 = Shift.objects.create(name="Pagi", start=time(7), end=time(15))
        cls.e1 = Employee.objects.create(nik="001", name="Budi", gender="L", department=cls.d1, position=cls.p1, join_date=date(2024, 1, 1),
                                         nik_ktp=KTP, npwp="12.345.678.9-012.000", bank_account="1234567890", phone="0812")
        cls.e2 = Employee.objects.create(nik="002", name="Sari", gender="P", department=cls.d2, position=cls.p1, join_date=date(2024, 1, 1))
        pw = "kata-sandi-panjang-123"
        cls.hrd = User.objects.create_user("hrd", password=pw, role=Role.HRD)
        cls.adm1 = User.objects.create_user("adm1", password=pw, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.adm2 = User.objects.create_user("adm2", password=pw, role=Role.DEPT_ADMIN, department=cls.d2)
        cls.poli = User.objects.create_user("poli", password=pw, role=Role.POLI)
        cls.su = User.objects.create_user("su", password=pw, role=Role.SUPERADMIN)

    def login(self, name): self.client.force_login(User.objects.get(username=name))

    def emp_data(self, **kw):
        d = {"nik": "100", "name": "Baru", "gender": "L", "join_date": "2025-01-01", "department": self.d1.pk, "position": self.p1.pk,
             "shift": "", "status": "aktif", "marital_status": "", "education": "", "address": "", "phone": "", "nik_ktp": "", "bpjs_kes": "",
             "bpjs_tk": "", "npwp": "", "bank_name": "", "bank_account": "", "supervisor_nik": ""}
        d.update(kw); return d


class EncryptionTests(CoreBase):
    def test_encrypted_in_db_decrypted_in_orm(self):
        for col, val in (("nik_ktp", KTP), ("npwp", "12.345.678.9-012.000"), ("bank_account", "1234567890")):
            r = raw(self.e1.pk, col)
            self.assertTrue(is_encrypted(r)); self.assertNotIn(val, r)
        e = Employee.objects.get(pk=self.e1.pk)
        self.assertEqual((e.nik_ktp, e.bank_account), (KTP, "1234567890"))
        self.assertEqual(raw(self.e1.pk, "phone"), "0812")  # kolom non-sensitif tidak dienkripsi

    def test_blank_not_encrypted_and_no_double_encrypt(self):
        self.assertEqual(raw(self.e2.pk, "nik_ktp"), "")
        once = encrypt("x"); self.assertEqual(encrypt(once), once)

    def test_legacy_plaintext_readable_and_command_encrypts_idempotent(self):
        with connection.cursor() as c: c.execute("UPDATE hr_employee SET nik_ktp = %s, bpjs_kes = %s WHERE id = %s", [KTP, "0001", self.e2.pk])
        self.assertEqual(Employee.objects.get(pk=self.e2.pk).nik_ktp, KTP)  # tetap terbaca
        out = StringIO(); call_command("encrypt_sensitive", "--dry-run", stdout=out)
        self.assertIn("Akan dienkripsi: 1", out.getvalue()); self.assertEqual(raw(self.e2.pk, "nik_ktp"), KTP)
        call_command("encrypt_sensitive", stdout=StringIO())
        self.assertTrue(is_encrypted(raw(self.e2.pk, "nik_ktp"))); self.assertTrue(is_encrypted(raw(self.e2.pk, "bpjs_kes")))
        out = StringIO(); call_command("encrypt_sensitive", stdout=out); self.assertIn("Dienkripsi: 0", out.getvalue())
        self.assertEqual(Employee.objects.get(pk=self.e2.pk).bpjs_kes, "0001")

    def test_missing_key_fails_loudly_and_wrong_key_fails(self):
        token = encrypt("rahasia")
        with override_settings(FIELD_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured): encrypt("rahasia")
        with override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(ImproperlyConfigured): decrypt(token)

    def test_key_rotation(self):
        token = encrypt("rahasia"); new = Fernet.generate_key().decode()
        with override_settings(FIELD_ENCRYPTION_KEY=f"{new},{KEY}"):  # kunci baru menulis, kunci lama tetap bisa membaca
            self.assertEqual(decrypt(token), "rahasia"); self.assertNotEqual(encrypt("a"), token)


class EmployeeCrudTests(CoreBase):
    def test_hrd_creates_and_audit_masks_sensitive(self):
        self.login("hrd")
        r = self.client.post("/employees/new/", self.emp_data(nik_ktp=KTP, bank_account="9988776655"))
        e = Employee.objects.get(nik="100"); self.assertRedirects(r, f"/employees/{e.pk}/")
        self.assertTrue(is_encrypted(raw(e.pk, "nik_ktp")))
        a = AuditLog.objects.get(action="employee_create"); blob = json.dumps(a.after)
        self.assertNotIn(KTP, blob); self.assertNotIn("9988776655", blob); self.assertEqual(a.after["name"], "Baru")

    def test_only_hrd_can_write(self):
        for who in ("adm1", "poli"):
            self.login(who)
            self.assertEqual(self.client.post("/employees/new/", self.emp_data()).status_code, 403)
            self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/edit/").status_code, 403)
            self.assertEqual(self.client.post(f"/employees/{self.e1.pk}/delete/", {"reason": "x"}).status_code, 403)
            self.assertEqual(self.client.get("/master/shift/").status_code, 403)
        self.assertFalse(Employee.objects.filter(nik="100").exists()); self.assertTrue(Employee.objects.filter(pk=self.e1.pk).exists())
        self.client.logout(); self.assertIn(self.client.get("/employees/new/").status_code, (302, 401))

    def test_validation(self):
        self.login("hrd")
        for kw, msg in (({"nik": "001"}, "sudah dipakai"), ({"nik_ktp": "123"}, "16 digit"), ({"supervisor_nik": "999"}, "Atasan tidak ditemukan"),
                        ({"join_date": "2999-01-01"}, "masa depan"), ({"npwp": "abc"}, "NPWP")):
            r = self.client.post("/employees/new/", self.emp_data(**kw)); self.assertEqual(r.status_code, 200); self.assertContains(r, msg)
        self.assertFalse(Employee.objects.filter(nik="100").exists())

    def test_supervisor_resolved_by_nik_and_not_self(self):
        self.login("hrd")
        self.client.post("/employees/new/", self.emp_data(supervisor_nik="001"))
        self.assertEqual(Employee.objects.get(nik="100").supervisor_id, self.e1.pk)
        r = self.client.post(f"/employees/{self.e1.pk}/edit/", self.emp_data(nik="001", name="Budi", supervisor_nik="001", effective_date="2026-10-06"))
        self.assertContains(r, "sama dengan karyawan ini")

    def test_edit_writes_history_not_overwrite(self):
        self.login("hrd")
        d = self.emp_data(nik="001", name="Budi", nik_ktp=KTP, department=self.d2.pk, position=self.p2.pk, effective_date="2026-11-01")
        self.client.post(f"/employees/{self.e1.pk}/edit/", d)
        h = {x.field: x for x in EmployeeHistory.objects.filter(employee=self.e1)}
        self.assertEqual(set(h), {"department", "position"})
        self.assertEqual((h["department"].old_value, h["department"].new_value, h["department"].effective_date), ("Produksi", "Gudang", date(2026, 11, 1)))
        self.assertEqual(h["department"].changed_by, self.hrd)
        a = AuditLog.objects.get(action="employee_update"); self.assertEqual(a.before["department"], "Produksi")

    def test_edit_without_change_makes_no_history_or_audit(self):
        self.login("hrd")
        d = self.emp_data(nik="001", name="Budi", gender="L", join_date="2024-01-01", nik_ktp=KTP, npwp="12.345.678.9-012.000", bank_account="1234567890",
                          phone="0812", effective_date="2026-11-01")
        self.client.post(f"/employees/{self.e1.pk}/edit/", d)
        self.assertFalse(EmployeeHistory.objects.exists()); self.assertFalse(AuditLog.objects.filter(action="employee_update").exists())

    def test_sensitive_change_audited_without_values(self):
        self.login("hrd")
        d = self.emp_data(nik="001", name="Budi", gender="L", join_date="2024-01-01", nik_ktp="3326010101010999", npwp="12.345.678.9-012.000",
                          bank_account="1234567890", phone="0812", effective_date="2026-11-01")
        self.client.post(f"/employees/{self.e1.pk}/edit/", d)
        a = AuditLog.objects.get(action="employee_update"); blob = json.dumps([a.before, a.after])
        self.assertNotIn(KTP, blob); self.assertNotIn("3326010101010999", blob); self.assertEqual(a.after["nik_ktp"], "*** (diubah)")
        self.assertEqual(Employee.objects.get(pk=self.e1.pk).nik_ktp, "3326010101010999")


class DetailVisibilityTests(CoreBase):
    def test_hrd_sees_all_and_access_audited(self):
        self.login("hrd"); r = self.client.get(f"/employees/{self.e1.pk}/")
        for v in (KTP, "1234567890", "12.345.678.9-012.000", "0812"): self.assertContains(r, v)
        self.assertTrue(AuditLog.objects.filter(action="view_sensitive", object_id=str(self.e1.pk)).exists())

    def test_dept_admin_no_sensitive_and_scoped(self):
        self.login("adm1"); r = self.client.get(f"/employees/{self.e1.pk}/")
        self.assertContains(r, "Budi")
        for v in (KTP, "1234567890", "12.345.678.9-012.000", "0812", "Kontrak", "Riwayat"): self.assertNotContains(r, v)
        self.assertEqual(self.client.get(f"/employees/{self.e2.pk}/").status_code, 404)  # URL tampering
        self.assertEqual(self.client.get("/employees/999999/").status_code, 404)

    def test_poli_minimum_identity_only(self):
        self.login("poli"); r = self.client.get(f"/employees/{self.e1.pk}/")
        self.assertContains(r, "Budi"); self.assertContains(r, "Produksi")
        for v in (KTP, "0812", "Tanggal masuk", "Status", "Atasan", "Kontrak"): self.assertNotContains(r, v)

    def test_xss_escaped(self):
        Employee.objects.filter(pk=self.e1.pk).update(name="<script>alert(1)</script>")
        self.login("hrd"); r = self.client.get(f"/employees/{self.e1.pk}/")
        self.assertNotContains(r, "<script>alert(1)</script>"); self.assertContains(r, "&lt;script&gt;")

    def test_list_page_create_button_only_for_hrd(self):
        self.login("hrd"); self.assertContains(self.client.get("/employees/"), "/employees/new/")
        self.login("adm1"); self.assertNotContains(self.client.get("/employees/"), "/employees/new/")


class SoftDeleteTests(CoreBase):
    def test_soft_delete_hides_everywhere_but_keeps_row_and_history(self):
        self.login("hrd")
        EmployeeHistory.objects.create(employee=self.e1, field="status", old_value="a", new_value="b", effective_date=date.today())
        r = self.client.post(f"/employees/{self.e1.pk}/delete/", {"reason": "Salah input"}); self.assertRedirects(r, "/employees/")
        self.assertTrue(Employee.all_objects.filter(pk=self.e1.pk, deleted_at__isnull=False, delete_reason="Salah input", deleted_by=self.hrd).exists())
        self.assertFalse(Employee.objects.filter(pk=self.e1.pk).exists())
        self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/api/employees/").json()["count"], 1)
        self.assertEqual(self.client.get(f"/api/employees/{self.e1.pk}/").status_code, 404)
        self.assertTrue(EmployeeHistory.objects.filter(employee=self.e1).exists())
        self.assertTrue(AuditLog.objects.filter(action="employee_delete").exists())

    def test_reason_required_and_blocked_by_active_request(self):
        self.login("hrd")
        r = self.client.post(f"/employees/{self.e1.pk}/delete/", {"reason": " "}); self.assertContains(r, "Alasan wajib")
        ChangeRequest.objects.create(type="cuti", employee=self.e1, department=self.d1, status="pending", requested_by=self.adm1)
        r = self.client.post(f"/employees/{self.e1.pk}/delete/", {"reason": "x"}); self.assertContains(r, "pengajuan yang berjalan")
        self.assertTrue(Employee.objects.filter(pk=self.e1.pk).exists())

    def test_nik_of_deleted_cannot_be_reused_and_restore_works(self):
        self.e1.soft_delete(self.hrd, "x"); self.login("hrd")
        r = self.client.post("/employees/new/", self.emp_data(nik="001")); self.assertContains(r, "sudah dipakai")
        self.e1.restore(); self.assertTrue(Employee.objects.filter(pk=self.e1.pk).exists())

    def test_deleted_employee_not_selectable_in_requests(self):
        self.e1.soft_delete(self.hrd, "x"); self.login("adm1")
        r = self.client.post("/requests/new/", {"type": "cuti", "nik": "001", "start_date": "2026-11-02", "end_date": "2026-11-03", "reason": "x"})
        self.assertContains(r, "tidak ditemukan")


class ContractTests(CoreBase):
    def post(self, **kw):
        d = {"number": "K-1", "kind": "PKWT", "start": "2026-01-01", "end": "2026-12-31", "previous": ""}; d.update(kw)
        return self.client.post(f"/employees/{self.e1.pk}/contracts/new/", d)

    def test_create_then_block_second_active_then_renew(self):
        self.login("hrd")
        self.assertEqual(self.post().status_code, 302); c1 = Contract.objects.get(number="K-1"); self.assertEqual(c1.status, "aktif")
        self.assertContains(self.post(number="K-2", start="2027-01-01", end="2027-12-31"), "sudah punya kontrak aktif")
        self.assertEqual(self.post(number="K-2", start="2027-01-01", end="2027-12-31", previous=c1.pk).status_code, 302)
        c1.refresh_from_db(); c2 = Contract.objects.get(number="K-2")
        self.assertEqual((c1.status, c2.status, c2.previous_id), ("diperpanjang", "aktif", c1.pk))
        self.assertEqual(EmployeeHistory.objects.filter(employee=self.e1, field="contract").count(), 2)  # pembuatan awal + perpanjangan
        h = EmployeeHistory.objects.get(employee=self.e1, field="contract", new_value="K-2"); self.assertEqual(h.old_value, "K-1")
        self.assertTrue(AuditLog.objects.filter(action="contract_renew").exists())

    def test_validation(self):
        self.login("hrd")
        self.assertContains(self.post(end="2025-01-01"), "Tidak boleh sebelum")
        self.post(); c1 = Contract.objects.get()
        self.assertContains(self.post(number="K-2", previous=c1.pk, start="2025-06-01", end=""), "Harus setelah")  # perpanjangan harus lebih baru
        self.assertContains(self.post(number="K-1", previous=c1.pk, start="2027-01-01", end="2027-12-31"), "telah ada")  # nomor kontrak unik
        self.assertEqual(Contract.objects.count(), 1)

    def test_other_employee_contract_not_selectable_as_previous(self):
        other = Contract.objects.create(employee=self.e2, number="X-1", kind="PKWT", start=date(2026, 1, 1))
        self.login("hrd"); r = self.post(previous=other.pk); self.assertEqual(r.status_code, 200)
        self.assertEqual(Contract.objects.filter(number="K-1").count(), 0)

    def test_forbidden_for_non_hrd(self):
        self.login("adm1"); self.assertEqual(self.post().status_code, 403)


class MasterTests(CoreBase):
    def test_department_crud_and_cycle_blocked(self):
        self.login("hrd")
        r = self.client.post("/master/department/new/", {"code": "c", "name": "QC", "parent": self.d1.pk}); self.assertRedirects(r, "/master/department/")
        qc = Department.objects.get(code="C"); self.assertEqual(qc.parent_id, self.d1.pk)  # kode di-uppercase
        r = self.client.post(f"/master/department/{self.d1.pk}/", {"code": "A", "name": "Produksi", "parent": qc.pk})
        self.assertContains(r, "turunannya"); self.assertIsNone(Department.objects.get(pk=self.d1.pk).parent_id)
        self.assertContains(self.client.post("/master/department/new/", {"code": "A", "name": "Dup"}), "sudah dipakai")
        self.assertTrue(AuditLog.objects.filter(action="master_create", object_type="Department").exists())

    def test_shift_midnight_rules(self):
        self.login("hrd")
        bad = self.client.post("/master/shift/new/", {"name": "Malam", "start": "22:00", "end": "06:00"}); self.assertContains(bad, "Melewati tengah malam")
        self.assertEqual(self.client.post("/master/shift/new/", {"name": "Malam", "start": "22:00", "end": "06:00", "crosses_midnight": "on"}).status_code, 302)
        self.assertContains(self.client.post("/master/shift/new/", {"name": "X", "start": "07:00", "end": "15:00", "crosses_midnight": "on"}), "hilangkan centang")

    def test_position_and_unknown_kind(self):
        self.login("hrd")
        self.assertEqual(self.client.post("/master/position/new/", {"name": "Manager", "level": 5}).status_code, 302)
        self.assertEqual(self.client.get("/master/bogus/").status_code, 404)
        self.assertEqual(self.client.get("/master/position/99999/").status_code, 404)

    def test_superadmin_allowed(self):
        self.login("su"); self.assertEqual(self.client.get("/master/shift/").status_code, 200)
