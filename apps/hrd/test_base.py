from datetime import date, time, timedelta
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from apps.core.models import AuditLog, Role, User
from apps.hr.models import Department, Employee, Position, Shift

KEY = Fernet.generate_key().decode()
PW = "kata-sandi-panjang-123"


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class HrdBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.pos = Position.objects.create(name="Staff")
        cls.e1 = Employee.objects.create(nik="001", name="Budi", gender="L", department=cls.d1, position=cls.pos, join_date=date(2024, 1, 1),
                                         bpjs_kes="0001234567890", bpjs_tk="TK-9988776655")
        cls.e2 = Employee.objects.create(nik="002", name="Sari", gender="P", department=cls.d2, position=cls.pos, join_date=date(2024, 1, 1))
        cls.e3 = Employee.objects.create(nik="003", name="Dewi", gender="P", department=cls.d1, position=cls.pos, join_date=date(2024, 6, 1))
        cls.e_off = Employee.objects.create(nik="009", name="Mantan", gender="P", department=cls.d1, position=cls.pos, join_date=date(2023, 1, 1), status="nonaktif")
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.adm = User.objects.create_user("adm", password=PW, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI)

    def login(self, name="hrd"): self.client.force_login(User.objects.get(username=name))

    def last_audit(self, action): return AuditLog.objects.filter(module="hrd", action=action).order_by("-id").first()

    def msgs(self, resp): return [str(m) for m in resp.context["messages"]] if resp.context else []

    @staticmethod
    def today(): return date.today()

    @staticmethod
    def days(n): return date.today() + timedelta(days=n)
