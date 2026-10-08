"""Putaran 20: Pengajuan dipecah menjadi submenu per fungsi (Izin & Cuti, Mutasi & Promosi, Perubahan Status, Shift & Tukar Jadwal)."""
from datetime import date
from django.test import TestCase
from apps.core.models import Role, User
from .models import ChangeRequest, Department, Employee, LeaveLedger, Position


class RequestGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d = Department.objects.create(code="A", name="Produksi"); pos = Position.objects.create(name="Staff")
        cls.e = Employee.objects.create(nik="001", name="Emp 001", gender="L", department=cls.d, position=pos, join_date=date(2024, 1, 1))
        LeaveLedger.objects.create(employee=cls.e, year=2026, kind="grant", days=12)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password="x", role=Role.DEPT_ADMIN, department=cls.d)
        cls.cuti = ChangeRequest.objects.create(type="cuti", employee=cls.e, department=cls.d, payload={}, requested_by=cls.hrd)
        cls.status = ChangeRequest.objects.create(type="status", employee=cls.e, department=cls.d, payload={}, requested_by=cls.hrd)
        cls.mut = ChangeRequest.objects.create(type="mutasi_dept", employee=cls.e, department=cls.d, payload={}, requested_by=cls.hrd)

    def ids(self, url):
        r = self.client.get(url); self.assertEqual(r.status_code, 200)
        return {x.pk for x in r.context["page"]}

    def test_each_group_lists_only_its_types(self):
        self.client.force_login(self.hrd)
        self.assertEqual(self.ids("/requests/g/izin/"), {self.cuti.pk}); self.assertEqual(self.ids("/requests/g/status/"), {self.status.pk})
        self.assertEqual(self.ids("/requests/g/mutasi/"), {self.mut.pk}); self.assertEqual(self.ids("/requests/g/jadwal/"), set())
        self.assertEqual(self.ids("/requests/"), {self.cuti.pk, self.status.pk, self.mut.pk})
        self.assertEqual(self.client.get("/requests/g/xxx/").status_code, 404)

    def test_filter_type_inside_group_cannot_escape_group(self):
        self.client.force_login(self.hrd); self.assertEqual(self.ids("/requests/g/izin/?type=status"), set())

    def test_menu_has_submenus_and_one_active(self):
        self.client.force_login(self.hrd); r = self.client.get("/requests/g/mutasi/")
        labels = [(i["label"], i["active"]) for g in r.context["nav_groups"] for i in g["items"]]
        self.assertIn(("Mutasi & Promosi", True), labels); self.assertEqual(sum(a for _, a in labels), 1)
        self.assertContains(r, 'href="/requests/g/status/"')

    def test_dept_admin_has_no_status_group(self):
        self.client.force_login(self.adm)
        r = self.client.get("/requests/g/izin/"); self.assertNotContains(r, "/requests/g/status/"); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/requests/g/status/").status_code, 404)

    def test_new_from_group_limits_types_and_posts_within_group(self):
        self.client.force_login(self.hrd); r = self.client.get("/requests/new/?grp=izin")
        self.assertEqual([c[0] for c in r.context["form"].fields["type"].choices], ["izin", "cuti", "sakit", "izin_terlambat", "izin_pulang", "izin_khusus"])
        bad = self.client.post("/requests/new/", {"grp": "izin", "type": "status", "nik": "001", "effective_date": "2026-11-01", "reason": "x"})
        self.assertEqual(bad.status_code, 200); self.assertEqual(ChangeRequest.objects.filter(type="status").count(), 1)  # tetap hanya yang lama
