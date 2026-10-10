"""Putaran 35: ringkasan pasien (riwayat, alergi, BPJS K/TK) saat NIK dipilih; Pemeriksa = satu isian terakhir; layout penuh; aksi tabel terbaca."""
from datetime import date
from apps.hr.models import Department, Employee
from apps.hrd.models import BpjsMembership
from apps.poli import services
from apps.poli.models import MedicalRecord, PatientAllergy
from apps.poli.test_poli import PoliBase


class PatientPanelTests(PoliBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dpoli = Department.objects.create(code="POL", name="Poliklinik")
        cls.nurse = Employee.objects.create(nik="P01", name="Ratna Perawat", gender="P", department=cls.dpoli, position=cls.pos, join_date=date(2023, 1, 1))

    def setUp(self): self.login("poli")
    def js(self, nik="001"): return self.client.get("/poli/api/patient/", {"nik": nik}).json()

    # --- ringkasan pasien
    def test_summary_has_allergies_bpjs_and_kinds(self):
        services.add_allergy(self.poli, self.e1, "Amoxicillin", "Ruam", "berat")
        BpjsMembership.objects.create(employee=self.e1, scheme="kes", status="aktif", effective_date=date(2026, 1, 1))
        self.create(); j = self.js()
        self.assertEqual([a["substance"] for a in j["allergies"]], ["Amoxicillin"]); self.assertEqual(j["allergies"][0]["severity_label"], "Berat")
        self.assertEqual((j["bpjs"]["kes"]["status"], j["bpjs"]["kes"]["active"]), ("Aktif", True)); self.assertEqual(j["bpjs"]["tk"]["status"], "Belum dicatat")
        self.assertEqual(j["kinds"], [{"label": "Berobat", "n": 1}]); self.assertEqual(j["total"], 1)

    def test_summary_never_exposes_bpjs_number(self):
        Employee.objects.filter(pk=self.e1.pk).update(bpjs_kes="0001234567890")
        j = self.js(); self.assertTrue(j["bpjs"]["kes"]["number_on_file"]); self.assertNotIn("0001234567890", str(j))

    def test_voided_allergy_hidden_from_summary_but_kept(self):
        a = services.add_allergy(self.poli, self.e1, "Udang"); services.void_allergy(self.poli, a)
        self.assertEqual(self.js()["allergies"], []); self.assertEqual(PatientAllergy.objects.count(), 1)
        with self.assertRaises(ValueError): services.void_allergy(self.poli, a)
        with self.assertRaises(PermissionError): a.delete()

    def test_add_allergy_validation_and_duplicates(self):
        for bad in ("", "   ", "x" * 101): self.assertRaises(ValueError, services.add_allergy, self.poli, self.e1, bad)
        services.add_allergy(self.poli, self.e1, "Udang"); self.assertRaises(ValueError, services.add_allergy, self.poli, self.e1, "udang")

    def test_allergy_views_poli_only_and_audit_without_content(self):
        r = self.client.post(f"/poli/employees/{self.e1.pk}/allergy/add/", {"substance": "Penisilin", "reaction": "Sesak", "severity": "berat"}); self.assertEqual(r.status_code, 302)
        a = PatientAllergy.objects.get(); h = self.client.get(f"/poli/employees/{self.e1.pk}/").content.decode()
        for needle in ("Penisilin", "Sesak", "BPJS Kesehatan (K)", "Tambah alergi"): self.assertIn(needle, h)
        from apps.core.models import AuditLog
        self.assertFalse(any("Penisilin" in str(x.after) or "Sesak" in str(x.after) for x in AuditLog.objects.all()))
        self.assertEqual(self.client.post(f"/poli/employees/{self.e1.pk}/allergy/{a.pk}/void/").status_code, 302); a.refresh_from_db(); self.assertIsNotNone(a.voided_at)
        for u in ("hrd", "adm"):
            self.login(u)
            self.assertEqual(self.client.post(f"/poli/employees/{self.e1.pk}/allergy/add/", {"substance": "X", "severity": "ringan"}).status_code, 403)

    # --- pemeriksa: satu isian
    def test_form_has_single_visible_examiner_input_as_last_field(self):
        h = self.client.get("/poli/records/new/").content.decode()
        self.assertIn('type="hidden" name="examiner_nik"', h); self.assertNotIn("Dokter (bukan karyawan)", h)
        self.assertEqual(h.count('data-lk-free="1"'), 1)
        self.assertLess(h.index("Resep obat"), h.index('id="examiner-box"')); self.assertLess(h.index('id="examiner-box"'), h.index("Simpan rekam medis"))

    def test_typed_name_without_nik_is_doctor_name(self):
        self.assertEqual(self.post_record(doctor_name="dr. Budi Sp.PD").status_code, 302)
        r = MedicalRecord.objects.get(); self.assertEqual((r.examiner, r.doctor_name), (None, "dr. Budi Sp.PD"))

    def test_picked_poli_staff_is_examiner_and_name_not_duplicated(self):
        self.assertEqual(self.post_record(examiner_nik="P01", doctor_name="Ratna Perawat").status_code, 302)
        r = MedicalRecord.objects.get(); self.assertEqual((r.examiner, r.doctor_name), (self.nurse, ""))

    def test_typed_nik_of_poli_staff_resolves_to_examiner(self):
        self.assertEqual(self.post_record(doctor_name="P01").status_code, 302)
        r = MedicalRecord.objects.get(); self.assertEqual((r.examiner, r.doctor_name), (self.nurse, ""))

    def test_non_poli_nik_in_hidden_field_rejected_but_typed_text_is_just_a_name(self):
        self.assertEqual(self.post_record(examiner_nik="001").status_code, 200); self.assertEqual(MedicalRecord.objects.count(), 0)
        self.assertEqual(self.post_record(doctor_name="001").status_code, 302); self.assertEqual(MedicalRecord.objects.get().doctor_name, "001")  # bukan Poli → teks biasa

    # --- layout & aksi
    def test_layout_full_width_and_panel_always_present(self):
        h = self.client.get("/poli/records/new/").content.decode()
        self.assertIn('id="pt"', h); self.assertIn("rm-grid", h); self.assertNotIn("minmax(0,680px)", h)
        from django.template.loader import get_template
        base = get_template("base.html").template.source; self.assertIn("main{flex:1;width:100%;max-width:none", base)

    def test_catering_actions_have_visible_labels(self):
        self.login("hrd")
        from apps.hrd.models import CateringOrder
        CateringOrder.objects.create(date=date(2026, 10, 9), meal="12:00", qty_large=5, qty_small=2)
        h = self.client.get("/hrd/catering/", {"from": "2026-10-01", "to": "2026-10-10"}).content.decode()
        self.assertIn('class="btn sm"', h); self.assertIn('class="sm danger"', h); self.assertNotIn("danger lnk", h)


class PatientInfoAndQuickConclusionTests(PoliBase):
    def setUp(self): self.login("poli")

    def test_summary_has_identity_details(self):
        j = self.client.get("/poli/api/patient/", {"nik": "001"}).json()
        for k in ("gender", "department", "position", "join_date", "tenure", "shift", "group", "status"): self.assertIn(k, j)
        self.assertIn(j["gender"], ("Laki-laki", "Perempuan")); self.assertTrue(j["tenure"])

    def test_tenure_text(self):
        from django.utils import timezone
        t = date(2026, 10, 10)
        self.assertEqual(services.tenure_text(date(2026, 10, 1), t), "< 1 bulan"); self.assertEqual(services.tenure_text(date(2024, 7, 20), t), "2 tahun 2 bulan")
        self.assertEqual(services.tenure_text(date(2025, 10, 10), t), "1 tahun")

    def test_exam_diagnoses_created_idempotently_and_not_overwritten(self):
        from apps.poli.models import Diagnosis
        Diagnosis.objects.create(code="Z00.0", name="Nama kustom Poli")
        self.client.get("/poli/records/new/"); self.client.get("/poli/records/new/")
        self.assertEqual(set(Diagnosis.objects.filter(code__startswith="Z0").values_list("code", flat=True)), {"Z00.0", "Z00.8", "Z02.7"})
        self.assertEqual(Diagnosis.objects.get(code="Z00.0").name, "Nama kustom Poli")

    def test_quick_conclusion_buttons_and_saving_a_healthy_exam(self):
        h = self.client.get("/poli/records/new/").content.decode()
        for k in ("fit", "fit_catatan", "tidak_fit"): self.assertIn(f'data-q="{k}"', h)
        r = self.client.post("/poli/records/new/", {"nik": "001", "kind": "pemeriksaan", "diagnosis_code": "Z00.0", "exam_conclusion": "fit", "exam_result": "Sehat.", "rx-TOTAL_FORMS": "0", "rx-INITIAL_FORMS": "0", "rx-MIN_NUM_FORMS": "0", "rx-MAX_NUM_FORMS": "20"})
        self.assertEqual(r.status_code, 302); rec = MedicalRecord.objects.get(); self.assertEqual((rec.kind, rec.diagnosis.code), ("pemeriksaan", "Z00.0"))

    def test_error_hint_mentions_migrate(self):
        self.assertIn("python manage.py migrate", self.client.get("/poli/records/new/").content.decode())


class FlowTextLayoutTests(PoliBase):
    def test_hint_not_width_limited_and_flow_class_on_alur_pages(self):
        from django.template.loader import get_template
        self.assertNotIn("max-width:80ch", get_template("base.html").template.source)
        self.login("hrd")
        for url in ("/hrd/catering/", "/hrd/uniforms/", "/hrd/bpjs/deductions/"):
            self.assertIn('class="hint flow"', self.client.get(url).content.decode(), url)
