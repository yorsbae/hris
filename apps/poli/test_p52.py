"""Putaran 52: (1) saran NIK akun Poli tidak membocorkan departemen lain di form pengajuan (tetap luas di form klinis);
(2) surat rujukan memuat NIK KTP di samping NIK karyawan."""
from datetime import date
from django.test import TestCase
from apps.core.models import Role, User
from apps.hr.models import ChangeRequest, Department, Employee, LeaveLedger, Position
from apps.poli import services
from apps.poli.pdf import referral_pdf
from apps.poli.test_letter_kop import pdf_text
from apps.poli.test_poli import PoliBase


class PoliLookupScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pol, cls.prd = Department.objects.create(code="POL", name="Poliklinik"), Department.objects.create(code="PRD", name="Produksi")
        pos = Position.objects.create(name="Staff")
        mk = lambda n, d: Employee.objects.create(nik=n, name=f"Emp {n}", gender="P", department=d, position=pos, join_date=date(2024, 1, 1))
        cls.p1, cls.x = mk("P01", cls.pol), mk("X01", cls.prd)
        for e in (cls.p1, cls.x): LeaveLedger.objects.create(employee=e, year=2026, kind="grant", days=12)
        cls.poli = User.objects.create_user("poli", password="x", role=Role.POLI, department=cls.pol)
        cls.nodept = User.objects.create_user("poli2", password="x", role=Role.POLI)
        cls.adm = User.objects.create_user("admprd", password="x", role=Role.DEPT_ADMIN, department=cls.prd)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)

    def niks(self, user, **extra):
        self.client.force_login(user)
        return {x["nik"] for x in self.client.get("/api/lookup/employees/", {"q": "01", **extra}).json()["results"]}

    def test_default_lookup_for_poli_is_own_department_only(self):
        self.assertEqual(self.niks(self.poli), {"P01"})

    def test_poli_cannot_widen_by_department_param_or_unknown_scope(self):
        self.assertEqual(self.niks(self.poli, department=self.prd.pk), set())
        self.assertEqual(self.niks(self.poli, scope="semua"), {"P01"})

    def test_clinical_forms_may_ask_scope_all_for_patients(self):
        self.assertEqual(self.niks(self.poli, scope="all"), {"P01", "X01"})

    def test_scope_all_is_ignored_for_department_admin_and_poli_without_department_fails_closed(self):
        self.assertEqual(self.niks(self.adm, scope="all"), {"X01"})
        self.assertEqual(self.niks(self.nodept), set())

    def test_hrd_still_sees_everyone(self):
        self.assertEqual(self.niks(self.hrd), {"P01", "X01"})

    def test_request_form_lookup_inputs_do_not_ask_for_wide_scope_but_poli_forms_do(self):
        self.client.force_login(self.poli)
        for url in ("/requests/new/", "/requests/new/?grp=izin_cuti"):
            h = self.client.get(url).content.decode(); self.assertIn('data-lookup="employee"', h); self.assertNotIn('data-q-scope="all"', h)
        h = self.client.get("/poli/records/new/").content.decode(); self.assertIn('data-q-scope="all"', h)

    def test_submitting_for_other_department_still_rejected(self):
        self.client.force_login(self.poli)
        r = self.client.post("/requests/new/", {"type": "cuti", "nik": "X01", "start_date": "2026-11-02", "end_date": "2026-11-03", "reason": "x", "submit": "1"})
        self.assertContains(r, "tidak ditemukan"); self.assertFalse(ChangeRequest.objects.exists())


class ReferralNikTests(PoliBase):
    def test_referral_letter_prints_nik_ktp_and_employee_nik(self):
        self.e1.nik_ktp = "3326010101900001"; self.e1.save()
        rec = services.create_record(self.poli, self.e1, "berobat", "Demam", {}, self.diag, "", (), doctor_name="dr. A")[0]
        t = pdf_text(referral_pdf(services.create_referral(self.poli, rec, "RSUD Batang", "Cek lab", purpose="berobat")))
        for need in ("NIK Karyawan", "NIK KTP", "3326010101900001", self.e1.nik): self.assertIn(need, t, need)

    def test_referral_letter_without_nik_ktp_shows_dash_not_error(self):
        self.e1.nik_ktp = ""; self.e1.save()
        rec = services.create_record(self.poli, self.e1, "berobat", "Demam", {}, self.diag, "", (), doctor_name="dr. A")[0]
        t = pdf_text(referral_pdf(services.create_referral(self.poli, rec, "RSUD Batang", purpose="berobat"))); self.assertIn("NIK KTP", t)
