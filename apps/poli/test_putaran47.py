"""Putaran 47: alergi di rekam medis (otomatis tampil di sesi berikutnya), rujukan lengkap (BPJS K/TK, tanggal kejadian, ttd pemeriksa),
surat istirahat/cuti tunggal, cuti hamil, umur dari tanggal lahir."""
from datetime import date, timedelta
from django.test import override_settings
from django.utils import timezone
from apps.core.models import AuditLog
from apps.hr.models import Employee
from apps.poli import services
from apps.poli.models import MedicalRecord, PatientAllergy, Referral, SickLeaveLetter
from apps.poli.pdf import referral_pdf, terbilang
from apps.poli.test_letter_kop import pdf_text
from apps.poli.test_poli import PoliBase


class AllergyInRecordTests(PoliBase):
    def setUp(self): self.login("poli")

    def test_new_allergy_saved_with_the_record_and_audited_without_content(self):
        resp = self.post_record(allergy_substance="  Amoxicillin ", allergy_reaction="gatal", allergy_severity="berat")
        self.assertEqual(resp.status_code, 302)
        a = PatientAllergy.objects.get(); self.assertEqual((a.employee, a.substance, a.reaction, a.severity), (self.e1, "Amoxicillin", "gatal", "berat"))
        blob = str(AuditLog.objects.filter(action="create_record").latest("id").__dict__); self.assertNotIn("Amoxicillin", blob); self.assertNotIn("gatal", blob)

    def test_allergy_shows_automatically_in_next_session(self):
        self.post_record(allergy_substance="Udang")
        j = self.client.get("/poli/api/patient/?nik=001").json(); self.assertEqual([x["substance"] for x in j["allergies"]], ["Udang"])  # panel pasien di form
        r2 = self.post_record(); page = self.client.get(r2["Location"]).content.decode()
        self.assertIn("Alergi pasien", page); self.assertIn("Udang", page)                                                                  # banner di detail rekam medis berikutnya
        self.assertIn("al-current", self.client.get("/poli/records/new/?nik=001").content.decode())

    def test_other_patients_allergy_is_not_shown(self):
        self.post_record(allergy_substance="Udang")
        r = self.post_record(nik="002"); self.assertNotIn("Udang", self.client.get(r["Location"]).content.decode())

    def test_duplicate_active_allergy_is_rejected_and_nothing_saved(self):
        self.post_record(allergy_substance="Udang"); n = MedicalRecord.objects.count()
        resp = self.post_record(allergy_substance="udang"); self.assertEqual(resp.status_code, 200); self.assertContains(resp, "sudah tercatat")
        self.assertEqual((MedicalRecord.objects.count(), PatientAllergy.objects.count()), (n, 1))

    def test_voided_allergy_can_be_recorded_again(self):
        self.post_record(allergy_substance="Udang"); services.void_allergy(self.poli, PatientAllergy.objects.get())
        self.assertEqual(self.post_record(allergy_substance="Udang").status_code, 302); self.assertEqual(PatientAllergy.objects.filter(voided_at__isnull=True).count(), 1)

    def test_reaction_without_substance_is_rejected(self):
        self.assertEqual(self.post_record(allergy_reaction="gatal").status_code, 200); self.assertEqual(PatientAllergy.objects.count(), 0)

    def test_failed_record_does_not_leave_the_allergy_behind(self):
        with self.assertRaises(ValueError):  # stok tidak cukup → seluruh transaksi dibatalkan
            services.create_record(self.poli, self.e1, "berobat", "k", {}, None, "", [(self.med.pk, 9999, "")], allergy=("Udang", "", "sedang"))
        self.assertEqual((MedicalRecord.objects.count(), PatientAllergy.objects.count()), (0, 0))

    def test_form_has_allergy_inputs_and_hrd_cannot_use_them(self):
        h = self.client.get("/poli/records/new/").content.decode()
        for t in ('name="allergy_substance"', 'name="allergy_reaction"', 'name="allergy_severity"', "Alergi pasien"): self.assertIn(t, h)
        self.login("hrd"); self.assertEqual(self.client.post("/poli/records/new/", {"nik": "001", "allergy_substance": "x"}).status_code, 403)


class ReferralDataTests(PoliBase):
    def setUp(self): self.login("poli")

    def test_referral_form_saves_purpose_incident_date_and_signer(self):
        r = self.create(); d = date.today() - timedelta(days=2)
        resp = self.client.post(f"/poli/records/{r.pk}/referral/new/", {"facility": "RSUD Kota", "purpose": "tes_lab", "incident_date": d.isoformat(), "note": "n"})
        ref = Referral.objects.get(); self.assertEqual(resp.status_code, 302)
        self.assertEqual((ref.purpose, ref.incident_date, ref.signer_name), ("tes_lab", d, "poli")); self.assertTrue(ref.signer_role)
        page = self.client.get(resp["Location"]).content.decode()
        for t in ("Tes lab", "Tanggal kejadian", "Penanda tangan"): self.assertIn(t, page)

    def test_incident_date_defaults_to_visit_date_and_future_is_rejected(self):
        r = self.create(); ref = services.create_referral(self.poli, r, "RS A", purpose="berobat")
        self.assertEqual(ref.incident_date, timezone.localtime(r.visit_at).date())
        with self.assertRaises(ValueError): services.create_referral(self.poli, self.create(self.e2), "RS A", incident_date=date.today() + timedelta(days=1))
        resp = self.client.post(f"/poli/records/{r.pk}/referral/new/", {"facility": "RS B", "purpose": "berobat", "incident_date": (date.today() + timedelta(days=3)).isoformat()}); self.assertEqual(resp.status_code, 200)

    def test_purpose_is_required_and_validated(self):
        r = self.create()
        for bad in ({"facility": "RS"}, {"facility": "RS", "purpose": "xx"}): self.assertEqual(self.client.post(f"/poli/records/{r.pk}/referral/new/", bad).status_code, 200, bad)
        with self.assertRaises(ValueError): services.create_referral(self.poli, r, "RS", purpose="xx")

    def test_printed_referral_carries_bpjs_numbers_incident_date_complaint_and_signer(self):
        self.e1.address = "Jl. Mawar 1, Batang"; self.e1.birth_date = date.today().replace(year=date.today().year - 40) - timedelta(days=1); self.e1.save()
        rec = services.create_record(self.poli, self.e1, "kecelakaan_kerja", "Tangan terjepit", {"kecelakaan": {"lokasi": "Line 2", "kronologi": "Terjepit mesin"}}, self.diag, "", (), doctor_name="dr. Muhammad Jaelani")[0]
        ref = services.create_referral(self.poli, rec, "RSUD Batang", "Cek rontgen", purpose="spesialis", incident_date=date(2026, 10, 1))
        t = pdf_text(referral_pdf(ref))
        for need in (self.e1.bpjs_kes, self.e1.bpjs_tk, "01-10-2026", "Tangan terjepit", "Line 2", "Terjepit mesin", "Jl. Mawar 1, Batang", "40 tahun", "Laki-laki", "RSUD Batang", "( dr. Muhammad Jaelani )", "Dokter Pemeriksa,"):
            self.assertIn(need, t, need)
        self.assertNotIn("A09", t); self.assertNotIn("Diare", t)

    def test_referral_pdf_for_legacy_row_without_new_data_still_prints(self):
        r = self.create(); ref = Referral.objects.create(number="RJK/OLD/1", record=r, facility="RS Lama", created_by=self.poli)
        t = pdf_text(referral_pdf(ref)); self.assertIn("RS Lama", t); self.assertIn("( poli )", t)

    def test_referral_list_shows_purpose_on_record_detail(self):
        r = self.create(); services.create_referral(self.poli, r, "RS A", purpose="spesialis")
        h = self.client.get(f"/poli/records/{r.pk}/").content.decode(); self.assertIn("Rujukan spesialis", h); self.assertIn("Cetak rujukan", h)


class PureHelperTests(PoliBase):
    def test_terbilang(self):
        for n, w in ((0, "nol"), (3, "tiga"), (11, "sebelas"), (14, "empat belas"), (20, "dua puluh"), (21, "dua puluh satu"), (100, "seratus"), (105, "seratus lima"), (365, "tiga ratus enam puluh lima")):
            self.assertEqual(terbilang(n), w)
        with self.assertRaises(ValueError): terbilang(1000)

    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(services.add_months(date(2026, 1, 31), 1), date(2026, 2, 28)); self.assertEqual(services.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(services.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))

    def test_employee_age(self):
        e = Employee(birth_date=date(1990, 6, 15)); self.assertEqual(e.age(date(2026, 6, 14)), 35); self.assertEqual(e.age(date(2026, 6, 15)), 36); self.assertIsNone(Employee().age())

    def test_create_letter_rules(self):
        r = self.create(); l, new = services.create_letter(self.poli, r, "istirahat", go_home=True)
        self.assertTrue(new); self.assertEqual(services.create_letter(self.poli, r, "istirahat", start_date=date.today(), days=5), (l, False))
        for kw in ({"kind": "istirahat"}, {"kind": "istirahat", "start_date": date.today(), "days": 31}, {"kind": "izin_hamil", "start_date": date.today()}, {"kind": "izin_pulang"}):
            with self.assertRaises(ValueError, msg=kw): services.create_letter(self.poli, self.create(self.e2), **kw)
        p = self.create(self.e2, kind="kehamilan")
        with self.assertRaises(ValueError): services.create_letter(self.poli, p, "izin_hamil", start_date=date.today(), months=7)
        h, _ = services.create_letter(self.poli, p, "izin_hamil", start_date=date(2026, 10, 12)); self.assertEqual((h.months, h.days), (3, 92))


class EmployeeBirthDateTests(PoliBase):
    def test_birth_date_form_validation_detail_and_audit_hides_value(self):
        self.login("hrd"); e = self.e1
        base = {"nik": e.nik, "name": e.name, "gender": e.gender, "join_date": e.join_date.isoformat(), "department": e.department_id, "position": e.position_id or "", "status": "aktif", "gs_short": "14", "effective_date": date.today().isoformat()}
        self.assertEqual(self.client.post(f"/employees/{e.pk}/edit/", {**base, "birth_date": date.today().isoformat()}).status_code, 200)  # umur 0 → ditolak
        self.assertEqual(self.client.post(f"/employees/{e.pk}/edit/", {**base, "birth_date": "1990-05-05"}).status_code, 302)
        e.refresh_from_db(); self.assertEqual(e.birth_date, date(1990, 5, 5))
        self.assertIn("05-05-1990", self.client.get(f"/employees/{e.pk}/").content.decode())
        self.assertNotIn("1990-05-05", str(AuditLog.objects.filter(action="employee_update").latest("id").__dict__))  # tanggal lahir = data sensitif, tidak masuk audit
