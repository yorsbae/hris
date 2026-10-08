"""Putaran 20: edit tabel rotasi di halaman Master Shift (menggantikan impor/ekspor rotasi)."""
from datetime import time
from django.test import TestCase
from apps.core.models import Role, User
from .models import Shift, ShiftGroup, ShiftRotation


class RotationEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .models import Department
        d = Department.objects.create(code="PRD", name="Produksi")
        User.objects.create_user("hrd", password="x", role=Role.HRD); User.objects.create_user("poli", password="x", role=Role.POLI)
        User.objects.create_user("admin_dept", password="x", role=Role.DEPT_ADMIN, department=d)
        cls.pagi = Shift.objects.create(code="PAGI", name="Pagi", start=time(6), end=time(14))
        cls.malam = Shift.objects.create(code="MALAM", name="Malam", start=time(22), end=time(6), crosses_midnight=True)
        cls.gs = Shift.objects.create(code="GS-16", name="GS", start=time(8), end=time(16), is_gs=True)
        cls.g2 = ShiftGroup.objects.create(code="A", pattern="2_SHIFT"); cls.g3 = ShiftGroup.objects.create(code="A_pack", pattern="3_SHIFT")

    def post(self, data): return self.client.post("/master/shift/rotation/", data)

    def test_edit_saves_cells_and_audits(self):
        self.client.login(username="hrd", password="x")
        self.assertContains(self.client.get("/master/shift/rotation/"), "Ubah tabel rotasi")
        r = self.post({f"c_{self.g3.pk}_0": "MALAM", f"c_{self.g3.pk}_1": "PAGI", f"c_{self.g2.pk}_0": "PAGI"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ShiftRotation.objects.get(group=self.g3, weekday=0).shift, self.malam)
        self.assertIsNone(ShiftRotation.objects.get(group=self.g3, weekday=2).shift)  # tidak diisi = libur
        from apps.core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(module="hr", action="rotation_update").exists())

    def test_rejects_night_in_2_shift_gs_and_unknown_all_or_nothing(self):
        self.client.login(username="hrd", password="x")
        for bad in ("MALAM", "GS-16", "XXX"):
            r = self.post({f"c_{self.g2.pk}_0": "PAGI", f"c_{self.g2.pk}_1": bad})
            self.assertEqual(r.status_code, 200); self.assertContains(r, "Tidak disimpan")
        self.assertFalse(ShiftRotation.objects.exists())  # tidak ada yang tersimpan sebagian

    def test_reset_to_official_and_permissions(self):
        self.client.login(username="hrd", password="x")
        Shift.objects.create(code="SIANG", name="Siang", start=time(14), end=time(22))
        self.assertEqual(self.post({"action": "reset"}).status_code, 302)
        self.assertEqual(ShiftGroup.objects.count(), 14)
        self.assertEqual(ShiftRotation.objects.get(group__code="A", weekday=2).shift.code, "PAGI")  # A: Rabu Pagi (jadwal resmi)
        for u in ("poli", "admin_dept"):
            self.client.login(username=u, password="x"); self.assertEqual(self.client.get("/master/shift/rotation/").status_code, 403)
        self.client.logout(); self.assertEqual(self.client.get("/master/shift/rotation/").status_code, 302)

    def test_master_shift_page_has_no_rotation_import_and_title_clean(self):
        self.client.login(username="hrd", password="x"); r = self.client.get("/master/shift/")
        self.assertNotContains(r, "/master/rotasi/"); self.assertContains(r, "/master/shift/rotation/")
        self.assertContains(r, "<title>Master Shift"); self.assertNotIn("<h3", r.content.decode().split("</title>")[0])
        self.assertEqual(self.client.get("/master/rotasi/import/").status_code, 404)
