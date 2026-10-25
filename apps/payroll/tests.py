"""Tes Payroll (putaran 57). Bagian 1: mesin hitung terhadap SLIP CONTOH (Okt-2025) dan alur run."""
import datetime
from decimal import Decimal as D

import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog, Role, User
from apps.hr.models import AttendanceDaily, Department, Employee, Position, Shift
from apps.hrd.models import BpjsDeduction, BpjsMembership, Separation, UniformPurchase, UniformSize, UniformType
from . import calc, engine, services
from .models import EducationAllowance, MealPeriod, MealRate, OvertimeRate, PayAdjustment, PayComponent, PayParam, PayProfile, PayrollRun, Payslip, WageScale

PERIOD = "2025-10"
D1 = datetime.date


def make_user(name, role, **kw): return User.objects.create_user(name, password="Test#Pass-12345", role=role, must_change_password=False, **kw)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(code="PRD", name="Produksi")
        cls.pos = Position.objects.create(name="Operator", level=1)
        cls.payroll = make_user("pay", Role.PAYROLL); cls.hrd = make_user("hrd1", Role.HRD); cls.hrd2 = make_user("hrd2", Role.HRD); cls.root = make_user("root", Role.SUPERADMIN, is_superuser=True, is_staff=True)

    def emp(self, nik="E001", join=D1(2021, 4, 5), status="aktif", dept=None, **kw):
        return Employee.objects.create(nik=nik, name=kw.pop("name", f"Karyawan {nik}"), gender="L", department=dept or self.dept, position=self.pos, join_date=join, status=status, education="SMP", **kw)

    def profile(self, e, grade=9, tier="A", on=D1(2021, 4, 1), **kw):
        return PayProfile.objects.create(employee=e, effective_from=on, grade=grade, tier=tier, **kw)

    def scale(self, wage="84480.87", grade=9, tier="A", on=D1(2021, 1, 1)): return WageScale.objects.create(grade=grade, tier=tier, daily_wage=D(wage), effective_from=on)

    def att(self, e, d, status="hadir", ot=0, notes="", **kw): return AttendanceDaily.objects.create(employee=e, date=d, status=status, overtime_minutes=ot, notes=notes, **kw)

    def sample_slip(self):
        """Karyawan persis seperti slip contoh: grade 9 A, masuk Apr-21, 4 tahun, Okt-25."""
        e = self.emp(join=D1(2021, 4, 5)); self.profile(e); self.scale()
        for i, m in enumerate([120, 120, 60, 60, 40]): self.att(e, D1(2025, 10, 1 + i), ot=m)           # lembur terbobot 3,5+3,5+1,5+1,5+1,0 = 11,0 jam
        for d in (D1(2025, 10, 17), D1(2025, 10, 20)): self.att(e, d, notes="Masuk di hari libur: Cuti bersama")   # 2 hari kerja di hari libur
        PayAdjustment.objects.create(employee=e, period=PERIOD, component=PayComponent.objects.get(code="pot_lain"), amount=42240, created_by=self.payroll)
        return e

    def item(self, slip, code): return slip.items.filter(code=code).first()


class CalcTests(TestCase):
    def test_rounding_matches_slip(self):
        self.assertEqual(calc.q0(D("84480.87") * 30), 2534426)
        self.assertEqual(calc.q0(calc.hourly_rate(D("84480.87"), 30, 173) * 11), 161148)
        self.assertEqual(calc.q0(D("84480.87") * 2), 168962)

    def test_percent_deductions_round_down_to_step(self):
        self.assertEqual(calc.floor_to(D("2534426") * D("0.03"), 10), 76030)
        self.assertEqual(calc.floor_to(D("2534426") * D("0.02"), 1), 50688)

    def test_weighted_overtime_hours(self):
        self.assertEqual(calc.weighted_hours([60], D("1.5"), D(2)), D("1.50"))
        self.assertEqual(calc.weighted_hours([120], D("1.5"), D(2)), D("3.50"))
        self.assertEqual(calc.weighted_hours([30, 0, -5, None], D("1.5"), D(2)), D("0.75"))
        self.assertEqual(calc.weighted_hours([120, 120, 60, 60, 40], D("1.5"), D(2)), D("11.00"))

    def test_paid_days_full_and_partial_month(self):
        s, e = calc.period_bounds("2026-02"); self.assertEqual(calc.paid_days(D1(2020, 1, 1), None, s, e, 30), 30)          # Februari 28 hari tetap 30
        s, e = calc.period_bounds("2025-10"); self.assertEqual(calc.paid_days(D1(2025, 10, 21), None, s, e, 30), 11)         # masuk tengah bulan
        self.assertEqual(calc.paid_days(D1(2020, 1, 1), D1(2025, 10, 10), s, e, 30), 10)                                       # keluar tengah bulan
        self.assertEqual(calc.paid_days(D1(2020, 1, 1), D1(2025, 9, 30), s, e, 30), 0)                                         # keluar sebelum periode

    def test_period_helpers(self):
        self.assertTrue(calc.valid_period("2026-10")); self.assertFalse(calc.valid_period("2026-13")); self.assertFalse(calc.valid_period("26-10")); self.assertFalse(calc.valid_period(None))
        self.assertEqual(calc.period_bounds("2024-02"), (D1(2024, 2, 1), D1(2024, 2, 29))); self.assertEqual(calc.period_bounds("2025-12")[1], D1(2025, 12, 31))
        self.assertEqual(calc.period_label("2025-10"), "Oct-25"); self.assertEqual(calc.full_years(D1(2021, 4, 5), D1(2025, 10, 31)), 4); self.assertEqual(calc.full_years(D1(2021, 11, 1), D1(2025, 10, 31)), 3)

    def test_indonesian_number_format(self): self.assertEqual(calc.fmt_qty(D("30"), 1), "30,0"); self.assertEqual(calc.fmt_qty(None), "")


class EngineSampleSlipTests(Base):
    def run_calc(self):
        run = services.create_run(PERIOD, self.payroll) if False else PayrollRun.objects.create(period=PERIOD, status="draft")   # periode lampau boleh dibuat langsung (create_run menolak hanya masa depan)
        return services.calculate_run(run.pk, self.payroll)

    def test_reproduces_sample_payslip_to_the_rupiah(self):
        e = self.sample_slip(); run = self.run_calc(); s = Payslip.objects.get(run=run, employee=e)
        self.assertEqual(self.item(s, "gaji_pokok").amount, 2534426); self.assertEqual(self.item(s, "gaji_pokok").qty, D("30.0"))
        self.assertEqual(self.item(s, "t_masa_kerja").amount, 12000)
        self.assertEqual(self.item(s, "lembur_overtime").amount, 161148); self.assertEqual(self.item(s, "lembur_overtime").qty, D("11.00"))
        self.assertEqual(self.item(s, "lembur_libur").amount, 168962); self.assertEqual(self.item(s, "lembur_libur").qty, 2)
        self.assertEqual(s.total_earnings, 2876536)
        self.assertEqual(self.item(s, "pot_jamsostek").amount, 76030); self.assertEqual(self.item(s, "pot_bpjs").amount, 50688); self.assertEqual(self.item(s, "pot_lain").amount, 42240)
        self.assertEqual(s.total_deductions, 168958); self.assertEqual(s.net_pay, 2707578)
        self.assertEqual((run.total_gross, run.total_deductions, run.total_net, run.n_slips), (2876536, 168958, 2707578, 1))

    def test_identity_and_params_are_copied(self):
        e = self.sample_slip(); run = self.run_calc(); s = Payslip.objects.get(run=run)
        self.assertEqual((s.nik, s.emp_name, s.department_name, s.grade_label, s.service_years, s.education), ("E001", e.name, "Produksi", "9 A", 4, "SMP"))
        self.assertEqual(run.params["hari_dasar"], "30") and self.assertEqual(run.params["pembagi_jam"], "173")
        Employee.objects.filter(pk=e.pk).update(name="Nama Baru"); self.assertEqual(Payslip.objects.get(pk=s.pk).emp_name, f"Karyawan E001")   # slip lama tidak bergeser

    def test_param_override_is_effective_dated_and_voidable(self):
        self.sample_slip()
        PayParam.objects.create(key="jamsostek_persen", value=D("2"), effective_from=D1(2025, 11, 1))      # berlaku SESUDAH periode → tak dipakai
        PayParam.objects.create(key="tunj_masa_kerja", value=D("5000"), effective_from=D1(2025, 1, 1))
        run = self.run_calc(); s = Payslip.objects.get(run=run)
        self.assertEqual(self.item(s, "pot_jamsostek").amount, 76030); self.assertEqual(self.item(s, "t_masa_kerja").amount, 20000)
        services.void_record(PayParam, PayParam.objects.get(key="tunj_masa_kerja").pk, "salah ketik", self.payroll)
        run = services.calculate_run(run.pk, self.payroll); self.assertEqual(self.item(Payslip.objects.get(run=run), "t_masa_kerja").amount, 12000)

    def test_missing_profile_and_scale_are_warned_not_silently_skipped(self):
        self.emp("E010")                                         # tanpa profil
        e = self.emp("E011"); self.profile(e, grade=7, tier="C")  # profil tanpa skala
        with self.assertRaises(ValueError) as cm: self.run_calc()   # semua karyawan terlewati → ditolak, alasan ada di pesan
        w = str(cm.exception); self.assertIn("belum punya profil gaji", w); self.assertIn("E010", w); self.assertIn("skala gajinya tidak ditemukan", w); self.assertIn("E011", w)

    def test_wage_override_beats_scale(self):
        e = self.emp(); PayProfile.objects.create(employee=e, effective_from=D1(2021, 4, 1), wage_override=D("100000"))
        s = Payslip.objects.get(run=self.run_calc()); self.assertEqual(self.item(s, "gaji_pokok").amount, 3_000_000)

    def test_newest_effective_profile_wins_and_future_profile_ignored(self):
        e = self.emp(); self.scale(); self.scale("90000", 9, "B", D1(2021, 1, 1))
        self.profile(e, tier="A", on=D1(2021, 4, 1)); self.profile(e, tier="B", on=D1(2025, 10, 1)); self.profile(e, tier="A", on=D1(2025, 11, 1), allowance_position=999_999)
        s = Payslip.objects.get(run=self.run_calc()); self.assertEqual(s.grade_label, "9 B"); self.assertIsNone(self.item(s, "t_jabatan"))

    def test_allowances_follow_profile(self):
        e = self.emp(); self.scale(); self.profile(e, allowance_position=100_000, allowance_education=50_000, meal_allowance=True)
        for i in range(10): self.att(e, D1(2025, 10, 1 + i))
        s = Payslip.objects.get(run=self.run_calc())
        self.assertEqual((self.item(s, "t_jabatan").amount, self.item(s, "t_pendidikan").amount), (100_000, 50_000))
        self.assertIsNone(self.item(s, "uang_makan"))   # bukan periode uang makan → Rp 0 walau berhak

    def test_meal_only_inside_meal_period_with_type_and_shift_rates(self):
        pagi, malam = Shift.objects.create(name="Pagi", code="PAGI", start="07:00", end="15:00"), Shift.objects.create(name="Malam", code="MALAM", start="23:00", end="07:00", crosses_midnight=True)
        MealPeriod.objects.create(name="Ramadan", start_date=D1(2025, 10, 10), end_date=D1(2025, 10, 19))
        on = D1(2021, 1, 1)
        MealRate.objects.create(pay_type="harian", amount=10_000, effective_from=on); MealRate.objects.create(pay_type="harian", shift=pagi, amount=0, effective_from=on)   # pagi tidak dapat
        MealRate.objects.create(pay_type="bulanan", amount=20_000, effective_from=on)
        self.scale()
        h, b = self.emp("H1"), self.emp("B1"); self.profile(h, meal_allowance=True, pay_type="harian"); self.profile(b, meal_allowance=True, pay_type="bulanan")
        for e in (h, b):
            for d in range(8, 13): self.att(e, D1(2025, 10, d), shift=malam if d % 2 else pagi)   # 8–9 di luar periode; 10,11,12 di dalam: pagi, malam, pagi
        run = self.run_calc(); sh, sb = Payslip.objects.get(run=run, employee=h), Payslip.objects.get(run=run, employee=b)
        self.assertEqual((self.item(sh, "uang_makan").amount, self.item(sh, "uang_makan").qty), (10_000, 1))   # hanya 1 hari malam; pagi tarif 0
        self.assertEqual((self.item(sb, "uang_makan").amount, self.item(sb, "uang_makan").qty), (60_000, 3))   # bulanan: tarif umum semua shift

    def test_no_meal_without_profile_flag(self):
        MealPeriod.objects.create(name="R", start_date=D1(2025, 10, 1), end_date=D1(2025, 10, 31)); MealRate.objects.create(pay_type="harian", amount=10_000, effective_from=D1(2021, 1, 1))
        e = self.emp(); self.scale(); self.profile(e); self.att(e, D1(2025, 10, 1)); self.assertIsNone(self.item(Payslip.objects.get(run=self.run_calc()), "uang_makan"))

    def test_flat_overtime_rate_per_hour_by_pay_type(self):
        self.scale(); h, b = self.emp("H1"), self.emp("B1"); self.profile(h, pay_type="harian"); self.profile(b, pay_type="bulanan")
        OvertimeRate.objects.create(pay_type="harian", amount=15_000, effective_from=D1(2021, 1, 1)); OvertimeRate.objects.create(pay_type="bulanan", amount=20_000, effective_from=D1(2021, 1, 1))
        for e in (h, b): self.att(e, D1(2025, 10, 1), ot=120); self.att(e, D1(2025, 10, 2), ot=30)   # 2,5 jam
        run = self.run_calc(); sh, sb = Payslip.objects.get(run=run, employee=h), Payslip.objects.get(run=run, employee=b)
        self.assertEqual((self.item(sh, "lembur_overtime").amount, self.item(sh, "lembur_overtime").qty), (37_500, D("2.50"))); self.assertEqual(self.item(sb, "lembur_overtime").amount, 50_000)

    def test_flat_standby_rate_and_education_master_with_profile_override(self):
        self.scale(); a, b = self.emp("A1"), self.emp("B1"); a.education = b.education = "S1"; a.save(); b.save()
        self.profile(a, pay_type="harian"); self.profile(b, allowance_education=75_000)
        EducationAllowance.objects.create(education="S1", amount=60_000, effective_from=D1(2021, 1, 1)); OvertimeRate.objects.create(kind="standby", pay_type="harian", amount=8_000, effective_from=D1(2021, 1, 1))
        self.att(a, D1(2025, 10, 1), standby_minutes=90)
        run = self.run_calc(); sa, sb = Payslip.objects.get(run=run, employee=a), Payslip.objects.get(run=run, employee=b)
        self.assertEqual(self.item(sa, "t_pendidikan").amount, 60_000); self.assertEqual(self.item(sb, "t_pendidikan").amount, 75_000)   # profil menang
        self.assertEqual((self.item(sa, "lembur_standby").amount, self.item(sa, "lembur_standby").qty), (12_000, D("1.50")))

    def test_alfa_deduction_and_standby(self):
        e = self.emp(); self.scale("90000"); self.profile(e)
        self.att(e, D1(2025, 10, 2), status="alfa"); self.att(e, D1(2025, 10, 3), status="alfa"); self.att(e, D1(2025, 10, 6), standby_minutes=120)
        s = Payslip.objects.get(run=self.run_calc())
        self.assertEqual(self.item(s, "pot_absensi").amount, 180_000)
        self.assertEqual(self.item(s, "lembur_standby").amount, calc.q0(D(2) * D(90000) * 30 / 173))

    def test_holiday_work_days_excluded_from_overtime(self):
        e = self.emp(); self.scale("86500"); self.profile(e)
        self.att(e, D1(2025, 10, 5), ot=240, notes="Masuk di hari libur: Minggu"); s = Payslip.objects.get(run=self.run_calc())
        self.assertIsNone(self.item(s, "lembur_overtime")); self.assertEqual(self.item(s, "lembur_libur").amount, 86500)

    def test_absensi_not_processed_is_flagged(self):
        e = self.emp(); self.scale(); self.profile(e); run = self.run_calc(); s = Payslip.objects.get(run=run)
        self.assertTrue(any("Absensi periode ini belum diolah" in w for w in run.warnings)); self.assertTrue(any("belum diolah" in f for f in s.flags))

    def test_unvalidated_days_are_flagged(self):
        e = self.emp(); self.scale(); self.profile(e); self.att(e, D1(2025, 10, 1)); self.att(e, D1(2025, 10, 2), status="tanpa_scan"); self.att(e, D1(2025, 10, 3), incomplete=True)
        f = " ".join(Payslip.objects.get(run=self.run_calc()).flags); self.assertIn("1 hari kerja tanpa scan", f); self.assertIn("1 hari scan tidak lengkap", f)


class EngineDeductionTests(Base):
    def setUp(self):
        self.e = self.emp(); self.scale(); self.profile(self.e); self.att(self.e, D1(2025, 10, 1))

    def calc(self): return Payslip.objects.get(run=services.calculate_run(PayrollRun.objects.get_or_create(period=PERIOD, defaults={"status": "draft"})[0].pk, self.payroll))

    def test_imported_bpjs_overrides_percent_and_inactive_membership_is_skipped(self):
        BpjsDeduction.objects.create(employee=self.e, scheme="kes", period=PERIOD, employee_amount=60_000)
        BpjsMembership.objects.create(employee=self.e, scheme="tk", status="nonaktif", effective_date=D1(2021, 4, 5))
        s = self.calc(); self.assertEqual((self.item(s, "pot_bpjs").amount, self.item(s, "pot_bpjs").source), (60_000, "bpjs")); self.assertIsNone(self.item(s, "pot_jamsostek"))
        self.assertTrue(any("BPJS TK nonaktif" in f for f in s.flags))

    def test_uniform_is_deducted_and_marked_only_when_paid(self):
        z, t = UniformSize.objects.get_or_create(code="L")[0], UniformType.objects.get_or_create(name="Seragam Kerja")[0]   # sudah disemai migrasi hrd/0007
        p = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 10, 3), utype=t, size=z, gender="L", quantity=2, rate_amount=19000, deduction_amount=38000)
        old = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 9, 3), utype=t, size=z, gender="L", quantity=1, rate_amount=19000, deduction_amount=19000, deduction_status="sudah", deducted_period="2025-09")
        s = self.calc(); self.assertEqual(self.item(s, "pot_seragam").amount, 38000); self.assertEqual(self.item(s, "pot_seragam").refs, [p.pk])
        p.refresh_from_db(); self.assertEqual(p.deduction_status, "belum"); old.refresh_from_db(); self.assertEqual(old.deducted_period, "2025-09")

    def test_manual_adjustments_and_signed_koreksi(self):
        for code, amt in (("insentif", 100_000), ("pot_pinjaman", 250_000)): services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code=code), amt, None, "", self.payroll)
        services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code="koreksi"), -20_000, None, "kelebihan bayar", self.payroll)
        s = self.calc(); self.assertEqual(self.item(s, "insentif").amount, 100_000); self.assertEqual(self.item(s, "pot_pinjaman").amount, 250_000); self.assertEqual(self.item(s, "koreksi").amount, -20_000)
        self.assertEqual(s.total_earnings, 2_534_426 + 3_000 * 4 + 100_000 - 20_000)

    def test_negative_net_is_flagged(self):
        services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code="pot_pinjaman"), 9_000_000, None, "", self.payroll)
        s = self.calc(); self.assertLess(s.net_pay, 0); self.assertTrue(any("NEGATIF" in f for f in s.flags))

    def test_leaver_mid_month_is_prorated_and_tali_asih_included(self):
        Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 10, 10), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=1_500_000, tali_asih_note="1 bulan")
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif")
        s = self.calc(); self.assertEqual(self.item(s, "gaji_pokok").qty, 10); self.assertEqual(self.item(s, "gaji_pokok").amount, calc.q0(D("84480.87") * 10))
        self.assertEqual(self.item(s, "tali_asih").amount, 1_500_000); self.assertEqual(self.item(s, "t_masa_kerja").amount, 4000)
        self.assertTrue(any("Keluar 10-10-2025" in f for f in s.flags))

    def test_leaver_before_period_gets_slip_only_for_pending_tali_asih(self):
        Separation.objects.create(employee=self.e, kind="phk", last_date=D1(2025, 8, 31), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=2_000_000)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif"); s = self.calc()
        self.assertEqual([i.code for i in s.items.all() if i.section != "potongan"], ["tali_asih"]); self.assertEqual(s.net_pay, 2_000_000)

    def test_leaver_before_period_without_tali_asih_is_not_paid(self):
        Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 8, 31), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif"); r = PayrollRun.objects.create(period=PERIOD, status="draft")
        with self.assertRaises(ValueError): services.calculate_run(r.pk, self.payroll)   # tidak ada slip → ditolak, bukan "Sudah dihitung" kosong
        r.refresh_from_db(); self.assertEqual((r.status, r.n_slips), ("draft", 0))

    def test_new_hire_mid_month_is_prorated(self):
        n = self.emp("E050", join=D1(2025, 10, 21)); self.profile(n, on=D1(2025, 10, 21)); run = services.calculate_run(PayrollRun.objects.create(period=PERIOD, status="draft").pk, self.payroll)
        s = Payslip.objects.get(run=run, employee=n); self.assertEqual(self.item(s, "gaji_pokok").qty, 11); self.assertIsNone(self.item(s, "t_masa_kerja") if calc.full_years(n.join_date, D1(2025, 10, 31)) == 0 else None)

    def test_inactive_without_separation_and_future_joiner_are_excluded(self):
        a = self.emp("E060", status="nonaktif"); self.profile(a); b = self.emp("E061", join=D1(2025, 11, 5)); self.profile(b, on=D1(2025, 11, 5))
        run = services.calculate_run(PayrollRun.objects.create(period=PERIOD, status="draft").pk, self.payroll); self.assertEqual(list(Payslip.objects.filter(run=run).values_list("nik", flat=True)), ["E001"])


class ZeroSlipTests(Base):
    """Run tanpa slip tidak boleh berstatus 'Sudah dihitung' (bug: Slip 0 / Rp 0 diam-diam)."""
    def setUp(self): self.e = self.emp(); self.run = PayrollRun.objects.create(period=PERIOD, status="draft")

    def test_no_profile_is_rejected_with_reason(self):
        with self.assertRaises(ValueError) as cm: services.calculate_run(self.run.pk, self.payroll)
        self.assertIn("belum punya profil gaji", str(cm.exception)); self.run.refresh_from_db(); self.assertEqual(self.run.status, "draft")

    def test_profile_without_scale_is_rejected_with_reason(self):
        self.profile(self.e)
        with self.assertRaises(ValueError) as cm: services.calculate_run(self.run.pk, self.payroll)
        self.assertIn("skala gajinya tidak ditemukan", str(cm.exception))

    def test_no_active_components_is_reported(self):
        self.profile(self.e); self.scale(); PayComponent.objects.update(active=False)
        with self.assertRaises(ValueError) as cm: services.calculate_run(self.run.pk, self.payroll)
        self.assertIn("komponen slip aktif", str(cm.exception))

    def test_complete_data_still_calculates(self):
        self.profile(self.e); self.scale(); r = services.calculate_run(self.run.pk, self.payroll)
        self.assertEqual((r.status, r.n_slips), ("dihitung", 1))


class RunWorkflowTests(Base):
    """Alur putaran 59: Payroll berdiri sendiri — buat (setelah HRD menutup periode) → hitung → tetapkan → bayar."""
    def setUp(self):
        self.e = self.emp(); self.scale(); self.profile(self.e); self.att(self.e, D1(2025, 10, 1))
        self.run = PayrollRun.objects.create(period=PERIOD)       # draft (disiapkan HRD)

    def to_final(self):
        services.calculate_run(self.run.pk, self.payroll); return services.finalize_run(self.run.pk, self.payroll)

    def test_happy_path_and_events(self):
        self.to_final(); r = services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        self.assertEqual(r.status, "dibayar"); self.assertEqual(list(r.events.values_list("action", flat=True)), ["hitung", "tetapkan", "bayar"])
        self.assertTrue(r.locked); self.assertEqual(r.finalized_by, self.payroll)

    def test_create_run_requires_period_closed_by_hrd(self):
        from apps.hr.models import PeriodClose
        PayrollRun.objects.all().delete()
        with self.assertRaisesMessage(ValueError, "HRD belum menutup periode"): services.create_run(PERIOD, self.payroll)
        PeriodClose.objects.create(period=PERIOD, start_date=D1(2025, 9, 26), end_date=D1(2025, 10, 25))
        self.assertEqual(services.create_run(PERIOD, self.payroll).status, "draft")

    def test_run_uses_the_date_range_closed_by_hrd(self):
        from apps.hr.models import PeriodClose
        from apps.payroll import calc
        self.assertEqual(calc.data_bounds(PERIOD), (D1(2025, 10, 1), D1(2025, 10, 31)))
        PeriodClose.objects.create(period=PERIOD, start_date=D1(2025, 9, 26), end_date=D1(2025, 10, 25))
        self.assertEqual(calc.data_bounds(PERIOD), (D1(2025, 9, 26), D1(2025, 10, 25)))

    def test_cannot_skip_steps(self):
        with self.assertRaisesMessage(ValueError, "ditetapkan"): services.finalize_run(self.run.pk, self.payroll)               # belum dihitung
        services.calculate_run(self.run.pk, self.payroll)
        with self.assertRaisesMessage(ValueError, "sudah ditetapkan"): services.pay_run(self.run.pk, datetime.date.today(), self.payroll)

    def test_reopen_finalized_run_for_recalculation(self):
        self.to_final(); self.assertEqual(services.reopen_run(self.run.pk, self.payroll).status, "dihitung")
        self.assertEqual(services.finalize_run(self.run.pk, self.payroll).status, "ditetapkan")

    def test_locked_runs_cannot_be_recalculated_or_cancelled_after_payment(self):
        self.to_final()
        with self.assertRaisesMessage(ValueError, "tidak dapat dihitung ulang"): services.calculate_run(self.run.pk, self.payroll)
        services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        with self.assertRaisesMessage(ValueError, "sudah dibayar"): services.cancel_run(self.run.pk, "oops", self.payroll)

    def test_stale_data_blocks_finalize_until_recalculated(self):
        services.calculate_run(self.run.pk, self.payroll)
        services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code="insentif"), 50_000, None, "", self.payroll)
        self.assertTrue(services.run_is_stale(PayrollRun.objects.get(pk=self.run.pk)))
        with self.assertRaisesMessage(ValueError, "Hitung ulang dulu"): services.finalize_run(self.run.pk, self.payroll)
        services.calculate_run(self.run.pk, self.payroll); services.finalize_run(self.run.pk, self.payroll)

    def test_one_live_run_per_period_but_cancelled_can_be_recreated(self):
        with self.assertRaisesMessage(ValueError, "sudah ada"): services.create_run(PERIOD, self.payroll, require_closed=False)
        services.cancel_run(self.run.pk, "Salah periode", self.payroll); self.assertEqual(services.create_run(PERIOD, self.payroll, require_closed=False).status, "draft")

    def test_create_run_validates_period(self):
        for bad in ("", "2025-13", "abc"):
            with self.assertRaises(ValueError): services.create_run(bad, self.payroll, require_closed=False)
        with self.assertRaisesMessage(ValueError, "masa depan"): services.create_run("2099-01", self.payroll, require_closed=False)

    def test_pay_date_cannot_be_future_or_missing(self):
        self.to_final()
        with self.assertRaisesMessage(ValueError, "masa depan"): services.pay_run(self.run.pk, datetime.date.today() + datetime.timedelta(days=1), self.payroll)
        with self.assertRaisesMessage(ValueError, "wajib"): services.pay_run(self.run.pk, None, self.payroll)

    def test_paying_marks_uniform_deducted_and_tali_asih_paid_atomically(self):
        z, t = UniformSize.objects.get_or_create(code="L")[0], UniformType.objects.get_or_create(name="Seragam Kerja")[0]   # sudah disemai migrasi hrd/0007
        p = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 10, 3), utype=t, size=z, gender="L", quantity=1, rate_amount=19000, deduction_amount=19000)
        sep = Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 10, 20), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=1_000_000)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif")
        self.to_final(); services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        p.refresh_from_db(); sep.refresh_from_db(); self.assertEqual((p.deduction_status, p.deducted_period), ("sudah", PERIOD)); self.assertEqual(sep.tali_asih_paid_on, datetime.date.today())

    def test_payment_rolls_back_everything_if_a_reference_went_stale(self):
        z, t = UniformSize.objects.get_or_create(code="L")[0], UniformType.objects.get_or_create(name="Seragam Kerja")[0]   # sudah disemai migrasi hrd/0007
        p = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 10, 3), utype=t, size=z, gender="L", quantity=1, rate_amount=19000, deduction_amount=19000)
        sep = Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 10, 20), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=1_000_000)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif")
        self.to_final()
        UniformPurchase.objects.filter(pk=p.pk).update(deduction_status="sudah", deducted_period="2025-10")   # dipotong di tempat lain setelah ditetapkan
        with self.assertRaisesMessage(ValueError, "Sudah ditandai dipotong"): services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        self.run.refresh_from_db(); sep.refresh_from_db(); self.assertEqual(self.run.status, "ditetapkan"); self.assertIsNone(sep.tali_asih_paid_on)

    def test_adjustments_locked_once_run_finalized(self):
        self.to_final(); comp = PayComponent.objects.get(code="insentif")
        with self.assertRaisesMessage(ValueError, "tarik atau batalkan"): services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll)
        services.reopen_run(self.run.pk, self.payroll); a = services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll); self.assertEqual(a.amount, 1000)

    def test_adjustment_replaces_not_overwrites(self):
        comp = PayComponent.objects.get(code="insentif"); a1 = services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll); a2 = services.save_adjustment(self.e, PERIOD, comp, 2000, None, "", self.payroll)
        a1.refresh_from_db(); self.assertEqual((a1.is_void, a1.void_reason, a2.amount), (True, "Diganti nilai baru", 2000)); self.assertEqual(PayAdjustment.objects.filter(voided_at__isnull=True).count(), 1)

    def test_adjustment_form_multi_rows_for_one_employee(self):
        self.client.force_login(self.payroll); url = reverse("pay_adjustment_new")
        r = self.client.get(url, {"period": PERIOD}); self.assertContains(r, "Tambah baris"); self.assertContains(r, "Simpan semua"); self.assertNotContains(r, "Gaji Pokok")      # komponen otomatis tidak ditawarkan
        mat, pin, ins, pre = (PayComponent.objects.get(code=c) for c in ("pot_materai", "pot_pinjaman", "insentif", "premi_produksi"))
        base = {"nik": self.e.nik, "period": PERIOD}
        r = self.client.post(url, {**base, "kind": ["potongan", "tambah"], "component": [mat.pk, mat.pk], "amount": ["6000", "1000"], "note": ["", ""]}); self.assertContains(r, "bukan tambahan")          # jenis ≠ komponen
        r = self.client.post(url, {**base, "kind": ["potongan", "potongan"], "component": [mat.pk, mat.pk], "amount": ["6000", "1000"], "note": ["", ""]}); self.assertContains(r, "sudah ada di baris lain")
        r = self.client.post(url, {**base, "kind": ["potongan", "tambah"], "component": [mat.pk, pre.pk], "amount": ["6000", ""], "note": ["", ""]}); self.assertEqual(r.status_code, 200)   # satu baris salah → tidak ada yang tersimpan
        self.assertEqual(PayAdjustment.objects.filter(employee=self.e, period=PERIOD).count(), 0)
        r = self.client.post(url, {**base, "kind": ["potongan", "potongan", "tambah"], "component": [mat.pk, pin.pk, ins.pk], "amount": ["6.000", "250.000", "100.000"], "note": ["", "kasbon", "bonus-xyz"]})
        self.assertEqual(r.status_code, 302); rows = PayAdjustment.objects.filter(employee=self.e, period=PERIOD, voided_at__isnull=True)
        self.assertEqual(sorted(rows.values_list("component__code", flat=True)), ["insentif", "pot_materai", "pot_pinjaman"]); self.assertEqual(rows.get(component=pin).note, "kasbon")      # banyak baris, satu karyawan
        r = self.client.post(url, {**base, "kind": ["tambah"], "component": [ins.pk], "amount": ["-5"], "note": [""]}); self.assertContains(r, "tanpa tanda minus")
        self.assertContains(self.client.post(url, {**base, "kind": [], "component": [], "amount": []}), "minimal satu baris")
        self.assertContains(self.client.post(url, {"nik": "TIDAKADA", "period": PERIOD, "kind": ["tambah"], "component": [ins.pk], "amount": ["1000"], "note": [""]}), "tidak ditemukan")
        r = self.client.get(url, {"period": PERIOD, "nik": self.e.nik}); self.assertContains(r, "Sudah tercatat"); self.assertContains(r, "Materai")
        r = self.client.get(reverse("pay_adjustments"), {"period": PERIOD}); self.assertContains(r, "Materai"); self.assertContains(r, "Insentif"); self.assertContains(r, "bersih")                       # satu baris per karyawan, rincian + / −
        r = self.client.get(reverse("pay_adjustments"), {"period": PERIOD, "kind": "potongan"}); self.assertContains(r, "kasbon"); self.assertNotContains(r, "bonus-xyz")      # tab Potongan: catatan insentif (tambahan) tidak tampil

    def test_adjustment_rules(self):
        c = PayComponent.objects.get(code="insentif")
        with self.assertRaisesMessage(ValueError, "negatif"): services.save_adjustment(self.e, PERIOD, c, -5, None, "", self.payroll)
        with self.assertRaisesMessage(ValueError, "0"): services.save_adjustment(self.e, PERIOD, c, 0, None, "", self.payroll)
        with self.assertRaisesMessage(ValueError, "manual"): services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code="gaji_pokok"), 5, None, "", self.payroll)
        with self.assertRaises(ValueError): services.save_adjustment(self.e, "2025-99", c, 5, None, "", self.payroll)

    def test_records_cannot_be_deleted(self):
        for obj in (self.run, PayComponent.objects.first(), WageScale.objects.first(), PayProfile.objects.first()):
            with self.assertRaises(PermissionError): obj.delete()

    def test_events_are_append_only(self):
        services.calculate_run(self.run.pk, self.payroll); ev = self.run.events.first()
        ev.note = "x"
        with self.assertRaises(PermissionError): ev.save()
        with self.assertRaises(PermissionError): ev.delete()

    def test_closing_a_period_notifies_payroll_not_hrd(self):
        from apps.core.models import Notification
        from apps.hr import periods
        PayrollRun.objects.all().delete()
        periods.close(D1(2025, 10, 1), D1(2025, 10, 31), PERIOD, self.hrd, force=True)
        self.assertTrue(Notification.objects.filter(user=self.payroll, kind="payroll", title__contains="ditutup").exists()); self.assertFalse(Notification.objects.filter(user=self.hrd).exists())


class ViewAccessTests(Base):
    """Tes tampilan + RBAC (putaran 59): Payroll/Superadmin penuh; HRD, Admin Dept & Poli 403 di seluruh halaman Payroll."""
    def setUp(self):
        self.e = self.emp(); self.scale(); self.profile(self.e); self.att(self.e, D1(2025, 10, 1), ot=60)
        self.run = PayrollRun.objects.create(period=PERIOD, status="draft"); services.calculate_run(self.run.pk, self.payroll)
        self.slip = self.run.slips.first()
        self.dept_admin = make_user("da", Role.DEPT_ADMIN, department=self.dept); self.poli = make_user("pl", Role.POLI)
        self.pages = ["pay_home", "pay_runs", "pay_profiles", "pay_profile_new", "pay_scale", "pay_scale_new", "pay_rates", "pay_meal_period_new", "pay_meal_rate_new", "pay_ot_rate_new", "pay_edu_allow_new", "pay_params", "pay_param_new", "pay_adjustments", "pay_adjustment_new", "pay_tali", "pay_tali_rule_new"]

    def get(self, user, name, *a, **kw):
        self.client.force_login(user); return self.client.get(reverse(name, args=a), **kw)

    def test_payroll_and_superadmin_open_every_page(self):
        for u in (self.payroll, self.root):
            for n in self.pages: self.assertEqual(self.get(u, n).status_code, 200, (u.username, n))
            for n, a in [("pay_run", (self.run.pk,)), ("pay_slip", (self.slip.pk,)), ("pay_slip_pdf", (self.slip.pk,)), ("pay_run_pdf", (self.run.pk,)), ("pay_profile_detail", (self.e.pk,))]:
                self.assertEqual(self.get(u, n, *a).status_code, 200, (u.username, n))

    def test_run_exports(self):
        for fmt in ("xlsx", "csv"): self.assertEqual(self.get(self.payroll, "pay_run", self.run.pk, data={"export": fmt}).status_code, 200)
        self.assertEqual(self.get(self.payroll, "pay_slip_pdf", self.slip.pk)["Content-Type"], "application/pdf")

    def test_hrd_has_no_payroll_access(self):
        for n in self.pages: self.assertEqual(self.get(self.hrd, n).status_code, 403, n)
        self.assertEqual(self.get(self.hrd, "pay_run", self.run.pk).status_code, 403); self.assertEqual(self.get(self.hrd, "pay_slip", self.slip.pk).status_code, 403)

    def test_other_roles_denied_everywhere(self):
        for u in (self.dept_admin, self.poli):
            for n in self.pages: self.assertEqual(self.get(u, n).status_code, 403, (u.username, n))
            self.assertEqual(self.get(u, "pay_run", self.run.pk).status_code, 403); self.assertEqual(self.get(u, "pay_slip", self.slip.pk).status_code, 403)

    def test_anonymous_redirected(self):
        self.client.logout(); self.assertEqual(self.client.get(reverse("pay_runs")).status_code, 302)

    def test_workflow_through_http(self):
        self.run.status = "draft"; self.run.save()
        a = lambda u, act, **d: (self.client.force_login(u), self.client.post(reverse("pay_run_action", args=[self.run.pk, act]), d))[1]   # noqa: E731
        st = lambda: PayrollRun.objects.get(pk=self.run.pk).status   # noqa: E731
        a(self.hrd, "calculate"); self.assertEqual(st(), "draft")                       # HRD tidak punya akses
        a(self.payroll, "handover"); self.assertEqual(st(), "draft")                    # tahap serah-terima run sudah tidak ada
        a(self.payroll, "calculate"); self.assertEqual(st(), "dihitung")
        a(self.hrd, "finalize"); self.assertEqual(st(), "dihitung")
        a(self.payroll, "finalize"); self.assertEqual(st(), "ditetapkan")
        a(self.hrd, "pay", paid_on="2025-11-01"); self.assertEqual(st(), "ditetapkan")
        a(self.payroll, "pay", paid_on="2025-11-01"); self.assertEqual(st(), "dibayar")

    def test_payroll_creates_runs_only_after_hrd_closes_the_period(self):
        from apps.hr.models import PeriodClose
        PayrollRun.objects.update(status="dibatalkan")
        self.client.force_login(self.hrd); self.assertEqual(self.client.post(reverse("pay_runs"), {"period": PERIOD}).status_code, 403)
        self.client.force_login(self.payroll); r = self.client.post(reverse("pay_runs"), {"period": PERIOD}); self.assertEqual(r.status_code, 200); self.assertContains(r, "HRD belum menutup periode")
        PeriodClose.objects.create(period=PERIOD, start_date=D1(2025, 9, 26), end_date=D1(2025, 10, 25))
        self.assertEqual(self.client.post(reverse("pay_runs"), {"period": PERIOD}).status_code, 302)
        self.assertEqual(PayrollRun.objects.exclude(status="dibatalkan").get().status, "draft")

    def test_periods_page_lists_hrd_closings_with_download(self):
        from apps.hr import periods
        PayrollRun.objects.update(status="dibatalkan")
        row, _ = periods.close(D1(2025, 9, 26), D1(2025, 10, 25), PERIOD, self.hrd, force=True)
        r = self.get(self.payroll, "pay_periods"); self.assertContains(r, "26-09-2025"); self.assertContains(r, reverse("att_period_download", args=[row.pk]))
        self.assertEqual(self.client.get(reverse("att_period_download", args=[row.pk])).status_code, 200)

    def test_get_does_not_mutate(self):
        self.get(self.payroll, "pay_run_action", self.run.pk, "finalize"); self.assertEqual(PayrollRun.objects.get(pk=self.run.pk).status, "dihitung")


class SeedPayrollDemoTests(Base):
    def test_seed_makes_slips_and_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        for i in range(3): self.emp(f"S{i}")
        out = StringIO(); call_command("seed_payroll_demo", "--periods", PERIOD, stdout=out)
        run = PayrollRun.objects.get(period=PERIOD); self.assertEqual(run.n_slips, 3); self.assertEqual(Payslip.objects.filter(run=run).count(), 3); self.assertGreater(run.total_net, 0)
        n = (PayProfile.objects.count(), WageScale.objects.count()); call_command("seed_payroll_demo", "--periods", PERIOD, stdout=StringIO())
        self.assertEqual((PayProfile.objects.count(), WageScale.objects.count()), n)


    def test_seed_recreates_components_wiped_by_flush(self):
        """`manage.py flush` menghapus komponen slip (dibuat migrasi). Seed harus membuatnya ulang, bukan menghasilkan run kosong."""
        from django.core.management import call_command
        from io import StringIO
        from .defaults import ensure_components
        self.emp("F1"); PayComponent.objects.all().delete()
        out = StringIO(); call_command("seed_payroll_demo", "--periods", PERIOD, stdout=out)
        self.assertIn("Komponen slip dibuat ulang", out.getvalue()); self.assertEqual(PayrollRun.objects.get(period=PERIOD).n_slips, 1)
        n = PayComponent.objects.count(); self.assertGreater(n, 20); self.assertEqual(ensure_components(), 0); self.assertEqual(PayComponent.objects.count(), n)   # idempoten

    def test_ensure_components_does_not_overwrite_edits(self):
        from .defaults import ensure_components
        c = PayComponent.objects.get(code="gaji_pokok"); PayComponent.objects.filter(pk=c.pk).update(label="Gaji Dasar", active=False); ensure_components()
        c.refresh_from_db(); self.assertEqual((c.label, c.active), ("Gaji Dasar", False))


class ReadinessAndCompareTests(Base):
    def run_calc(self): return services.calculate_run(PayrollRun.objects.create(period=PERIOD, status="draft").pk, self.payroll)

    def test_readiness_flags_missing_profile_and_scale_then_clears(self):
        from . import readiness as RD
        e = self.emp("R1"); lv = lambda: {i["title"]: i["level"] for i in RD.readiness(PERIOD)}
        self.assertEqual(lv()["Karyawan aktif tanpa Profil Gaji"], "bad")
        self.profile(e, grade=7, tier="C"); self.assertEqual(lv()["Grade/tingkat belum ada di Skala Gaji"], "bad")
        self.scale(grade=7, tier="C"); l = lv(); self.assertEqual(l["Semua karyawan aktif punya Profil Gaji"], "ok"); self.assertEqual(l["Skala gaji mencakup semua profil"], "ok")
        self.assertEqual(RD.summary(RD.readiness(PERIOD))["bad"], 0)

    def test_run_detail_shows_readiness_and_comparison(self):
        e = self.emp("R2"); self.profile(e); self.scale()
        prev = services.calculate_run(PayrollRun.objects.create(period="2025-09", status="draft").pk, self.payroll)
        cur = self.run_calc(); Payslip.objects.filter(run=cur).update(net_pay=D("9000000"))   # lonjakan besar
        PayrollRun.objects.filter(pk=cur.pk).update(total_net=D("9000000"))
        self.client.force_login(self.payroll); r = self.client.get(reverse("pay_run", args=[cur.pk])); self.assertContains(r, "Kesiapan data"); self.assertContains(r, "Dibanding 2025-09"); self.assertContains(r, "1 berubah besar")

    def test_comparison_new_and_gone(self):
        from . import readiness as RD
        a, b = self.emp("R3"), self.emp("R4"); self.profile(a); self.profile(b); self.scale()
        services.calculate_run(PayrollRun.objects.create(period="2025-09", status="draft").pk, self.payroll)
        Employee.objects.filter(pk=a.pk).update(status="nonaktif"); c = self.emp("R5"); self.profile(c)
        cur = self.run_calc(); cmp = RD.comparison(cur)
        self.assertEqual((cmp["n_new"], cmp["n_gone"]), (1, 1)); self.assertEqual(cmp["new"][0].nik, "R5"); self.assertEqual(cmp["gone"][0].nik, "R3")

    def test_first_run_has_no_comparison(self):
        from . import readiness as RD
        e = self.emp("R6"); self.profile(e); self.scale(); self.assertIsNone(RD.comparison(self.run_calc()))



@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReportTests(Base):
    """Laporan XLSX Payroll (putaran 60): 8 sheet, arsip saat dibayar, akses hanya Payroll/Superadmin, unduhan diaudit."""
    def setUp(self):
        self.e = self.emp(); self.scale(); self.profile(self.e); self.att(self.e, D1(2025, 10, 1), ot=60)
        self.e.bank_name = "BCA"; self.e.bank_account = "1234567890"; self.e.save()
        self.run = PayrollRun.objects.create(period=PERIOD, status="draft"); services.calculate_run(self.run.pk, self.payroll)

    def sheets(self, resp):
        import io
        from openpyxl import load_workbook
        return load_workbook(io.BytesIO(b"".join(resp.streaming_content) if resp.streaming else resp.content))

    def test_report_has_all_sheets_with_totals_and_bank(self):
        from . import report
        self.client.force_login(self.payroll); r = self.client.get(reverse("pay_run_report", args=[self.run.pk])); self.assertEqual(r.status_code, 200)
        wb = self.sheets(r); self.assertEqual(wb.sheetnames, list(report.SHEETS))
        rekap = list(wb["Rekap Gaji"].iter_rows(values_only=True)); self.assertEqual(rekap[1][0], self.e.nik); self.assertEqual(rekap[1][15], int(Payslip.objects.get(run=self.run).net_pay))
        bank = list(wb["Transfer Bank"].iter_rows(values_only=True)); self.assertEqual((bank[1][3], bank[1][4]), ("BCA", "1234567890"))
        self.assertGreater(wb["Rincian Komponen"].max_row, 2); self.assertEqual(wb["Per Departemen"].max_row, 2)
        self.assertTrue(AuditLog.objects.filter(module="payroll", action="run_report").exists())

    def test_paying_archives_a_report_and_download_serves_it(self):
        import datetime
        services.finalize_run(self.run.pk, self.payroll); services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        run = PayrollRun.objects.get(pk=self.run.pk); self.assertTrue(run.report_file); self.assertIsNotNone(run.report_at)
        self.client.force_login(self.payroll); self.assertEqual(self.sheets(self.client.get(reverse("pay_run_report", args=[run.pk]))).sheetnames[0], "Ringkasan")
        self.assertEqual(self.client.get(reverse("pay_run_report", args=[run.pk]), {"fresh": 1}).status_code, 200)

    def test_only_payroll_and_superadmin(self):
        for u, code in ((self.hrd, 403), (self.root, 200), (self.payroll, 200)):
            self.client.force_login(u); self.assertEqual(self.client.get(reverse("pay_run_report", args=[self.run.pk])).status_code, code, u.username)

    def test_empty_run_redirects(self):
        r2 = PayrollRun.objects.create(period="2025-09", status="draft"); self.client.force_login(self.payroll)
        self.assertEqual(self.client.get(reverse("pay_run_report", args=[r2.pk])).status_code, 302)


class TaxUnitTests(TestCase):
    """PPh 21 (putaran 61): fungsi murni + data TER resmi."""
    def test_ter_spot_checks(self):
        from . import tax, ter_data
        A, B, C = (ter_data.TABLES[k] for k in "ABC")
        self.assertEqual(tax.ter_rate(A, D(5_400_000)), D(0)); self.assertEqual(tax.pph_ter(D(10_000_000), tax.ter_rate(A, D(10_000_000))), 200_000)
        self.assertGreater(tax.ter_rate(B, D(20_000_000)), 0); self.assertEqual(tax.ter_rate(C, D(1_000_000)), 0)
        self.assertEqual(tax.ter_rate(A, D(2_000_000_000)), D(34))
        for t in (A, B, C): self.assertIsNone(t[-1][0]); self.assertEqual([r[0] for r in t[:-1]], sorted(r[0] for r in t[:-1]))

    def test_surcharge_and_floor(self):
        from . import tax
        self.assertEqual(tax.pph_ter(D(10_000_000), D(2), D("1.2")), 240_000); self.assertEqual(tax.pph_ter(D(1_234_567), D("1.5")), 18_518)

    def test_pasal17_brackets(self):
        from . import tax
        self.assertEqual(tax.pasal17(D(60_000_000)), 3_000_000); self.assertEqual(tax.pasal17(D(250_000_000)), 3_000_000 + 28_500_000)
        self.assertEqual(tax.pasal17(D(0)), 0)

    def test_annual_tax_known_case(self):
        from . import tax
        r = tax.annual_tax(D(120_000_000), D(0), D(54_000_000), 12)   # 10 jt/bln, TK/0
        self.assertEqual((r["biaya_jabatan"], r["neto"], r["pkp"], r["pajak"]), (6_000_000, 114_000_000, 60_000_000, 3_000_000))
        self.assertEqual(tax.annual_tax(D(120_000_000), D(0), D(54_000_000), 12, D("1.2"))["pajak"], 3_600_000)
        self.assertEqual(tax.annual_tax(D(30_000_000), D(0), D(54_000_000), 12)["pajak"], 0)


class ThrAndPagesTests(Base):
    def setUp(self):
        self.e = self.emp(join=D1(2024, 1, 10)); self.scale(wage="100000"); self.profile(self.e, allowance_position=500_000)

    def test_thr_formula_prorata_and_full(self):
        from . import thr
        r = thr.compute(D1(2025, 3, 31), "2025-03")[0]; self.assertEqual((r["months"], r["monthly"], r["amount"]), (14, D(3_500_000), D(3_500_000)))
        e2 = self.emp(nik="E002", join=D1(2024, 10, 10)); self.profile(e2)
        r2 = next(x for x in thr.compute(D1(2025, 3, 31), "2025-03") if x["emp"] == e2); self.assertEqual((r2["months"], r2["amount"]), (5, D(3_000_000 * 5 // 12)))
        e3 = self.emp(nik="E003", join=D1(2025, 3, 20)); self.profile(e3); self.assertEqual(next(x for x in thr.compute(D1(2025, 3, 31), "2025-03") if x["emp"] == e3)["amount"], 0)

    def test_thr_page_pushes_adjustments_and_replaces(self):
        self.client.force_login(self.payroll); u = reverse("pay_thr")
        self.assertEqual(self.client.get(u, {"on": "2025-03-31", "period": "2025-03"}).status_code, 200)
        self.client.post(u, {"on": "2025-03-31", "period": "2025-03"}); a = PayAdjustment.objects.get(employee=self.e, period="2025-03", component__code="thr", voided_at__isnull=True); self.assertEqual(a.amount, 3_500_000)
        self.client.post(u, {"on": "2025-03-31", "period": "2025-03"}); self.assertEqual(PayAdjustment.objects.filter(component__code="thr", voided_at__isnull=True).count(), 1)
        self.assertTrue(AuditLog.objects.filter(module="payroll", action="thr_push").exists())

    def test_tax_page_and_recap_download_and_rbac(self):
        self.client.force_login(self.payroll); self.assertContains(self.client.get(reverse("pay_tax")), "PPh 21")
        r = self.client.get(reverse("pay_tax"), {"download": 1, "year": 2025}); self.assertEqual(r.status_code, 200); self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertTrue(AuditLog.objects.filter(module="payroll", action="pph21_recap").exists())
        for name in ("pay_thr", "pay_tax"):
            for u, code in ((self.hrd, 403), (self.root, 200), (self.payroll, 200)): self.client.force_login(u); self.assertEqual(self.client.get(reverse(name)).status_code, code, (name, u.username))

    def test_new_report_sheets_have_pph_and_labor_cost(self):
        from . import report
        PayParam.objects.create(key="pph21_aktif", value=D(1), effective_from=D1(2020, 1, 1)); self.scale(wage="900000", on=D1(2025, 1, 1)); self.att(self.e, D1(2025, 10, 1))
        run = PayrollRun.objects.create(period=PERIOD, status="draft"); services.calculate_run(run.pk, self.payroll)
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(report.build(run)[0])); self.assertEqual(len(wb.sheetnames), 11)
        self.assertEqual(wb["PPh 21"].max_row, 2); self.assertEqual(wb["Biaya Tenaga Kerja"].max_row, 3)


class Pph21EngineTests(Base):
    """Mesin: nonaktif secara bawaan; aktif → TER Jan–Nov, penyelesaian setahun di Desember."""
    def setUp(self):
        from datetime import date
        self.e = self.emp(npwp="123456789012345"); self.scale(wage="400000"); self.profile(self.e, ptkp_status="TK0")
        for m in (10, 11, 12): self.att(self.e, D1(2025, m, 1))

    def run_for(self, period):
        r, _ = PayrollRun.objects.get_or_create(period=period, defaults={"status": "draft"}); services.calculate_run(r.pk, self.payroll); return Payslip.objects.get(run=r)

    def on(self, v="1"): PayParam.objects.create(key="pph21_aktif", value=D(v), effective_from=D1(2020, 1, 1))

    def test_off_by_default(self):
        self.assertIsNone(self.item(self.run_for("2025-10"), "pot_pph21"))

    def test_ter_month_uses_category_a_and_npwp_surcharge(self):
        from . import tax, ter_data
        self.on(); s = self.run_for("2025-10"); it = self.item(s, "pot_pph21"); self.assertIsNotNone(it)
        bruto = sum(i.amount for i in s.items.all() if i.section != "potongan" and i.code != "tali_asih"); pct = tax.ter_rate(ter_data.TABLES["A"], bruto)
        self.assertEqual(it.amount, tax.pph_ter(bruto, pct)); self.assertGreater(it.amount, 0)
        self.assertEqual(s.total_deductions, sum(i.amount for i in s.items.filter(section="potongan")))
        self.e.npwp = ""; self.e.save(); it2 = self.item(self.run_for("2025-10"), "pot_pph21"); self.assertGreater(it2.amount, it.amount)

    def test_category_follows_ptkp_status(self):
        self.on(); a = self.item(self.run_for("2025-10"), "pot_pph21").amount
        PayProfile.objects.create(employee=self.e, effective_from=D1(2025, 10, 1), grade=9, tier="A", ptkp_status="K3"); b = self.item(self.run_for("2025-10"), "pot_pph21").amount
        self.assertLess(b, a)

    def test_december_settles_year_and_flags_missing_history(self):
        self.scale(wage="2000000", on=D1(2025, 1, 1)); self.on(); s = self.run_for("2025-12"); it = self.item(s, "pot_pph21"); self.assertIsNotNone(it); self.assertIn("Penyelesaian setahun", it.note)
        self.assertTrue(any("slip bulan sebelumnya" in f for f in s.flags))

    def test_december_subtracts_withheld_ytd(self):
        self.scale(wage="2000000", on=D1(2025, 1, 1)); self.on(); self.run_for("2025-10"); self.run_for("2025-11"); dec = self.run_for("2025-12")
        paid = sum(self.item(self.run_for(p), "pot_pph21").amount for p in ("2025-10", "2025-11")); self.assertGreater(paid, 0)
        dec = self.run_for("2025-12"); it = self.item(dec, "pot_pph21")   # hanya 3 bulan di setahun → pajak setahun < yang sudah dipotong: tidak ada potongan baru + peringatan kelebihan
        self.assertTrue(it is None or f"sudah dipotong {int(paid):,}".replace(",", ".") in it.note); self.assertTrue(it is not None or any("kelebihan" in f for f in dec.flags))

    def test_profile_form_and_import_accept_ptkp(self):
        from .bulk_specs import PROFILE
        self.assertIn("status_ptkp", PROFILE.columns if hasattr(PROFILE, "columns") else str(PROFILE.__dict__))


class MasterDeductionTests(Base):
    """Potongan & angka pajak = master bertanggal, bukan kode (putaran 61)."""
    def setUp(self):
        self.e = self.emp(); self.scale(wage="100000"); self.profile(self.e); self.att(self.e, D1(2025, 10, 1)); self.client.force_login(self.payroll)

    def post_rule(self, **kw):
        d = {"name": "Iuran Koperasi", "method": "persen_pokok", "value": "1", "rounding": "1", "pay_type": "", "effective_from": "2025-10-01", "note": "AD/ART"}; d.update(kw)
        return self.client.post(reverse("pay_deduction_new"), d)

    def slip(self, period=PERIOD):
        r, _ = PayrollRun.objects.get_or_create(period=period, defaults={"status": "draft"}); services.calculate_run(r.pk, self.payroll); return Payslip.objects.get(run=r)

    def test_custom_rule_creates_component_and_deducts_from_effective_date(self):
        self.assertEqual(self.post_rule().status_code, 302)
        s = self.slip(); it = s.items.get(code__startswith="pot_m_"); self.assertEqual((it.section, it.label, it.source), ("potongan", "Iuran Koperasi", "master"))
        self.assertEqual(it.amount, int(self.item(s, "gaji_pokok").amount) // 100)
        self.post_rule(value="0", effective_from="2025-11-01"); self.assertTrue(self.slip(PERIOD).items.filter(code__startswith="pot_m_").exists())   # Okt tetap kena
        self.att(self.e, D1(2025, 11, 3)); self.assertFalse(self.slip("2025-11").items.filter(code__startswith="pot_m_").exists())   # dihentikan sejak 1 Nov

    def test_future_rule_not_applied_and_same_name_reuses_component(self):
        self.post_rule(effective_from="2026-01-01"); self.assertFalse(self.slip().items.filter(code__startswith="pot_m_").exists())
        self.post_rule(method="nominal_bulan", value="10000", effective_from="2025-10-01"); self.assertEqual(PayComponent.objects.filter(code__startswith="pot_m_").count(), 1)
        self.assertEqual(self.slip().items.get(code__startswith="pot_m_").amount, 10000)

    def test_builtin_name_rejected_and_void(self):
        self.assertEqual(self.post_rule(name="THR").status_code, 200)   # form error: nama komponen bawaan
        self.post_rule(); from .models import DeductionRule
        r = DeductionRule.objects.get(); self.client.post(reverse("pay_void", args=["dedrule", r.pk]), {"reason": "salah"}); r.refresh_from_db(); self.assertIsNotNone(r.voided_at)
        self.assertFalse(self.slip().items.filter(code__startswith="pot_m_").exists())

    def test_tax_masters_drive_calculation(self):
        from . import tax
        self.assertEqual(tax.annual_tax(D(120_000_000), D(0), D(54_000_000), 12, D(1), D(5), D(500_000), [(None, D("0.10"))])["pajak"], 6_000_000)
        r = self.client.post(reverse("pay_bracket_new"), {"effective_from": "2026-01-01", "lines": "100.000.000;10\n-;20"}); self.assertEqual(r.status_code, 302)
        self.assertEqual(engine.tax_brackets(D1(2026, 6, 1)), [(100_000_000, D("0.1")), (None, D("0.2"))]); self.assertEqual(engine.tax_brackets(D1(2025, 6, 1))[0][0], 60_000_000)
        self.assertEqual(self.client.post(reverse("pay_bracket_new"), {"effective_from": "2026-01-01", "lines": "100;10\n200;20"}).status_code, 200)   # tanpa lapisan teratas ditolak

    def test_ptkp_is_a_parameter(self):
        from . import params
        self.assertEqual(params.defaults()["ptkp_k3"], 72_000_000); PayParam.objects.create(key="ptkp_k3", value=D(80_000_000), effective_from=D1(2026, 1, 1))
        self.assertEqual(engine.load_params(D1(2026, 2, 1))["ptkp_k3"], 80_000_000); self.assertEqual(engine.load_params(D1(2025, 2, 1))["ptkp_k3"], 72_000_000)

    def test_pages_and_rbac(self):
        for name in ("pay_deductions", "pay_deduction_new", "pay_bracket_new", "pay_tax"):
            for u, code in ((self.hrd, 403), (self.root, 200), (self.payroll, 200)): self.client.force_login(u); self.assertEqual(self.client.get(reverse(name)).status_code, code, (name, u.username))
        self.client.force_login(self.payroll); self.assertContains(self.client.get(reverse("pay_deductions")), "Jamsostek")


class NotificationRoutingTests(Base):
    """Notifikasi dikirim ke akun yang berwenang atas tugasnya (putaran 62)."""
    def test_helper_only_active_users_of_given_roles_and_dedupes(self):
        from apps.core import notify
        from apps.core.models import Notification
        User.objects.filter(pk=self.hrd2.pk).update(is_active=False)
        self.assertEqual(notify.send((Role.HRD,), "request", "T", "/x/"), 1); self.assertEqual(Notification.objects.get().user, self.hrd)
        self.assertEqual(notify.send((Role.HRD,), "request", "T", "/x/", skip_if_unread_title="T"), 0)
        self.assertEqual(notify.send((Role.PAYROLL, Role.SUPERADMIN), "payroll", "P"), 2)

    def test_separation_notifies_payroll_only(self):
        from apps.core.models import Notification
        from apps.hrd import services as hs
        e = self.emp()
        with self.captureOnCommitCallbacks(execute=True):
            hs.record_separation(e, {"kind": "resign", "last_date": D1(2030, 1, 31)}, self.hrd)
        n = Notification.objects.filter(kind="payroll"); self.assertEqual({x.user.role for x in n}, {Role.PAYROLL}); self.assertIn("gaji akhir", n[0].title); self.assertEqual(n[0].link, "/payroll/tali-asih/")

    def test_payroll_sees_company_announcements(self):
        from apps.hr import info
        from apps.hr.models import Announcement
        a = Announcement.objects.create(title="Libur", body="x", kind="pengumuman", all_departments=True, created_by=self.hrd)
        self.assertIn(a, info.visible_to(self.payroll)); self.assertIn(self.payroll, info.audience(a)); self.assertNotIn(self.payroll, info.audience(Announcement.objects.create(title="Dept", body="x", kind="pengumuman", created_by=self.hrd)))
