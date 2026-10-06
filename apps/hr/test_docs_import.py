import json, os, shutil, tempfile
from datetime import date, timedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from apps.core.crypto import is_encrypted
from apps.core.models import AuditLog
from .models import Contract, Employee, EmployeeDocument
from .test_core import CoreBase, KEY, raw

TMP = tempfile.mkdtemp()
PDF = b"%PDF-1.4\n%fake\n"


def tearDownModule(): shutil.rmtree(TMP, ignore_errors=True)


@override_settings(MEDIA_ROOT=TMP, FIELD_ENCRYPTION_KEY=KEY)
class DocumentTests(CoreBase):
    def up(self, name="ktp.pdf", content=PDF, pk=None, **kw):
        d = {"kind": "ktp", "title": "KTP Budi", "file": SimpleUploadedFile(name, content)}; d.update(kw)
        return self.client.post(f"/employees/{pk or self.e1.pk}/documents/new/", d)

    def make(self):
        self.login("hrd"); self.up(); return EmployeeDocument.objects.get()

    def test_upload_stores_random_name_and_audits(self):
        d = self.make()
        self.assertNotIn("ktp", os.path.basename(d.file.name)); self.assertTrue(d.file.name.endswith(".pdf"))
        self.assertEqual((d.original_name, d.uploaded_by, d.size), ("ktp.pdf", self.hrd, len(PDF)))
        self.assertTrue(AuditLog.objects.filter(action="document_upload").exists())

    def test_rejects_bad_type_fake_content_empty_and_oversize(self):
        self.login("hrd")
        for name, content, msg in (("a.exe", PDF, "tidak diizinkan"), ("a.pdf", b"MZ\x90\x00 bukan pdf", "tidak sesuai"), ("a.png", PDF, "tidak sesuai"),
                                   ("a.pdf", b"", "kosong"), ("a.pdf", PDF + b"x" * (5 * 1024 * 1024), "maksimal 5 MB")):
            r = self.up(name, content); self.assertEqual(r.status_code, 200); self.assertContains(r, msg)
        self.assertFalse(EmployeeDocument.objects.exists())

    def test_path_traversal_filename_is_harmless(self):
        self.login("hrd"); self.up("../../evil.pdf"); d = EmployeeDocument.objects.get()
        self.assertTrue(os.path.realpath(d.file.path).startswith(os.path.realpath(TMP)))
        self.assertNotIn("evil", d.file.name)

    def test_download_content_and_audit(self):
        d = self.make(); r = self.client.get(f"/employees/{self.e1.pk}/documents/{d.pk}/")
        self.assertEqual(b"".join(r.streaming_content), PDF); self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(AuditLog.objects.filter(action="document_download", object_id=str(d.pk)).exists())

    def test_only_hrd_and_scope(self):
        d = self.make()
        for who in ("adm1", "poli"):
            self.login(who)
            self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/documents/{d.pk}/").status_code, 403)
            self.assertEqual(self.client.post(f"/employees/{self.e1.pk}/documents/{d.pk}/delete/", {"reason": "x"}).status_code, 403)
            self.assertEqual(self.up().status_code, 403)
        self.assertEqual(EmployeeDocument.objects.count(), 1)
        self.login("hrd")  # dokumen milik karyawan A tidak bisa dibuka lewat URL karyawan B
        self.assertEqual(self.client.get(f"/employees/{self.e2.pk}/documents/{d.pk}/").status_code, 404)
        self.client.logout(); self.assertIn(self.client.get(f"/employees/{self.e1.pk}/documents/{d.pk}/").status_code, (302, 401))

    def test_documents_hidden_from_dept_admin_page(self):
        self.make(); self.login("adm1"); r = self.client.get(f"/employees/{self.e1.pk}/")
        self.assertNotContains(r, "KTP Budi"); self.assertNotContains(r, "<h3>Dokumen"); self.assertNotContains(r, "/documents/")

    def test_soft_delete_requires_reason_keeps_file(self):
        d = self.make(); url = f"/employees/{self.e1.pk}/documents/{d.pk}/delete/"
        self.client.post(url, {"reason": " "}); d.refresh_from_db(); self.assertIsNone(d.deleted_at)
        self.client.post(url, {"reason": "salah unggah"}); d.refresh_from_db()
        self.assertIsNotNone(d.deleted_at); self.assertTrue(os.path.exists(d.file.path))
        self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/documents/{d.pk}/").status_code, 404)
        self.assertNotContains(self.client.get(f"/employees/{self.e1.pk}/"), "KTP Budi")
        self.assertEqual(self.client.get(url).status_code, 405)  # hapus hanya via POST

    def test_deleted_employee_documents_unreachable(self):
        d = self.make(); self.e1.soft_delete(self.hrd, "x")
        self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/documents/{d.pk}/").status_code, 404)

    def test_title_xss_escaped(self):
        self.login("hrd"); self.up(title="<img src=x onerror=alert(1)>")
        self.assertNotContains(self.client.get(f"/employees/{self.e1.pk}/"), "<img src=x onerror")


HEAD = "nik,name,gender,join_date,department_code,position,shift,status,nik_ktp,supervisor_nik\n"


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class ImportTests(CoreBase):
    def imp(self, csv, mode="import", name="k.csv"):
        data = csv.encode() if isinstance(csv, str) else csv
        return self.client.post("/employees/import/", {"mode": mode, "file": SimpleUploadedFile(name, data)})

    def test_check_only_writes_nothing(self):
        self.login("hrd"); r = self.imp(HEAD + "N1,Ani,P,2025-01-01,A,Staff,Pagi,aktif,,\n", "check")
        self.assertContains(r, "Semua 1 baris valid"); self.assertFalse(Employee.objects.filter(nik="N1").exists())

    def test_import_creates_encrypted_and_audit_has_no_row_data(self):
        self.login("hrd")
        r = self.imp(HEAD + f"N1,Ani,P,2025-01-01,a,Staff,Pagi,,{3326010101010002},\nN2,Eko,L,2025-02-01,B,,,aktif,,N1\n")
        self.assertContains(r, "2 karyawan diimpor")
        n1, n2 = Employee.objects.get(nik="N1"), Employee.objects.get(nik="N2")
        self.assertEqual((n1.status, n1.department_id, n1.shift_id, n2.supervisor_id), ("aktif", self.d1.pk, self.s1.pk, n1.pk))
        self.assertTrue(is_encrypted(raw(n1.pk, "nik_ktp"))); self.assertEqual(n1.nik_ktp, "3326010101010002")
        a = AuditLog.objects.get(action="employee_import"); self.assertEqual((a.after["rows"], a.after["created"]), (2, 2))
        self.assertNotIn("3326010101010002", json.dumps(a.after)); self.assertNotIn("Ani", json.dumps(a.after))

    def test_all_or_nothing_on_one_bad_row(self):
        self.login("hrd")
        r = self.imp(HEAD + "N1,Ani,P,2025-01-01,A,,,,,\nN2,Eko,L,2025-02-01,ZZZ,,,,,\nN3,Ito,X,2025-02-01,A,,,,123,\n")
        self.assertContains(r, "Tidak ada yang disimpan"); self.assertContains(r, "tidak ada"); self.assertContains(r, "16 digit")
        self.assertEqual(Employee.objects.count(), 2)  # hanya e1, e2 dari fixture
        self.assertTrue(AuditLog.objects.filter(action="employee_import_rejected").exists())

    def test_duplicates_unknown_master_and_bad_supervisor(self):
        self.login("hrd")
        r = self.imp(HEAD + "N1,A,L,2025-01-01,A,,,,,\nN1,B,L,2025-01-01,A,,,,,\n001,C,L,2025-01-01,A,,,,,\nN4,D,L,2025-01-01,A,Dewa,Siang,,,\nN5,E,L,2025-01-01,A,,,,,NOPE\nN6,F,L,2025-01-01,A,,,,,N6\n")
        for m in ("dobel di dalam file", "sudah dipakai", "Jabatan &#x27;Dewa&#x27; tidak ada", "Shift &#x27;Siang&#x27; tidak ada", "Atasan tidak ditemukan"): self.assertContains(r, m)
        self.assertEqual(Employee.objects.count(), 2)

    def test_semicolon_bom_and_dmy_dates(self):
        self.login("hrd")
        r = self.imp("\ufeffnik;name;gender;join_date;department_code\nN1;Ani;P;15/01/2025;A\n")
        self.assertContains(r, "1 karyawan diimpor"); self.assertEqual(Employee.objects.get(nik="N1").join_date, date(2025, 1, 15))

    def test_fatal_errors(self):
        self.login("hrd")
        self.assertContains(self.imp("nik,name\nN1,Ani\n"), "Kolom wajib tidak ada")
        self.assertContains(self.imp(b"nik,name\n\xff\xfe\x00"), "UTF-8")
        self.assertContains(self.imp(HEAD), "tidak berisi data")
        self.assertContains(self.client.post("/employees/import/", {"mode": "import"}), "Pilih file")

    def test_formula_injection_rejected(self):
        self.login("hrd"); r = self.imp(HEAD + "N1,=cmd|' /c calc'!A0,L,2025-01-01,A,,,,,\n")
        self.assertContains(r, "formula"); self.assertFalse(Employee.objects.filter(nik="N1").exists())

    def test_only_hrd_and_template(self):
        for who in ("adm1", "poli"):
            self.login(who); self.assertEqual(self.client.get("/employees/import/").status_code, 403)
            self.assertEqual(self.imp(HEAD + "N1,A,L,2025-01-01,A,,,,,\n").status_code, 403)
            self.assertEqual(self.client.get("/employees/import/template.csv").status_code, 403)
        self.assertFalse(Employee.objects.filter(nik="N1").exists())
        self.login("hrd"); r = self.client.get("/employees/import/template.csv"); self.assertIn(b"department_code", r.content)

    def test_template_roundtrip_is_valid_after_master_exists(self):
        from .importer import template_csv
        from .models import Department, Position
        Department.objects.create(code="PROD", name="Prod"); Position.objects.create(name="Staff")
        self.login("hrd"); self.assertContains(self.imp(template_csv(), "check"), "Semua 1 baris valid")

    def test_import_of_3000_rows(self):
        self.login("hrd")
        body = HEAD + "".join(f"B{i:04d},Nama {i},L,2025-01-01,A,Staff,Pagi,aktif,,\n" for i in range(3000))
        self.assertContains(self.imp(body), "3000 karyawan diimpor"); self.assertEqual(Employee.objects.count(), 3002)


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class FilterTests(CoreBase):
    def niks(self, qs="", who="hrd"):
        self.login(who); return {r["nik"] for r in self.client.get("/api/employees/?" + qs).json()["results"]}

    def test_position_shift_department_filters(self):
        Employee.objects.filter(pk=self.e1.pk).update(position=self.p2, shift=self.s1)
        self.assertEqual(self.niks(f"position={self.p2.pk}"), {"001"}); self.assertEqual(self.niks(f"shift={self.s1.pk}"), {"001"})
        self.assertEqual(self.niks(f"department={self.d2.pk}"), {"002"})

    def test_invalid_ids_ignored_not_500(self):
        for q in ("department=abc", "position='", "shift=1;DROP", "contract=zzz"):
            self.assertEqual(self.niks(q), {"001", "002"})

    def test_contract_filters(self):
        t = date.today()
        Contract.objects.create(employee=self.e1, number="C1", kind="PKWT", start=t - timedelta(days=300), end=t + timedelta(days=10))
        Contract.objects.create(employee=self.e2, number="C2", kind="PKWT", start=t - timedelta(days=300), end=t - timedelta(days=1))
        self.assertEqual(self.niks("contract=expiring"), {"001"}); self.assertEqual(self.niks("contract=expired"), {"002"})
        self.assertEqual(self.niks("contract=aktif"), {"001"})
        e3 = Employee.objects.create(nik="003", name="X", gender="L", department=self.d1, join_date=t)
        self.assertEqual(self.niks("contract=none"), {"003"})

    def test_contract_filter_ignored_for_non_hrd_and_scope_kept(self):
        t = date.today(); Contract.objects.create(employee=self.e1, number="C1", kind="PKWT", start=t, end=t + timedelta(days=5))
        self.assertEqual(self.niks("contract=expired", "adm1"), {"001"})  # filter diabaikan → tidak bocor info kontrak
        self.assertEqual(self.niks(f"department={self.d2.pk}", "adm1"), {"001"})  # tidak bisa keluar dari scope

    def test_filters_rendered_by_role(self):
        self.login("hrd"); r = self.client.get("/employees/"); self.assertContains(r, 'id="contract"'); self.assertContains(r, 'id="department"')
        self.login("adm1"); r = self.client.get("/employees/"); self.assertNotContains(r, 'id="contract"'); self.assertNotContains(r, 'id="department"')
