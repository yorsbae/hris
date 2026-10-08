"""Putaran 19: form rekam medis menyesuaikan jenis kunjungan, surat izin per jenis, riwayat poli di detail karyawan, hapus baris obat."""
from datetime import date, timedelta
from apps.core.models import AuditLog
from apps.hr.models import Employee
from apps.poli.models import MedicalRecord, Prescription, SickLeaveLetter
from apps.poli.test_poli import PoliBase


class KindFormTests(PoliBase):
    def setUp(self): self.login("poli")

    def test_pregnancy_hpl_and_weeks_computed_from_hpht(self):
        hpht = date.today() - timedelta(days=84)  # 12 minggu
        r = self.post_record(nik="002", kind="kehamilan", preg_hpht=hpht.isoformat(), preg_g="2", preg_p="1", preg_a="0", preg_letak="Kepala")
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        k = MedicalRecord.objects.latest("id").exam["kehamilan"]
        self.assertEqual(k["hpl"], (hpht + timedelta(days=280)).isoformat()); self.assertEqual(k["usia_minggu"], 12)
        self.assertEqual((k["g"], k["p"], k["a"], k["letak"]), (2, 1, 0, "Kepala"))
        page = self.client.get(f"/poli/records/{MedicalRecord.objects.latest('id').pk}/").content.decode()
        self.assertIn("HPHT", page); self.assertIn("G (gravida)", page)

    def test_pregnancy_validation(self):
        for extra, msg in (({"preg_hpht": (date.today() + timedelta(days=2)).isoformat()}, "masa depan"), ({}, "usia kehamilan atau HPHT"),
                           ({"preg_weeks": "10", "preg_g": "1", "preg_p": "1", "preg_a": "1"}, "P + A"),
                           ({"preg_hpht": (date.today() - timedelta(days=30)).isoformat(), "preg_hpl": (date.today() - timedelta(days=40)).isoformat()}, "setelah HPHT")):
            r = self.post_record(nik="002", kind="kehamilan", **extra); self.assertEqual(r.status_code, 200); self.assertContains(r, msg)
        self.assertEqual(self.post_record(nik="001", kind="kehamilan", preg_weeks="8").status_code, 200)  # laki-laki ditolak
        self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_work_accident_fields(self):
        r = self.post_record(kind="kecelakaan_kerja", incident_place="Line 2", incident_story="Tergores mesin", injury_part="Tangan kanan", injury_type="luka_robek", lost_days="2")
        self.assertEqual(r.status_code, 302); inc = MedicalRecord.objects.latest("id").exam["kecelakaan"]
        self.assertEqual((inc["bagian_tubuh"], inc["jenis_cedera"], inc["hari_hilang"]), ("Tangan kanan", "luka_robek", 2))
        page = self.client.get(r["Location"]).content.decode(); self.assertIn("Luka robek", page); self.assertIn("Hari kehilangan kerja", page)

    def test_checkup_needs_conclusion_and_ignores_other_kind_fields(self):
        self.assertContains(self.post_record(kind="pemeriksaan", complaint=""), "Kesimpulan pemeriksaan wajib")
        r = self.post_record(kind="pemeriksaan", exam_conclusion="fit_catatan", exam_result="TD 130/85", preg_weeks="9", incident_place="x")
        self.assertEqual(r.status_code, 302); e = MedicalRecord.objects.latest("id").exam
        self.assertEqual(e["pemeriksaan"]["kesimpulan"], "fit_catatan"); self.assertNotIn("kehamilan", e); self.assertNotIn("kecelakaan", e)

    def test_visit_requires_complaint_only_for_berobat(self):
        self.assertContains(self.post_record(kind="berobat", complaint=""), "Keluhan wajib")

    def test_form_has_kind_sections_and_row_delete(self):
        h = self.client.get("/poli/records/new/").content.decode()
        for t in ('data-kinds="kehamilan"', 'data-kinds="kecelakaan_kerja"', 'data-kinds="pemeriksaan"', "rx-del", "preg_hpht"): self.assertIn(t, h)

    def test_blank_rows_after_delete_are_ignored(self):
        r = self.post_record(rx=[(str(self.med.pk), "2", "3x1")])  # baris 2–5 dikosongkan (hasil klik Hapus) → diabaikan
        self.assertEqual(r.status_code, 302); self.assertEqual(Prescription.objects.count(), 1)


class LetterTests(PoliBase):
    def setUp(self): self.login("poli")

    def rec(self, nik="001", kind="berobat"):
        e = Employee.objects.get(nik=nik); from apps.poli import services
        return services.create_record(self.poli, e, kind, "k", {"kehamilan": {"usia_minggu": 20, "hpl": "2027-01-10"}} if kind == "kehamilan" else {}, None, "", ())[0]

    def test_leave_letter_created_once_and_printed(self):
        r = self.rec(); u = f"/poli/records/{r.pk}/letter/new/"
        self.assertEqual(self.client.get(u).status_code, 200)
        resp = self.client.post(u, {"kind": "izin_libur", "start_date": date.today().isoformat(), "days": "3"})
        l = SickLeaveLetter.objects.get(record=r, kind="izin_libur"); self.assertEqual(resp.status_code, 302); self.assertTrue(l.number.startswith("SIL/"))
        pdf = self.client.get(resp["Location"]); self.assertTrue(pdf.content.startswith(b"%PDF")); self.assertEqual(l.end_date, date.today() + timedelta(days=2))
        self.client.post(u, {"kind": "izin_libur", "start_date": date.today().isoformat(), "days": "9"})
        self.assertEqual(SickLeaveLetter.objects.filter(record=r).count(), 1); self.assertEqual(SickLeaveLetter.objects.get(record=r).days, 3)  # idempoten
        self.assertTrue(AuditLog.objects.filter(action="print_letter").exists() and AuditLog.objects.filter(action="create_letter").exists())

    def test_pulang_letter_needs_no_dates_and_both_kinds_coexist(self):
        r = self.rec(); u = f"/poli/records/{r.pk}/letter/new/"
        self.assertEqual(self.client.post(u, {"kind": "izin_pulang"}).status_code, 302)
        self.client.post(u, {"kind": "izin_libur", "start_date": date.today().isoformat(), "days": "1"})
        self.assertEqual(set(SickLeaveLetter.objects.filter(record=r).values_list("kind", flat=True)), {"izin_pulang", "izin_libur"})

    def test_validation_and_pregnancy_letter(self):
        r = self.rec(); u = f"/poli/records/{r.pk}/letter/new/"
        for bad in ({"kind": "izin_libur"}, {"kind": "izin_libur", "start_date": "2026-10-10", "days": "40"}, {"kind": "izin_hamil", "start_date": "2026-10-10", "days": "1", "purpose": "kontrol"}):
            self.assertEqual(self.client.post(u, bad).status_code, 200, bad)
        self.assertEqual(SickLeaveLetter.objects.count(), 0)
        p = self.rec("002", "kehamilan"); u2 = f"/poli/records/{p.pk}/letter/new/"
        self.assertEqual(self.client.post(u2, {"kind": "izin_hamil", "start_date": "2026-10-12", "days": "1"}).status_code, 200)  # keperluan wajib
        resp = self.client.post(u2, {"kind": "izin_hamil", "start_date": "2026-10-12", "days": "1", "purpose": "kontrol"})
        self.assertEqual(resp.status_code, 302); self.assertTrue(SickLeaveLetter.objects.get(record=p).number.startswith("SIH/"))
        self.assertTrue(self.client.get(resp["Location"]).content.startswith(b"%PDF"))

    def test_letters_closed_to_hrd_and_dept_admin(self):
        r = self.rec(); l = SickLeaveLetter.objects.create(record=r, kind="izin_pulang", number="SIP/X/1")
        for who in ("hrd", "adm"):
            self.login(who)
            for m, u in (("get", f"/poli/records/{r.pk}/letter/new/"), ("post", f"/poli/records/{r.pk}/letter/new/"), ("get", f"/poli/letters/{l.pk}.pdf")): self.assertEqual(getattr(self.client, m)(u).status_code, 403, (who, u))


class EmployeeDetailMedicalTests(PoliBase):
    def test_poli_sees_history_on_employee_detail_and_it_is_audited(self):
        from apps.poli import services
        e = Employee.objects.get(nik="002")
        services.create_record(self.poli, e, "kehamilan", "kontrol", {"kehamilan": {"hpht": "2026-08-01", "hpl": (date.today() + timedelta(days=60)).isoformat(), "usia_minggu": 20, "g": 2, "p": 1, "a": 0}}, self.diag, "", ())
        r0 = services.create_record(self.poli, e, "berobat", "pusing", {}, self.diag, "istirahat", ())[0]
        SickLeaveLetter.objects.create(record=r0, kind="izin_pulang", number="SIP/T/9")
        self.login("poli"); h = self.client.get(f"/employees/{e.pk}/").content.decode()
        for t in ("Riwayat poliklinik", "Riwayat berobat", "Surat izin", "SIP/T/9", "Hamil (HPL belum lewat)", "G/P/A 2/1/0"): self.assertIn(t, h)
        self.assertNotIn("pusing", h); self.assertNotIn("istirahat", h)  # keluhan/tindakan tidak ikut
        self.assertTrue(AuditLog.objects.filter(action="view_history").exists())

    def test_hrd_and_dept_admin_do_not_see_poli_history(self):
        from apps.poli import services
        e = Employee.objects.get(nik="001"); services.create_record(self.poli, e, "berobat", "rahasia", {}, self.diag, "", ())
        for who in ("hrd", "adm"):
            self.login(who); h = self.client.get(f"/employees/{e.pk}/").content.decode()
            self.assertNotIn("Riwayat poliklinik", h); self.assertNotIn("rahasia", h)
