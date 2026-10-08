"""Potongan BPJS (putaran 22, P4): input manual + impor memakai form yang sama, RBAC, anomali, ekspor tanpa nomor BPJS."""
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from apps.core.models import AuditLog
from . import services
from .models import BpjsDeduction
from .test_base import HrdBase

URLS = ["hrd_bpjs_deductions", "hrd_bpjs_deduction_new", "hrd_bpjs_deduction_import"]
HDR = "nik,scheme,period,employee_amount,employer_amount,note\n"


class DeductionBase(HrdBase):
    def setUp(self): self.login()

    def post(self, **kw):
        d = {"nik": "001", "scheme": "kes", "period": "2026-10", "employee_amount": "150.000", "employer_amount": "600.000", "note": ""}; d.update(kw)
        return self.client.post(reverse("hrd_bpjs_deduction_new"), d)

    def upload(self, text, mode="import", name="p.csv"):
        return self.client.post(reverse("hrd_bpjs_deduction_import"), {"file": SimpleUploadedFile(name, text.encode()), "mode": mode})


class AccessTests(DeductionBase):
    def test_only_hrd_and_superadmin(self):
        for who, code in (("hrd", 200), ("su", 200), ("adm", 403), ("poli", 403)):
            self.login(who)
            for u in URLS: self.assertEqual(self.client.get(reverse(u)).status_code, code, (who, u))
            self.assertEqual(self.client.post(reverse("hrd_bpjs_deduction_new"), {"nik": "001"}).status_code, 200 if code == 200 else 403)
        self.assertEqual(self.client.post(reverse("hrd_bpjs_deduction_import")).status_code, 403)

    def test_anonymous_redirected(self):
        self.client.logout()
        for u in URLS: self.assertEqual(self.client.get(reverse(u)).status_code, 302)

    def test_denied_roles_write_nothing(self):
        for who in ("adm", "poli"):
            self.login(who); self.post(); self.upload(HDR + "001,kes,2026-10,100000,0,\n")
        self.assertEqual(BpjsDeduction.objects.count(), 0)


class ManualTests(DeductionBase):
    def test_create_formats_and_audit(self):
        r = self.post(); self.assertEqual(r.status_code, 302)
        d = BpjsDeduction.objects.get(); self.assertEqual((d.employee_amount, d.employer_amount, d.period, d.created_by.username), (Decimal(150000), Decimal(600000), "2026-10", "hrd"))
        a = self.last_audit("bpjs_deduction_set"); self.assertEqual(a.after["period"], "2026-10")

    def test_same_key_replaces_not_duplicates(self):
        self.post(); self.post(employee_amount="175000")
        self.assertEqual(BpjsDeduction.objects.count(), 1); self.assertEqual(BpjsDeduction.objects.get().employee_amount, Decimal(175000))

    def test_schemes_and_periods_are_separate_rows(self):
        self.post(); self.post(scheme="tk"); self.post(period="2026-11")
        self.assertEqual(BpjsDeduction.objects.count(), 3)

    def test_validation(self):
        for bad in ({"period": "2026-13"}, {"period": "26-10"}, {"period": "2026-1"}, {"nik": "999"}, {"employee_amount": "-5"}, {"employee_amount": "1.5"},
                    {"scheme": "xx"}, {"period": "2023-12"}):  # terakhir: sebelum bulan masuk (2024-01)
            self.assertEqual(self.post(**bad).status_code, 200, bad)
        self.assertEqual(BpjsDeduction.objects.count(), 0)

    def test_inactive_employee_allowed_but_flagged(self):
        self.assertEqual(self.post(nik="009").status_code, 302)
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=dipotong")
        self.assertContains(r, "Mantan"); self.assertContains(r, "karyawan nonaktif")

    def test_inactive_employee_with_active_membership_is_still_flagged(self):
        from .models import BpjsMembership
        for e in (self.e_off, self.e1): BpjsMembership.objects.create(employee=e, scheme="kes", status="aktif", effective_date=self.today().replace(year=2024))
        self.post(nik="009")
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=dipotong"); self.assertContains(r, "Mantan"); self.assertContains(r, "karyawan nonaktif")
        self.post(nik="001"); self.assertNotContains(self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=dipotong"), "Budi")

    def test_audit_has_no_bpjs_numbers(self):
        self.post(); blob = str(list(AuditLog.objects.values_list("before", "after")))
        self.assertNotIn("0001234567890", blob); self.assertNotIn("TK-9988776655", blob)


class ImportTests(DeductionBase):
    def test_check_only_saves_nothing(self):
        r = self.upload(HDR + "001,kes,2026-10,150000,600000,\n", mode="check")
        self.assertContains(r, "valid"); self.assertEqual(BpjsDeduction.objects.count(), 0)

    def test_import_and_reimport_replaces(self):
        self.upload(HDR + "001,K,2026-10,150000,600000,\n002,tk,2026-10,\"1.000.000\",0,catatan\n")
        self.assertEqual(BpjsDeduction.objects.count(), 2); self.assertEqual(BpjsDeduction.objects.get(employee=self.e1).scheme, "kes")
        self.assertEqual(BpjsDeduction.objects.get(employee=self.e2).employee_amount, Decimal(1000000))
        self.upload(HDR + "001,kes,2026-10,160000,600000,\n")
        self.assertEqual(BpjsDeduction.objects.count(), 2); self.assertEqual(BpjsDeduction.objects.get(employee=self.e1).employee_amount, Decimal(160000))
        self.assertTrue(AuditLog.objects.filter(module="hrd", action="potongan-bpjs_import").exists())

    def test_all_or_nothing(self):
        r = self.upload(HDR + "001,kes,2026-10,150000,0,\n999,kes,2026-10,150000,0,\n")
        self.assertEqual(BpjsDeduction.objects.count(), 0); self.assertContains(r, "tidak ditemukan")

    def test_duplicate_key_in_file_and_formula_rejected(self):
        self.upload(HDR + "001,kes,2026-10,1,0,\n001,kes,2026-10,2,0,\n"); self.assertEqual(BpjsDeduction.objects.count(), 0)
        self.upload(HDR + "001,kes,2026-10,1,0,=HYPERLINK(\"x\")\n"); self.assertEqual(BpjsDeduction.objects.count(), 0)

    def test_unknown_scheme_rejected(self):
        self.upload(HDR + "001,jp,2026-10,1,0,\n"); self.assertEqual(BpjsDeduction.objects.count(), 0)

    def test_template_download(self):
        r = self.client.get("/hrd/bpjs/deductions/import/template.csv"); self.assertEqual(r.status_code, 200); self.assertIn(b"employee_amount", r.content)


class RecapTests(DeductionBase):
    def setUp(self):
        super().setUp()
        services.set_bpjs_status(self.e1, "kes", "aktif", self.today().replace(year=2024, month=2, day=1), "", self.hrd)
        services.set_bpjs_status(self.e2, "kes", "aktif", self.today().replace(year=2024, month=2, day=1), "", self.hrd)

    def test_totals_and_rupiah_format(self):
        self.post(); self.post(nik="002", employee_amount="1.250.000", employer_amount="0")
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10")
        self.assertContains(r, "Rp 1.400.000"); self.assertContains(r, "Rp 600.000"); self.assertNotContains(r, ",00")

    def test_missing_anomaly_lists_active_members_without_deduction(self):
        self.post()  # hanya e1
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=belum")
        self.assertContains(r, "Sari"); self.assertNotContains(r, "Budi")

    def test_not_active_member_flagged(self):
        self.post(nik="003")  # Dewi: tidak punya status K aktif
        self.assertContains(self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=dipotong"), "status program tidak aktif")

    def test_export_has_no_bpjs_number_and_is_audited(self):
        self.post()
        for q in ("", "&format=xlsx"):
            r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&export=1" + q); self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"0001234567890", self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&export=1").content)
        self.assertTrue(self.last_audit("bpjs_deduction_export"))

    def test_garbage_filters_do_not_500(self):
        for q in ("period=zzz", "department=abc", "scheme=%00", "anomaly=belum&period=bad", "page=999"):
            self.assertIn(self.client.get(reverse("hrd_bpjs_deductions") + "?" + q).status_code, (200, 400), q)

    def test_hub_and_status_page_link(self):
        self.assertContains(self.client.get("/hrd/"), "Potongan BPJS"); self.assertContains(self.client.get("/hrd/bpjs/"), "/hrd/bpjs/deductions/")
