"""Tes Payroll (putaran 57). Bagian 1: mesin hitung terhadap SLIP CONTOH (Okt-2025) dan alur run."""
import datetime
from decimal import Decimal as D

from django.test import TestCase

from apps.core.models import Role, User
from apps.hr.models import AttendanceDaily, Department, Employee, Position
from apps.hrd.models import BpjsDeduction, BpjsMembership, Separation, UniformPurchase, UniformSize, UniformType
from . import calc, engine, services
from .models import PayAdjustment, PayComponent, PayParam, PayProfile, PayrollRun, Payslip, WageScale

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
        run = services.create_run(PERIOD, self.payroll) if False else PayrollRun.objects.create(period=PERIOD)   # periode lampau boleh dibuat langsung (create_run menolak hanya masa depan)
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
        run = self.run_calc(); self.assertEqual(run.n_slips, 0)
        w = " ".join(run.warnings); self.assertIn("belum punya profil gaji", w); self.assertIn("E010", w); self.assertIn("skala gajinya tidak ditemukan", w); self.assertIn("E011", w)

    def test_wage_override_beats_scale(self):
        e = self.emp(); PayProfile.objects.create(employee=e, effective_from=D1(2021, 4, 1), wage_override=D("100000"))
        s = Payslip.objects.get(run=self.run_calc()); self.assertEqual(self.item(s, "gaji_pokok").amount, 3_000_000)

    def test_newest_effective_profile_wins_and_future_profile_ignored(self):
        e = self.emp(); self.scale(); self.scale("90000", 9, "B", D1(2021, 1, 1))
        self.profile(e, tier="A", on=D1(2021, 4, 1)); self.profile(e, tier="B", on=D1(2025, 10, 1)); self.profile(e, tier="A", on=D1(2025, 11, 1), allowance_position=999_999)
        s = Payslip.objects.get(run=self.run_calc()); self.assertEqual(s.grade_label, "9 B"); self.assertIsNone(self.item(s, "t_jabatan"))

    def test_allowances_and_meal_follow_profile_and_attendance(self):
        e = self.emp(); self.scale(); self.profile(e, allowance_position=100_000, allowance_education=50_000, meal_allowance=True)
        for i in range(10): self.att(e, D1(2025, 10, 1 + i))
        s = Payslip.objects.get(run=self.run_calc())
        self.assertEqual((self.item(s, "t_jabatan").amount, self.item(s, "t_pendidikan").amount), (100_000, 50_000))
        self.assertEqual((self.item(s, "uang_makan").amount, self.item(s, "uang_makan").qty), (37_500, 10))

    def test_no_meal_without_profile_flag(self):
        e = self.emp(); self.scale(); self.profile(e); self.att(e, D1(2025, 10, 1)); self.assertIsNone(self.item(Payslip.objects.get(run=self.run_calc()), "uang_makan"))

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

    def calc(self): return Payslip.objects.get(run=services.calculate_run(PayrollRun.objects.get_or_create(period=PERIOD)[0].pk, self.payroll))

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
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif"); run = services.calculate_run(PayrollRun.objects.create(period=PERIOD).pk, self.payroll); self.assertEqual(run.n_slips, 0)

    def test_new_hire_mid_month_is_prorated(self):
        n = self.emp("E050", join=D1(2025, 10, 21)); self.profile(n, on=D1(2025, 10, 21)); run = services.calculate_run(PayrollRun.objects.create(period=PERIOD).pk, self.payroll)
        s = Payslip.objects.get(run=run, employee=n); self.assertEqual(self.item(s, "gaji_pokok").qty, 11); self.assertIsNone(self.item(s, "t_masa_kerja") if calc.full_years(n.join_date, D1(2025, 10, 31)) == 0 else None)

    def test_inactive_without_separation_and_future_joiner_are_excluded(self):
        a = self.emp("E060", status="nonaktif"); self.profile(a); b = self.emp("E061", join=D1(2025, 11, 5)); self.profile(b, on=D1(2025, 11, 5))
        run = services.calculate_run(PayrollRun.objects.create(period=PERIOD).pk, self.payroll); self.assertEqual(list(Payslip.objects.filter(run=run).values_list("nik", flat=True)), ["E001"])


class RunWorkflowTests(Base):
    def setUp(self):
        self.e = self.emp(); self.scale(); self.profile(self.e); self.att(self.e, D1(2025, 10, 1))
        self.run = PayrollRun.objects.create(period=PERIOD)

    def to_submitted(self):
        services.calculate_run(self.run.pk, self.payroll); return services.submit_run(self.run.pk, self.payroll)

    def test_happy_path_and_events(self):
        self.to_submitted(); services.approve_run(self.run.pk, self.hrd); r = services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        self.assertEqual(r.status, "dibayar"); self.assertEqual(list(r.events.values_list("action", flat=True)), ["hitung", "ajukan", "setujui", "bayar"])
        self.assertTrue(r.locked)

    def test_cannot_skip_steps(self):
        with self.assertRaisesMessage(ValueError, "diajukan"): services.submit_run(self.run.pk, self.payroll)                # belum dihitung
        services.calculate_run(self.run.pk, self.payroll)
        with self.assertRaisesMessage(ValueError, "menunggu persetujuan"): services.approve_run(self.run.pk, self.hrd)        # belum diajukan
        with self.assertRaisesMessage(ValueError, "sudah disetujui"): services.pay_run(self.run.pk, datetime.date.today(), self.payroll)

    def test_submitter_cannot_approve_own_run(self):
        self.to_submitted()
        with self.assertRaisesMessage(ValueError, "pemisahan tugas"): services.approve_run(self.run.pk, self.payroll)
        with self.settings(PAYROLL_ALLOW_SELF_APPROVE=True): self.assertEqual(services.approve_run(self.run.pk, self.payroll).status, "disetujui")

    def test_return_needs_reason_and_reopens_for_recalculation(self):
        self.to_submitted()
        with self.assertRaisesMessage(ValueError, "Alasan wajib"): services.return_run(self.run.pk, " ", self.hrd)
        r = services.return_run(self.run.pk, "Lembur departemen A terlalu besar", self.hrd); self.assertEqual((r.status, r.submitted_by), ("dihitung", None))
        self.assertTrue(self.run.events.filter(action="kembalikan", note__contains="Lembur").exists())

    def test_withdraw_and_recalculate_resets_submission(self):
        self.to_submitted(); self.assertEqual(services.withdraw_run(self.run.pk, self.payroll).status, "dihitung")
        self.to_submitted(); self.assertEqual(services.calculate_run(self.run.pk, self.payroll).status, "dihitung")

    def test_locked_runs_cannot_be_recalculated_or_cancelled_after_payment(self):
        self.to_submitted(); services.approve_run(self.run.pk, self.hrd)
        with self.assertRaisesMessage(ValueError, "tidak dapat dihitung ulang"): services.calculate_run(self.run.pk, self.payroll)
        services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        with self.assertRaisesMessage(ValueError, "sudah dibayar"): services.cancel_run(self.run.pk, "oops", self.payroll)

    def test_stale_data_blocks_submit_until_recalculated(self):
        services.calculate_run(self.run.pk, self.payroll)
        services.save_adjustment(self.e, PERIOD, PayComponent.objects.get(code="insentif"), 50_000, None, "", self.payroll)
        self.assertTrue(services.run_is_stale(PayrollRun.objects.get(pk=self.run.pk)))
        with self.assertRaisesMessage(ValueError, "Hitung ulang dulu"): services.submit_run(self.run.pk, self.payroll)
        services.calculate_run(self.run.pk, self.payroll); services.submit_run(self.run.pk, self.payroll)

    def test_one_live_run_per_period_but_cancelled_can_be_recreated(self):
        with self.assertRaisesMessage(ValueError, "sudah ada"): services.create_run(PERIOD, self.payroll)
        services.cancel_run(self.run.pk, "Salah periode", self.payroll); self.assertEqual(services.create_run(PERIOD, self.payroll).status, "draft")

    def test_create_run_validates_period(self):
        for bad in ("", "2025-13", "abc"):
            with self.assertRaises(ValueError): services.create_run(bad, self.payroll)
        with self.assertRaisesMessage(ValueError, "masa depan"): services.create_run("2099-01", self.payroll)

    def test_pay_date_cannot_be_future_or_missing(self):
        self.to_submitted(); services.approve_run(self.run.pk, self.hrd)
        with self.assertRaisesMessage(ValueError, "masa depan"): services.pay_run(self.run.pk, datetime.date.today() + datetime.timedelta(days=1), self.payroll)
        with self.assertRaisesMessage(ValueError, "wajib"): services.pay_run(self.run.pk, None, self.payroll)

    def test_paying_marks_uniform_deducted_and_tali_asih_paid_atomically(self):
        z, t = UniformSize.objects.get_or_create(code="L")[0], UniformType.objects.get_or_create(name="Seragam Kerja")[0]   # sudah disemai migrasi hrd/0007
        p = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 10, 3), utype=t, size=z, gender="L", quantity=1, rate_amount=19000, deduction_amount=19000)
        sep = Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 10, 20), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=1_000_000)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif")
        self.to_submitted(); services.approve_run(self.run.pk, self.hrd); services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        p.refresh_from_db(); sep.refresh_from_db(); self.assertEqual((p.deduction_status, p.deducted_period), ("sudah", PERIOD)); self.assertEqual(sep.tali_asih_paid_on, datetime.date.today())

    def test_payment_rolls_back_everything_if_a_reference_went_stale(self):
        z, t = UniformSize.objects.get_or_create(code="L")[0], UniformType.objects.get_or_create(name="Seragam Kerja")[0]   # sudah disemai migrasi hrd/0007
        p = UniformPurchase.objects.create(employee=self.e, purchase_date=D1(2025, 10, 3), utype=t, size=z, gender="L", quantity=1, rate_amount=19000, deduction_amount=19000)
        sep = Separation.objects.create(employee=self.e, kind="resign", last_date=D1(2025, 10, 20), emp_name=self.e.name, department_name="Produksi", join_date=self.e.join_date, tali_asih=1_000_000)
        Employee.objects.filter(pk=self.e.pk).update(status="nonaktif")
        self.to_submitted(); services.approve_run(self.run.pk, self.hrd)
        UniformPurchase.objects.filter(pk=p.pk).update(deduction_status="sudah", deducted_period="2025-10")   # dipotong di tempat lain setelah disetujui
        with self.assertRaisesMessage(ValueError, "Sudah ditandai dipotong"): services.pay_run(self.run.pk, datetime.date.today(), self.payroll)
        self.run.refresh_from_db(); sep.refresh_from_db(); self.assertEqual(self.run.status, "disetujui"); self.assertIsNone(sep.tali_asih_paid_on)

    def test_adjustments_locked_once_run_submitted(self):
        self.to_submitted(); comp = PayComponent.objects.get(code="insentif")
        with self.assertRaisesMessage(ValueError, "tarik atau batalkan"): services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll)
        services.withdraw_run(self.run.pk, self.payroll); a = services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll); self.assertEqual(a.amount, 1000)

    def test_adjustment_replaces_not_overwrites(self):
        comp = PayComponent.objects.get(code="insentif"); a1 = services.save_adjustment(self.e, PERIOD, comp, 1000, None, "", self.payroll); a2 = services.save_adjustment(self.e, PERIOD, comp, 2000, None, "", self.payroll)
        a1.refresh_from_db(); self.assertEqual((a1.is_void, a1.void_reason, a2.amount), (True, "Diganti nilai baru", 2000)); self.assertEqual(PayAdjustment.objects.filter(voided_at__isnull=True).count(), 1)

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

    def test_notifications_go_to_the_right_roles(self):
        from apps.core.models import Notification
        self.to_submitted(); self.assertTrue(Notification.objects.filter(user=self.hrd, kind="payroll").exists()); self.assertFalse(Notification.objects.filter(user=self.payroll).exists())
        services.approve_run(self.run.pk, self.hrd); self.assertTrue(Notification.objects.filter(user=self.payroll, kind="payroll").exists())
