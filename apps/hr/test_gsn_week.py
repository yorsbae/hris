"""GS/N: pola mingguan per karyawan (Senin…Minggu: GS / Pagi / Siang / Malam / Libur), tampil di form dan dipakai jadwal."""
from datetime import date, time
from django.test import TestCase
from apps.hr import schedule
from apps.hr.emp_forms import EmployeeForm
from apps.hr.models import Department, Employee, Shift, ShiftGroup

WEEK = ["GS", "PAGI", "GS", "SIANG", "GS", "MALAM", "LIBUR"]


class GsnWeekTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d = Department.objects.create(code="A", name="Produksi")
        cls.gs = Shift.objects.get_or_create(code="GS-16", defaults=dict(name="GS-16", start=time(8), end=time(16), is_gs=True))[0]
        for c, a, b, x in (("PAGI", 6, 14, False), ("SIANG", 14, 22, False), ("MALAM", 22, 6, True)): Shift.objects.get_or_create(code=c, defaults=dict(name=c, start=time(a), end=time(b), crosses_midnight=x))
        cls.gsn = ShiftGroup.objects.get(pattern=ShiftGroup.GSN)
        cls.base = {"gender": "L", "join_date": "2020-01-01", "department": cls.d.pk, "status": "aktif", "pay_basis": "bulanan"}

    def post(self, nik, week, **kw):
        data = {**self.base, "nik": nik, "name": "N" + nik, "shift_choice": self.gsn.pk, **{f"gsn_{i}": c for i, c in enumerate(week)}, **kw}
        return EmployeeForm(data)

    def test_week_is_required_and_needs_a_day_off(self):
        f = self.post("1", ["GS"] * 7); self.assertFalse(f.is_valid()); self.assertIn("shift_choice", f.errors)   # tanpa libur
        f = self.post("2", ["GS"] * 6 + [""]); self.assertFalse(f.is_valid())                                    # terisi sebagian
        self.assertTrue(self.post("5", [""] * 7).is_valid())                                                     # kosong semua = pola lama (impor/data lama)

    def test_saved_pattern_drives_schedule(self):
        f = self.post("3", WEEK); self.assertTrue(f.is_valid(), f.errors); e = f.save()
        e = Employee.objects.get(pk=e.pk); self.assertEqual(e.gsn_week, WEEK); self.assertEqual(e.off_weekday, 6); self.assertEqual(e.shift_label, "GS/N")
        mon = date(2026, 10, 19)   # Senin
        got = [(x["off"], x["shift"].code if x["shift"] else None) for x in schedule.schedule_range(e, mon, 7)]
        self.assertEqual(got, [(False, "GS-16"), (False, "PAGI"), (False, "GS-16"), (False, "SIANG"), (False, "GS-16"), (False, "MALAM"), (True, None)])   # Sabtu GS penuh (bukan GS-14)

    def test_switching_to_rotation_or_gs_clears_pattern(self):
        e = self.post("4", WEEK).save()
        a = ShiftGroup.objects.get_or_create(code="A", defaults={"pattern": ShiftGroup.P2})[0]
        f = EmployeeForm({**self.base, "nik": "4", "name": "N4", "shift_choice": a.pk, "effective_date": "2026-10-21"}, instance=Employee.objects.get(pk=e.pk))
        self.assertTrue(f.is_valid(), f.errors); e = f.save(); self.assertEqual(Employee.objects.get(pk=e.pk).gsn_week, [])

    def test_form_page_has_week_fields(self):
        from django.urls import reverse
        from apps.core.models import Role, User
        User.objects.create_user("hrdx", password="Pw-12345-abc", role=Role.HRD); self.client.login(username="hrdx", password="Pw-12345-abc")
        h = self.client.get("/employees/new/").content.decode()
        for t in ('name="gsn_0"', 'name="gsn_6"', "Pola mingguan GS/N", "Senin", "Minggu"): self.assertIn(t, h)
