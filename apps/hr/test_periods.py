"""Tutup periode (tutup buku): checklist, penguncian rekap absensi & tagihan Poli, buka kembali hanya Superadmin."""
from datetime import date, time
from django.test import TestCase
from apps.core.models import AuditLog, Role, User
from apps.hr import periods
from apps.hr.models import AttendanceDaily, Department, Employee, PeriodClose, Shift

PW = "Pw-12345-abc!"


class PeriodTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d = Department.objects.create(code="PR", name="Produksi")
        cls.hrd = User.objects.create_user("hrd9", password=PW, role=Role.HRD); cls.su = User.objects.create_user("su9", password=PW, role=Role.SUPERADMIN)
        cls.adm = User.objects.create_user("adm9", password=PW, role=Role.DEPT_ADMIN, department=cls.d)
        cls.sh = Shift.objects.create(name="Pagi9", code="PAGI9", start=time(7), end=time(15))
        cls.e = Employee.objects.create(nik="P1", name="Pee", gender="L", department=cls.d, join_date=date(2020, 1, 1), shift=cls.sh)

    def login(self, u): self.client.login(username=u, password=PW)

    def test_segments_skip_closed_month(self):
        PeriodClose.objects.create(period="2026-09")
        self.assertEqual(periods.open_segments(date(2026, 8, 25), date(2026, 10, 5)), [(date(2026, 8, 25), date(2026, 8, 31)), (date(2026, 10, 1), date(2026, 10, 5))])
        self.assertEqual(periods.open_segments(date(2026, 9, 3), date(2026, 9, 9)), [])
        self.assertEqual(periods.open_segments(date(2026, 10, 1), date(2026, 10, 3)), [(date(2026, 10, 1), date(2026, 10, 3))])

    def test_closed_month_is_not_recomputed(self):
        from apps.hr import attendance_calc as calc
        today = date(2026, 10, 20)
        calc.process(date(2026, 9, 1), date(2026, 9, 3), [self.e.pk], today); n = AttendanceDaily.objects.filter(date__month=9).count(); self.assertEqual(n, 3)
        PeriodClose.objects.create(period="2026-09"); AttendanceDaily.objects.filter(date__month=9).delete()
        r = calc.process(date(2026, 9, 1), date(2026, 9, 3), [self.e.pk], today); self.assertEqual(r["rows"], 0); self.assertEqual(AttendanceDaily.objects.filter(date__month=9).count(), 0)
        calc.process(date(2026, 10, 1), date(2026, 10, 2), [self.e.pk], today); self.assertEqual(AttendanceDaily.objects.filter(date__month=10).count(), 2)   # bulan terbuka tetap jalan

    def test_close_needs_checklist_or_force_and_reopen_is_superadmin_only(self):
        AttendanceDaily.objects.create(employee=self.e, department=self.d, date=date(2026, 9, 2), status="hadir")
        self.login("hrd9"); self.client.post("/absensi/periode/", {"period": "2026-09", "action": "close"})
        self.assertTrue(PeriodClose.objects.filter(period="2026-09", closed=True).exists())            # bersih → bisa ditutup
        self.assertTrue(AuditLog.objects.filter(action="period_close").exists())
        self.client.post("/absensi/periode/", {"period": "2026-09", "action": "reopen"}); self.assertTrue(PeriodClose.objects.get(period="2026-09").closed)   # HRD tidak boleh
        self.login("su9"); self.client.post("/absensi/periode/", {"period": "2026-09", "action": "reopen"}); self.assertFalse(PeriodClose.objects.get(period="2026-09").closed)
        self.assertTrue(AuditLog.objects.filter(action="period_reopen").exists())

    def test_blockers_stop_closing_unless_forced(self):
        AttendanceDaily.objects.create(employee=self.e, department=self.d, date=date(2026, 9, 2), status="tanpa_scan")
        self.login("hrd9"); self.client.post("/absensi/periode/", {"period": "2026-09", "action": "close"}); self.assertFalse(PeriodClose.objects.filter(period="2026-09").exists())
        self.client.post("/absensi/periode/", {"period": "2026-09", "action": "close", "force": "1"}); self.assertTrue(PeriodClose.objects.filter(period="2026-09", closed=True).exists())

    def test_page_access(self):
        self.login("hrd9"); self.assertEqual(self.client.get("/absensi/periode/").status_code, 200)
        self.login("adm9"); self.assertEqual(self.client.get("/absensi/periode/").status_code, 403)


class PoliBillLockTests(PeriodTests):
    def test_bill_in_closed_period_is_refused(self):
        from apps.poli import services
        from apps.poli.models import Partner
        PeriodClose.objects.create(period="2026-09")
        p = Partner.objects.create(name="RS Uji")
        with self.assertRaisesMessage(ValueError, "sudah ditutup"):
            services.create_partner_bill(self.hrd, p, "INV-1", date(2026, 10, 1), self.e, date(2026, 9, 10), "rawat_jalan", "", None, "non_bpjs", 100000)
