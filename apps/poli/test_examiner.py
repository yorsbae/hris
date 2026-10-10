"""Putaran 34: rekam medis punya Pemeriksa (karyawan departemen Poli, dipilih lewat NIK) dan Dokter (bukan karyawan, hanya nama)."""
from datetime import date
from django.test import override_settings
from apps.hr.models import Department, Employee
from apps.poli import services
from apps.poli.models import LetterCounter, MedicalRecord, SickLeaveLetter
from apps.poli.pdf import sick_leave_pdf
from apps.poli.test_letter_kop import pdf_text
from apps.poli.test_poli import PoliBase


class ExaminerTests(PoliBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dpoli = Department.objects.create(code="POL", name="Poliklinik")
        cls.nurse = Employee.objects.create(nik="P01", name="Ratna Perawat", gender="P", department=cls.dpoli, position=cls.pos, join_date=date(2023, 1, 1))
        cls.gone = Employee.objects.create(nik="P09", name="Mantan Perawat", gender="P", department=cls.dpoli, position=cls.pos, join_date=date(2022, 1, 1), status="nonaktif")

    def setUp(self): self.login("poli")

    def test_poli_staff_helper_only_active_poli_department(self):
        self.assertEqual([e.nik for e in services.poli_staff()], ["P01"])
        self.assertEqual(services.poli_staff("P01"), self.nurse)
        for nik in ("001", "P09", "zzz"): self.assertIsNone(services.poli_staff(nik), nik)  # bukan Poli / nonaktif / tidak ada

    def test_record_saved_with_examiner_and_free_text_doctor(self):
        resp = self.post_record(examiner_nik="P01", doctor_name="  dr.  Anisa   Rahayu ")
        self.assertEqual(resp.status_code, 302)
        r = MedicalRecord.objects.get(); self.assertEqual((r.examiner, r.doctor_name), (self.nurse, "dr. Anisa Rahayu"))  # spasi dirapikan
        page = self.client.get(resp["Location"]).content.decode()
        for t in ("Pemeriksa", "Ratna Perawat", "P01", "Dokter", "dr. Anisa Rahayu"): self.assertIn(t, page)
        self.assertFalse(Employee.objects.filter(name__contains="Anisa").exists())  # dokter tidak dibuat sebagai karyawan

    def test_both_inputs_are_optional(self):
        self.assertEqual(self.post_record().status_code, 302)
        r = MedicalRecord.objects.get(); self.assertIsNone(r.examiner); self.assertEqual(r.doctor_name, "")
        self.assertNotIn("<th>Pemeriksa</th>", self.client.get(f"/poli/records/{r.pk}/").content.decode())

    def test_examiner_must_be_active_poli_employee(self):
        for nik in ("002", "P09", "NOPE"):
            resp = self.post_record(examiner_nik=nik); self.assertEqual(resp.status_code, 200, nik); self.assertContains(resp, "karyawan aktif departemen Poli")
        self.assertEqual(MedicalRecord.objects.count(), 0)
        with self.assertRaises(ValueError): services.create_record(self.poli, self.e1, "berobat", "k", {}, None, "", (), examiner=self.e2)  # servis juga menolak

    def test_doctor_name_length_capped(self):
        self.assertEqual(self.post_record(doctor_name="x" * 101).status_code, 200); self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_form_offers_poli_only_lookup_and_env_default_doctor(self):
        with override_settings(POLI_DOCTOR_NAME="dr. Default"):
            h = self.client.get("/poli/records/new/").content.decode()
        self.assertIn(f'data-q-department="{self.dpoli.pk}"', h); self.assertIn('name="examiner_nik"', h); self.assertIn('value="dr. Default"', h)

    def test_lookup_endpoint_filters_by_poli_department(self):
        j = self.client.get(f"/api/lookup/employees/?q=P0&department={self.dpoli.pk}").json()["results"]
        self.assertEqual([x["nik"] for x in j], ["P01"])

    def test_audit_has_markers_not_names(self):
        self.post_record(examiner_nik="P01", doctor_name="dr. Rahasia")
        from apps.core.models import AuditLog
        a = AuditLog.objects.filter(action="create_record").latest("id"); blob = str(a.__dict__)
        self.assertIn("P01", blob); self.assertNotIn("dr. Rahasia", blob)

    def test_signer_priority_on_letters(self):
        def sign(**kw):
            r = services.create_record(self.poli, self.e1, "berobat", "k", {}, None, "", (), **kw)[0]
            l = SickLeaveLetter.objects.create(record=r, kind="izin_pulang", number=LetterCounter.next_number("sip", date.today()) + str(r.pk)); return pdf_text(sick_leave_pdf(l))
        with override_settings(POLI_DOCTOR_NAME="dr. Perusahaan", COMPANY_NAME="PT X"):
            t = sign(examiner=self.nurse, doctor_name="dr. Pemeriksa"); self.assertIn("Dokter Pemeriksa,", t); self.assertIn("( dr. Pemeriksa )", t)   # dokter di rekam medis menang
            t = sign(examiner=self.nurse); self.assertIn("Dokter Perusahaan,", t); self.assertIn("( dr. Perusahaan )", t)                           # lalu dokter perusahaan
        with override_settings(POLI_DOCTOR_NAME="", COMPANY_NAME="PT X"):
            t = sign(examiner=self.nurse); self.assertIn("Petugas Poliklinik,", t); self.assertIn("( Ratna Perawat )", t)                           # lalu pemeriksa
            t = sign(); self.assertIn("Petugas Poliklinik,", t); self.assertNotIn("Ratna", t)                                                       # terakhir pembuat rekam medis
