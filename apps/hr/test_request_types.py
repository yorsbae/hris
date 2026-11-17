"""Pengajuan: status karyawan (harian/borongan/magang), Perubahan shift = kelompok (GS, GS/N, A7…), promosi/demosi/rotasi/izin khusus digabung, panel info."""
from datetime import date, time, timedelta
from django.test import TestCase
from apps.core.models import Role, User
from . import services
from .forms import allowed_types
from .models import ChangeRequest, Department, Employee, EmployeeHistory, Position, Shift, ShiftGroup, ShiftRotation


def next_tue():
    d = date.today() + timedelta(days=1)
    while d.weekday() != 1: d += timedelta(days=1)
    return d


class RequestTypeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.pos = Position.objects.create(name="Staff")
        cls.pagi = Shift.objects.get_or_create(code="PAGI", defaults=dict(name="Shift Pagi", start=time(7), end=time(15)))[0]
        Shift.objects.filter(is_gs=True).update(active=False)
        cls.gs16 = Shift.objects.update_or_create(code="GS-16", defaults=dict(name="GS 16", start=time(8), end=time(16), is_gs=True, active=True))[0]
        cls.gGS = ShiftGroup.objects.get_or_create(code="GS", defaults=dict(pattern=ShiftGroup.GS))[0]
        cls.gGSN = ShiftGroup.objects.get_or_create(code="GS/N", defaults=dict(pattern=ShiftGroup.GSN))[0]
        cls.gA = ShiftGroup.objects.get_or_create(code="A", defaults=dict(pattern=ShiftGroup.P2))[0]
        cls.gAp = ShiftGroup.objects.get_or_create(code="A_pack", defaults=dict(pattern=ShiftGroup.P3))[0]
        for w in range(7): ShiftRotation.objects.update_or_create(group=cls.gA, weekday=w, defaults=dict(shift=cls.pagi))
        mk = lambda nik, dept, **kw: Employee.objects.create(nik=nik, name=f"Emp {nik}", gender="L", department=dept, position=cls.pos, join_date=date(2020, 1, 1), **kw)
        cls.e1, cls.e2, cls.e3 = mk("001", cls.d1, shift_group=cls.gA), mk("002", cls.d1, shift_group=cls.gAp), mk("003", cls.d2, shift_group=cls.gA)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password="x", role=Role.DEPT_ADMIN, department=cls.d1)

    def post(self, **kw):
        return self.client.post("/requests/new/", {"reason": "x", "submit": "1", **kw})

    def test_legacy_types_not_offered_and_labels_merged(self):
        t = allowed_types(self.hrd)
        for gone in ("promosi", "demosi", "rotasi", "izin_khusus"): self.assertNotIn(gone, t)
        for keep in ("mutasi_dept", "mutasi_jabatan", "status_kerja", "shift"): self.assertIn(keep, t)
        self.client.force_login(self.hrd)
        self.assertEqual(self.post(type="promosi", nik="001", position=self.pos.pk, effective_date="2026-12-01").status_code, 200)
        self.assertFalse(ChangeRequest.objects.exists())

    def test_status_kerja_hrd_only_and_executes_with_history(self):
        self.client.force_login(self.adm)
        self.assertNotIn("status_kerja", allowed_types(self.adm))
        self.assertEqual(self.post(type="status_kerja", nik="001", pay_basis="harian", effective_date="2026-12-01").status_code, 200)
        self.assertFalse(ChangeRequest.objects.exists())
        self.client.force_login(self.hrd)
        self.assertEqual(self.post(type="status_kerja", nik="001", pay_basis="bulanan", effective_date="2026-12-01").status_code, 200)  # sama dengan saat ini
        self.assertEqual(self.post(type="status_kerja", nik="001", pay_basis="magang_harian", effective_date="2026-12-01").status_code, 302)
        r = ChangeRequest.objects.get(); self.assertEqual(r.payload["pay_basis_name"], "Magang Harian")
        services.transition(r, "approved", self.hrd); services.transition(r, "executed", self.hrd)
        self.e1.refresh_from_db(); self.assertEqual(self.e1.pay_basis, "magang_harian")
        h = EmployeeHistory.objects.get(field="pay_basis"); self.assertEqual((h.old_value, h.new_value), ("Bulanan", "Magang Harian"))

    def test_shift_change_targets_group_and_applies(self):
        self.client.force_login(self.hrd)
        f = self.client.get("/requests/new/").context["form"]
        self.assertEqual([g.label for g in f.fields["shift_group"].queryset], ["GS", "GS/N", "A7", "A7Pack"])
        self.assertEqual(self.post(type="shift", nik="001", shift_group=self.gA.pk, effective_date="2026-12-01").status_code, 200)  # sama
        self.assertEqual(self.post(type="shift", nik="001", shift_group=self.gGS.pk, effective_date="2026-12-01").status_code, 302)
        r = ChangeRequest.objects.get(); self.assertEqual(r.payload["shift_group_name"], "GS")
        services.transition(r, "approved", self.hrd); services.transition(r, "executed", self.hrd)
        self.e1.refresh_from_db(); self.assertEqual((self.e1.shift_group_id, self.e1.shift_id), (self.gGS.pk, self.gs16.pk))
        r2 = ChangeRequest.objects.create(type="shift", employee=self.e1, department=self.d1, status="approved", requested_by=self.hrd, payload={"shift_group_id": self.gAp.pk, "effective_date": "2026-12-02"})
        services.transition(r2, "executed", self.hrd)
        self.e1.refresh_from_db(); self.assertEqual((self.e1.shift_group_id, self.e1.shift_id), (self.gAp.pk, None))

    def test_info_panel_swap_shows_both_people_and_scope(self):
        self.client.force_login(self.hrd); d = next_tue().isoformat()
        j = self.client.get("/requests/info/", {"type": "tukar_shift", "nik": "001", "partner_nik": "002", "date": d}).json()
        self.assertEqual([b["title"] for b in j["blocks"]], ["Karyawan", "Rekan tukar", "Setelah ditukar"])
        self.assertIn(["Shift", "A7"], j["blocks"][0]["rows"]); self.assertIn(["Shift", "A7Pack"], j["blocks"][1]["rows"])
        j = self.client.get("/requests/info/", {"type": "shift", "nik": "001", "shift_group": self.gGSN.pk}).json()
        self.assertIn(["Shift", "A7 → GS/N"], j["blocks"][1]["rows"])
        self.client.force_login(self.adm)  # Admin Dept: karyawan departemen lain = tidak ditemukan
        self.assertEqual(self.client.get("/requests/info/", {"type": "tukar_shift", "nik": "003", "date": d}).json(), {"blocks": []})
        j = self.client.get("/requests/info/", {"type": "tukar_shift", "nik": "001", "partner_nik": "003", "date": d}).json()
        self.assertEqual([b["title"] for b in j["blocks"]], ["Karyawan"])

    def test_tukar_shift_target_is_group_and_lookup_shows_group_label(self):
        self.client.force_login(self.hrd); d = next_tue()
        r = self.post(type="tukar_shift", nik="002", date=d.isoformat(), shift_group=self.gA.pk)   # e2 kelompok A7Pack, tujuan A7 (Pagi, tiap hari)
        self.assertEqual(r.status_code, 302)
        q = ChangeRequest.objects.get(); self.assertEqual((q.payload["shift_id"], q.payload["shift_group_name"]), (self.pagi.pk, "A7"))
        j = self.client.get("/api/lookup/employees/", {"q": "002"}).json()["results"][0]
        self.assertEqual(j["shift"], "A7Pack")
        j = self.client.get("/requests/info/", {"type": "tukar_shift", "nik": "002", "date": d.isoformat(), "shift_group": self.gA.pk}).json()
        self.assertIn("A7 — Shift Pagi", j["blocks"][-1]["rows"][0][1])
