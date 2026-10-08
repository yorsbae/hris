"""Putaran 16: perintah seed_demo (data uji coba) berjalan, konsisten, dan tidak bisa berjalan dua kali."""
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from apps.core.models import Role, User
from apps.hr import schedule
from apps.hr.models import ChangeRequest, Employee, LeaveLedger, Shift, ShiftAssignment, ShiftGroup, ShiftRotation
from apps.poli.models import Medicine, MedicalRecord


class SeedDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.out = StringIO(); call_command("seed_demo", "--employees", "30", stdout=cls.out)

    def test_counts_and_accounts(self):
        self.assertEqual(Employee.objects.filter(nik__startswith="DM").count(), 30)
        for role in (Role.SUPERADMIN, Role.HRD, Role.POLI, Role.DEPT_ADMIN): self.assertTrue(User.objects.filter(role=role).exists())
        self.assertTrue(User.objects.get(username="superadmin").is_staff)
        self.assertTrue(ChangeRequest.objects.filter(status="pending").exists())
        self.assertIn("Demo#HRIS-2026", self.out.getvalue())

    def test_stock_never_negative_and_ledger_consistent(self):
        self.assertFalse(Medicine.objects.filter(stock__lt=0).exists())
        for m in Medicine.objects.all(): self.assertEqual(m.stock, sum(x.qty for x in m.movements.all()))
        self.assertTrue(MedicalRecord.objects.exists())

    def test_leave_balance_not_overdrawn(self):
        from django.db.models import Sum
        for e in Employee.objects.filter(status="aktif"):
            self.assertGreaterEqual(LeaveLedger.objects.filter(employee=e).aggregate(s=Sum("days"))["s"] or 0, 0)

    def test_second_run_refuses(self):
        with self.assertRaises(CommandError): call_command("seed_demo", "--employees", "20", stdout=StringIO())

    def test_pages_render_for_each_role(self):
        for uname, paths in (("superadmin", ["/", "/admin/", "/audit/"]), ("hrd", ["/", "/employees/", "/requests/", "/hrd/"]),
                             ("poli", ["/", "/poli/records/", "/poli/medicines/"]), ("admin_prd", ["/", "/employees/", "/requests/"])):
            self.client.force_login(User.objects.get(username=uname))
            for p in paths: self.assertLess(self.client.get(p).status_code, 500, (uname, p))


    # ---- putaran 17b: master shift baru + tukar 1 orang / 2 orang
    def test_shift_master_codes_groups_and_full_rotation_table(self):
        self.assertEqual(set(Shift.objects.values_list("code", flat=True)), {"PAGI", "SIANG", "MALAM", "GS-12", "GS-14", "GS-16"})
        self.assertEqual(set(Shift.objects.filter(is_gs=True).values_list("code", flat=True)), {"GS-12", "GS-14", "GS-16"})
        self.assertEqual(ShiftGroup.objects.count(), 14)
        self.assertEqual(set(ShiftGroup.objects.filter(pattern="3_SHIFT").values_list("code", flat=True)), {f"{l}7" for l in "ABCDEFG"})
        self.assertEqual(ShiftRotation.objects.count(), 14 * 7)  # tiap kelompok punya 7 hari
        for g in ShiftGroup.objects.all():
            rows = list(g.rotation.all()); self.assertEqual(sum(r.shift_id is None for r in rows), 1, g.code)  # tepat 1 hari libur per minggu
            if g.pattern == "2_SHIFT": self.assertFalse(any(r.shift and r.shift.crosses_midnight for r in rows), g.code)  # pola 2 shift tanpa Malam
            r_clean = [r.clean() for r in rows]  # lolos validasi rotasi

    def test_employees_have_either_group_or_fixed_shift(self):
        for e in Employee.objects.filter(nik__startswith="DM"):
            self.assertNotEqual(bool(e.shift_id), bool(e.shift_group_id), e.nik)  # tepat salah satu
            if e.shift_id: self.assertTrue(e.shift.is_gs, e.nik)
            if e.department.code in ("PRD", "GDG", "MTC"): self.assertEqual(e.shift_group.pattern, "2_SHIFT")
            if e.department.code == "PKG": self.assertEqual(e.shift_group.pattern, "3_SHIFT")

    def test_swap_requests_cover_solo_and_duo_and_are_consistent_with_schedule_rules(self):
        swaps = list(ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"]).select_related("employee"))
        solo, duo = [q for q in swaps if not q.payload.get("partner_id")], [q for q in swaps if q.payload.get("partner_id")]
        self.assertTrue(solo and duo)
        self.assertTrue({q.type for q in duo} == {"tukar_shift", "tukar_libur"} and {q.type for q in solo} == {"tukar_shift", "tukar_libur"})
        self.assertTrue(any(q.status == "executed" for q in duo) and any(q.status == "executed" for q in solo))
        expected = {("tukar_shift", True): 2, ("tukar_libur", True): 4, ("tukar_libur", False): 2, ("tukar_shift", False): 1}
        for q in swaps:
            partner = schedule.partner_of(q); n = ShiftAssignment.objects.filter(request=q).count()
            if partner: self.assertEqual(q.employee.department_id, partner.department_id)  # pasangan satu departemen (Admin Dept boleh mengajukan)
            if q.status == "executed": self.assertEqual(n, expected[(q.type, bool(partner))], q.pk)
            else:
                self.assertEqual(n, 0, q.pk)
                if q.status in ("pending", "approved"): schedule.build_rows(q.type, q.employee, partner, q.payload)  # masih valid menurut aturan jadwal yang sama

    def test_demo_swaps_do_not_collide(self):
        seen = set()
        for a in ShiftAssignment.objects.all(): self.assertNotIn((a.employee_id, a.date), seen); seen.add((a.employee_id, a.date))
        live = {}
        for q in ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"], status__in=["pending", "approved"]):
            for who in (q.employee_id, q.payload.get("partner_id")):
                for d in schedule.dates_of(q.type, q.payload):
                    if who: self.assertNotIn((who, d), live); live[(who, d)] = q.pk

    def test_hrd_can_execute_a_demo_duo_swap_end_to_end(self):
        from apps.hr import services
        q = ChangeRequest.objects.filter(type="tukar_shift", status="approved").exclude(payload__partner_id=None).first()
        self.assertIsNotNone(q)
        hrd = User.objects.get(username="hrd"); services.transition(q, "executed", hrd)
        self.assertEqual(ShiftAssignment.objects.filter(request=q).count(), 2)

    def test_pages_for_swaps_render(self):
        self.client.force_login(User.objects.get(username="hrd"))
        for q in ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"])[:6]: self.assertEqual(self.client.get(f"/requests/{q.pk}/").status_code, 200)
        e = Employee.objects.filter(shift_group__isnull=False).first(); self.assertContains(self.client.get(f"/employees/{e.pk}/"), "Jadwal 14 hari ke depan")
        self.assertEqual(self.client.get("/master/shift/").status_code, 200)
