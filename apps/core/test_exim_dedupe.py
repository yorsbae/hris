"""Putaran 58 — impor/ekspor tidak boleh menggandakan data: impor ulang nilai identik = 'tidak berubah' (tanpa baris kembar / jejak baru),
pengenal pribadi (KTP/BPJS/NPWP) tidak boleh terdaftar di dua karyawan, dan impor divalidasi ulang di dalam transaksi (anti-balapan)."""
import datetime
from decimal import Decimal as D
from unittest import mock

from django.test import TestCase

from apps.core import bulk
from apps.core.models import Role, User
from apps.hr import importer
from apps.hr.models import Department, Employee, Position
from apps.hrd.bulk_specs import BPJS_DEDUCTION
from apps.hrd.models import BpjsDeduction
from apps.payroll import bulk_specs as pbs
from apps.payroll.models import PayAdjustment, PayProfile, WageScale

D1 = datetime.date


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(code="PRD", name="Produksi"); cls.pos = Position.objects.create(name="Operator", level=1)
        cls.e = Employee.objects.create(nik="E001", name="Budi", gender="L", department=cls.dept, position=cls.pos, join_date=D1(2021, 4, 5))
        cls.user = User.objects.create_user("pay", password="Test#Pass-12345", role=Role.PAYROLL, must_change_password=False)

    def run_csv(self, spec, text, commit=True): return bulk.run(spec, text.encode(), "t.csv", commit, user=self.user)


class PayrollReimportTests(Base):
    PROFILE = "nik,berlaku_sejak,grade,subgrade,upah_harian_khusus,t_jabatan,t_pendidikan,t_khusus,t_lain,uang_makan,keterangan\nE001,2025-10-01,9,A,,1000,0,0,0,ya,\n"
    SCALE = "grade,subgrade,upah_harian,berlaku_sejak,keterangan\n9,A,84480.87,2025-01-01,UMK\n"
    ADJ = "nik,periode,komponen,nominal,jumlah,keterangan\nE001,2025-10,insentif,150000,,Target\n"

    def test_profile_reimport_is_noop(self):
        r1 = self.run_csv(pbs.PROFILE, self.PROFILE); self.assertEqual((r1["created"], r1["unchanged"]), (1, 0))
        r2 = self.run_csv(pbs.PROFILE, self.PROFILE); self.assertEqual((r2["created"], r2["updated"], r2["unchanged"]), (0, 0, 1))
        self.assertEqual(PayProfile.objects.count(), 1)           # tidak ada baris batal + baris kembar

    def test_profile_changed_value_replaces_once(self):
        self.run_csv(pbs.PROFILE, self.PROFILE); r = self.run_csv(pbs.PROFILE, self.PROFILE.replace(",1000,", ",2000,"))
        self.assertEqual((r["updated"], r["unchanged"]), (1, 0)); self.assertEqual((PayProfile.objects.count(), PayProfile.objects.filter(voided_at__isnull=True).count()), (2, 1))
        self.assertEqual(self.run_csv(pbs.PROFILE, self.PROFILE.replace(",1000,", ",2000,"))["unchanged"], 1); self.assertEqual(PayProfile.objects.count(), 2)

    def test_scale_reimport_is_noop_including_decimals(self):
        self.run_csv(pbs.SCALE, self.SCALE); r = self.run_csv(pbs.SCALE, self.SCALE)
        self.assertEqual((r["unchanged"], WageScale.objects.count()), (1, 1))
        self.assertEqual(self.run_csv(pbs.SCALE, self.SCALE.replace("84480.87", "84480.88"))["updated"], 1); self.assertEqual(WageScale.objects.count(), 2)

    def test_adjustment_reimport_is_noop(self):
        self.run_csv(pbs.ADJUSTMENT, self.ADJ); r = self.run_csv(pbs.ADJUSTMENT, self.ADJ)
        self.assertEqual((r["created"], r["unchanged"], PayAdjustment.objects.count()), (0, 1, 1))

    def test_check_only_mode_saves_nothing(self):
        r = self.run_csv(pbs.PROFILE, self.PROFILE, commit=False); self.assertTrue(r["ok"]); self.assertEqual(PayProfile.objects.count(), 0)

    def test_duplicate_key_inside_file_is_rejected(self):
        r = self.run_csv(pbs.PROFILE, self.PROFILE + self.PROFILE.splitlines()[1] + "\n"); self.assertFalse(r["ok"]); self.assertEqual(PayProfile.objects.count(), 0)


class BpjsReimportTests(Base):
    CSV = "nik,scheme,period,employee_amount,employer_amount,note\nE001,kes,2026-10,150000,600000,\n"

    def test_reimport_identical_is_unchanged_and_single_row(self):
        self.run_csv(BPJS_DEDUCTION, self.CSV); r = self.run_csv(BPJS_DEDUCTION, self.CSV)
        self.assertEqual((r["created"], r["updated"], r["unchanged"], BpjsDeduction.objects.count()), (0, 0, 1, 1))
        r = self.run_csv(BPJS_DEDUCTION, self.CSV.replace("150000", "175000")); self.assertEqual((r["updated"], BpjsDeduction.objects.count()), (1, 1)); self.assertEqual(BpjsDeduction.objects.get().employee_amount, D(175000))


class RaceRevalidationTests(Base):
    def test_commit_revalidates_after_lock(self):
        """Pembuat lain menyimpan baris yang sama di antara pratinjau dan simpan → validasi ulang di dalam transaksi menolak; tidak ada duplikat."""
        from apps.hrd.bulk_specs import SEPARATION
        csv = "nik,jenis,tgl_pengajuan,tgl_keluar,alasan,tali_asih,dasar_tali_asih\nE001,resign,2026-09-01,2026-09-30,Pindah,0,\n"
        from apps.hrd.models import Separation
        real = bulk._lock
        def sneaky(spec):
            real(spec)
            Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2026, 9, 30), emp_name="Budi", department_name="Produksi", join_date=self.e.join_date)
        with mock.patch.object(bulk, "_lock", sneaky): r = self.run_csv(SEPARATION, csv)
        self.assertFalse(r["ok"]); self.assertEqual(Separation.objects.count(), 1)


class EmployeeIdentifierDuplicateTests(Base):
    HDR = "nik,name,gender,join_date,department_code,nik_ktp,bpjs_kes,npwp\n"

    def imp(self, rows, commit=True): return importer.run((self.HDR + rows).encode(), commit, "k.csv")

    def test_same_ktp_in_file_is_rejected(self):
        r = self.imp("N1,A,L,2025-01-01,PRD,3301010101010001,,\nN2,B,P,2025-01-01,PRD,3301010101010001,,\n"); self.assertFalse(r["ok"]); self.assertEqual(Employee.objects.count(), 1)
        self.assertTrue(any("NIK KTP" in m for _, _, errs in r["errors"] for m in errs))

    def test_ktp_already_in_db_is_rejected_even_if_soft_deleted(self):
        Employee.objects.filter(pk=self.e.pk).update(nik_ktp="3301010101010009")
        r = self.imp("N3,C,L,2025-01-01,PRD,3301010101010009,,\n"); self.assertFalse(r["ok"]); self.assertIn("E001", str(r["errors"]))
        Employee.all_objects.filter(pk=self.e.pk).update(deleted_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        self.assertFalse(self.imp("N3,C,L,2025-01-01,PRD,3301010101010009,,\n")["ok"])

    def test_bpjs_and_npwp_normalised_before_compare(self):
        Employee.objects.filter(pk=self.e.pk).update(bpjs_kes="0001-2345-67890", npwp="12.345.678.9-012.345")
        self.assertFalse(self.imp("N4,D,L,2025-01-01,PRD,,000123456 7890,\n".replace("000123456 7890", "0001 2345 67890"))["ok"])
        self.assertFalse(self.imp("N5,E,L,2025-01-01,PRD,,,123456789012345\n")["ok"])

    def test_reimport_same_file_creates_nothing_new(self):
        rows = "N6,F,L,2025-01-01,PRD,3301010101010006,,\n"
        self.assertEqual(self.imp(rows)["created"], 1); r = self.imp(rows); self.assertFalse(r["ok"]); self.assertEqual(Employee.objects.filter(nik="N6").count(), 1)

    def test_blank_identifiers_never_collide(self):
        r = self.imp("N7,G,L,2025-01-01,PRD,,,\nN8,H,L,2025-01-01,PRD,,,\n"); self.assertTrue(r["ok"]); self.assertEqual(r["created"], 2)

    def test_manual_form_rejects_duplicate_identifier(self):
        from apps.hr.emp_forms import EmployeeForm
        Employee.objects.filter(pk=self.e.pk).update(nik_ktp="3301010101010001")
        f = EmployeeForm({"nik": "N9", "name": "I", "gender": "L", "join_date": "2025-01-01", "department": self.dept.pk, "status": "aktif", "nik_ktp": "3301010101010001", "gs_short": "14"})
        self.assertFalse(f.is_valid()); self.assertIn("nik_ktp", f.errors)
        own = EmployeeForm({"nik": "E001", "name": "Budi", "gender": "L", "join_date": "2021-04-05", "department": self.dept.pk, "status": "aktif", "nik_ktp": "3301010101010001", "gs_short": "14", "effective_date": "2026-01-01"}, instance=Employee.objects.get(pk=self.e.pk))
        own.is_valid(); self.assertNotIn("nik_ktp", own.errors)         # mengedit karyawan sendiri tidak dianggap duplikat
