"""Putaran 16: perintah seed_demo (data uji coba) berjalan, konsisten, dan tidak bisa berjalan dua kali."""
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from apps.core.models import Role, User
from apps.hr.models import ChangeRequest, Employee, LeaveLedger
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
