"""Putaran 17b — master shift baru (kelompok rotasi, GS, kode) × tukar shift/libur 1 orang (sendiri) dan 2 orang (dengan rekan)."""
import threading
from datetime import date, time, timedelta
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from apps.core.models import Role, User
from . import schedule, services
from .emp_forms import EmployeeForm, ShiftForm
from .models import ChangeRequest, Department, Employee, Position, Shift, ShiftAssignment, ShiftGroup, ShiftRotation
from .test_schedule_leave import Base, PW, TODAY, next_weekday

# Tabel rotasi uji (0=Senin … 6=Minggu). Kelompok A libur Rabu & Minggu, B libur Kamis & Minggu.
A = {0: "PAGI", 1: "SIANG", 2: None, 3: "SIANG", 4: "PAGI", 5: "PAGI", 6: None}
B = {0: "SIANG", 1: "PAGI", 2: "PAGI", 3: None, 4: "SIANG", 5: "SIANG", 6: None}


class RotBase(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pagi = Shift.objects.create(code="PAGI", name="Shift Pagi", start=time(7), end=time(15))
        cls.siang = Shift.objects.create(code="SIANG", name="Shift Siang", start=time(15), end=time(23))
        cls.malam = Shift.objects.create(code="MALAM", name="Shift Malam", start=time(23), end=time(7), crosses_midnight=True)
        cls.gs = Shift.objects.create(code="GS-12", name="GS 12", start=time(8), end=time(12), is_gs=True)
        cls.gA = ShiftGroup.objects.create(code="A7_pack", pattern=ShiftGroup.P2)
        cls.gB = ShiftGroup.objects.create(code="B7_pack", pattern=ShiftGroup.P2)
        sh = {"PAGI": cls.pagi, "SIANG": cls.siang, None: None}
        for grp, tbl in ((cls.gA, A), (cls.gB, B)):
            for w, code in tbl.items(): ShiftRotation.objects.create(group=grp, weekday=w, shift=sh[code])
        mk = lambda nik, dept, **kw: Employee.objects.create(nik=nik, name=f"Emp {nik}", gender="L", department=dept, position=cls.pos, join_date=date(2020, 1, 1), **kw)
        cls.e3, cls.e4, cls.e6 = mk("003", cls.d1, shift_group=cls.gA), mk("004", cls.d1, shift_group=cls.gB), mk("006", cls.d1, shift_group=cls.gA)
        cls.e5 = mk("005", cls.d2, shift_group=cls.gB)

    def post_req(self, **kw):
        data = {"type": "tukar_shift", "nik": "003", "date": next_weekday(0).isoformat(), "partner_nik": "004", "reason": "x", "submit": "1"}
        data.update(kw); return self.client.post("/requests/new/", data)

    def duo_shift_req(self, d, a=None, b=None, **kw):
        a, b = a or self.e3, b or self.e4
        return self.make_req("tukar_shift", {"date": d.isoformat(), "partner_id": b.pk, "partner_nik": b.nik, "partner_name": b.name, "reason": "x"}, emp=a, **kw)

    def duo_libur_req(self, d1, d2, a=None, b=None, **kw):
        a, b = a or self.e3, b or self.e4
        return self.make_req("tukar_libur", {"date": d1.isoformat(), "date_to": d2.isoformat(), "partner_id": b.pk, "partner_nik": b.nik, "partner_name": b.name, "reason": "x"}, emp=a, **kw)

    def do_exec(self, req):  # setujui lalu laksanakan sebagai HRD
        self.login("hrd"); self.act(req, "approved"); self.act(req, "executed"); req.refresh_from_db(); return req

    def sched(self, emp, d):
        x = schedule.effective_schedule(Employee.objects.get(pk=emp.pk), d); return "LIBUR" if x["off"] else x["shift"].code


# ------------------------------------------------------------------ jadwal dasar dari master shift baru
class RotationScheduleTests(RotBase):
    def test_group_member_follows_rotation_table(self):
        for w in range(7):
            d = next_weekday(w); self.assertEqual(self.sched(self.e3, d), A[w] or "LIBUR", w); self.assertEqual(self.sched(self.e4, d), B[w] or "LIBUR", w)

    def test_fixed_shift_employee_unchanged_sunday_off(self):
        self.assertEqual(schedule.effective_schedule(self.e1, next_weekday(1))["shift"], self.s_pagi)
        self.assertEqual(self.sched(self.e1, next_weekday(6)), "LIBUR")

    def test_group_without_row_for_that_day_falls_back_to_fixed(self):
        g = ShiftGroup.objects.create(code="C7_pack", pattern=ShiftGroup.P2); ShiftRotation.objects.create(group=g, weekday=0, shift=self.siang)
        e = Employee.objects.create(nik="900", name="X", gender="L", department=self.d1, position=self.pos, join_date=date(2020, 1, 1), shift=self.gs, shift_group=g)
        self.assertEqual(self.sched(e, next_weekday(0)), "SIANG")  # ada baris → rotasi
        self.assertEqual(self.sched(e, next_weekday(1)), "GS-12")  # tidak ada baris → shift tetap

    def test_override_beats_rotation_and_master_untouched(self):
        d = next_weekday(2)  # Rabu: libur kelompok A
        req = self.make_req("tukar_shift", {"date": d.isoformat(), "shift_id": self.pagi.pk}, emp=self.e3, status="executed")
        ShiftAssignment.objects.create(employee=self.e3, date=d, kind="shift", shift=self.pagi, request=req)
        self.assertEqual(self.sched(self.e3, d), "PAGI"); self.assertEqual(Employee.objects.get(pk=self.e3.pk).shift_group_id, self.gA.pk)

    def test_schedule_range_matches_effective_with_constant_queries(self):
        d = next_weekday(2); req = self.make_req("tukar_libur", {"date": d.isoformat(), "date_to": next_weekday(3).isoformat()}, emp=self.e3, status="executed")
        ShiftAssignment.objects.create(employee=self.e3, date=d, kind="off", request=req)
        e = Employee.objects.get(pk=self.e3.pk)
        with self.assertNumQueries(2):  # tabel rotasi + penyesuaian, berapa pun jumlah harinya
            rng = schedule.schedule_range(e, TODAY, 28)
        self.assertEqual(len(rng), 28)
        for x in rng: self.assertEqual((x["off"], x["shift"]), tuple(schedule.effective_schedule(e, x["date"])[k] for k in ("off", "shift")), x["date"])

    def test_rotation_row_validation(self):
        bad = lambda grp, sh: ShiftRotation(group=grp, weekday=0, shift=sh).clean()
        with self.assertRaises(ValidationError): bad(self.gA, self.gs)  # GS tidak ikut rotasi
        with self.assertRaises(ValidationError): bad(self.gA, self.malam)  # pola 2 shift tanpa Malam
        self.pagi.active = False
        with self.assertRaises(ValidationError): bad(self.gA, self.pagi)  # shift nonaktif
        p3 = ShiftGroup(code="A7", pattern=ShiftGroup.P3); p3.save(); ShiftRotation(group=p3, weekday=0, shift=self.malam).clean()  # pola 3 shift boleh Malam

    def test_employee_form_rejects_group_and_fixed_shift_together(self):
        f = EmployeeForm({"nik": "777", "name": "N", "gender": "L", "join_date": "2020-01-01", "department": self.d1.pk, "shift": self.gs.pk, "shift_group": self.gA.pk,
                          "status": "aktif"})
        self.assertFalse(f.is_valid()); self.assertIn("shift_group", f.errors)

    def test_master_shift_form_code_unique_uppercase_and_flags(self):
        f = ShiftForm({"name": "GS 14", "code": "gs-14", "start": "07:00", "end": "14:00", "is_gs": "on", "active": "on"})
        self.assertTrue(f.is_valid(), f.errors); s = f.save(); self.assertEqual((s.code, s.is_gs, s.active), ("GS-14", True, True))
        dup = ShiftForm({"name": "Lain", "code": "GS-14", "start": "08:00", "end": "16:00", "active": "on"}); self.assertFalse(dup.is_valid()); self.assertIn("code", dup.errors)

    def test_inactive_shift_not_offered_in_request_form(self):
        Shift.objects.filter(pk=self.malam.pk).update(active=False); self.login("adm1")
        html = self.client.get("/requests/new/").content.decode()
        self.assertIn("Shift Siang", html); self.assertNotIn("Shift Malam", html)

    def test_master_shift_list_shows_code_and_kind(self):
        self.login("hrd"); r = self.client.get("/master/shift/"); self.assertContains(r, "GS-12"); self.assertContains(r, "GS")


# ------------------------------------------------------------------ tukar 1 orang
class SoloSwapTests(RotBase):
    def test_solo_libur_rotation_employee_takes_shift_of_swapped_day(self):
        wed, thu = next_weekday(2), next_weekday(3)  # e3: Rabu libur, Kamis SIANG
        req = self.do_exec(self.make_req("tukar_libur", {"date": wed.isoformat(), "date_to": thu.isoformat(), "reason": "x"}, emp=self.e3))
        self.assertEqual(req.status, "executed")
        rows = {(a.date, a.kind, a.shift_id) for a in ShiftAssignment.objects.all()}
        self.assertEqual(rows, {(wed, "shift", self.siang.pk), (thu, "off", None)})  # masuk di shift hari yang ditukar, bukan "shift master" (kosong)
        self.assertEqual((self.sched(self.e3, wed), self.sched(self.e3, thu)), ("SIANG", "LIBUR"))
        self.assertEqual(Employee.objects.get(pk=self.e3.pk).shift_group_id, self.gA.pk)

    def test_solo_libur_form_requires_real_off_day_and_work_day(self):
        self.login("adm1")
        r = self.post_req(type="tukar_libur", partner_nik="", date=next_weekday(1).isoformat(), date_to=next_weekday(3).isoformat())  # Selasa bukan libur A
        self.assertContains(r, "bukan hari libur karyawan ini")
        r = self.post_req(type="tukar_libur", partner_nik="", date=next_weekday(2).isoformat(), date_to=next_weekday(6).isoformat())  # Minggu sudah libur
        self.assertContains(r, "sudah hari libur"); self.assertFalse(ChangeRequest.objects.exists())

    def test_solo_libur_form_accepts_valid_and_stores_no_partner(self):
        self.login("adm1"); r = self.post_req(type="tukar_libur", partner_nik="", date=next_weekday(2).isoformat(), date_to=next_weekday(3).isoformat())
        self.assertEqual(r.status_code, 302); q = ChangeRequest.objects.get()
        self.assertNotIn("partner_id", q.payload); self.assertEqual(q.status, "pending")

    def test_solo_libur_fixed_shift_employee_still_writes_work_and_off(self):  # perilaku lama (putaran 9) tidak berubah
        sun, mon = next_weekday(6), next_weekday(0, next_weekday(6))
        self.do_exec(self.swap_off_req(sun, mon)); self.assertEqual({(a.date, a.kind) for a in ShiftAssignment.objects.all()}, {(sun, "work"), (mon, "off")})

    def test_solo_shift_on_day_off_is_refused_use_tukar_libur(self):
        self.login("adm1"); r = self.post_req(partner_nik="", date=next_weekday(2).isoformat(), shift=self.pagi.pk)  # Rabu libur kelompok A
        self.assertContains(r, "hari libur karyawan"); self.assertFalse(ChangeRequest.objects.exists())

    def test_solo_shift_same_as_group_shift_refused(self):
        self.login("adm1"); r = self.post_req(partner_nik="", date=next_weekday(0).isoformat(), shift=self.pagi.pk)  # Senin e3 sudah PAGI
        self.assertContains(r, "Sama dengan shift reguler")

    def test_solo_shift_rotation_employee_executes_override_only(self):
        mon = next_weekday(0); self.do_exec(self.make_req("tukar_shift", {"date": mon.isoformat(), "shift_id": self.siang.pk}, emp=self.e3))
        self.assertEqual(self.sched(self.e3, mon), "SIANG"); self.assertEqual(self.sched(self.e3, mon + timedelta(days=7)), "PAGI")  # hanya tanggal itu

    def test_solo_libur_execute_refused_when_day_no_longer_off(self):
        wed, thu = next_weekday(2), next_weekday(3)
        req = self.make_req("tukar_libur", {"date": wed.isoformat(), "date_to": thu.isoformat()}, emp=self.e3, status="approved")
        Employee.objects.filter(pk=self.e3.pk).update(shift_group=self.gB)  # kelompok pindah: Rabu kini hari kerja
        with self.assertRaisesMessage(ValueError, "bukan hari libur"): services.transition(req, "executed", self.hrd)
        req.refresh_from_db(); self.assertEqual(req.status, "approved"); self.assertEqual(ShiftAssignment.objects.count(), 0)


# ------------------------------------------------------------------ tukar 2 orang
class PartnerSwapTests(RotBase):
    def test_form_duo_shift_valid_stores_partner_and_needs_no_target_shift(self):
        self.login("adm1"); r = self.post_req(date=next_weekday(0).isoformat())  # Senin: e3 PAGI, e4 SIANG; kolom shift kosong
        self.assertEqual(r.status_code, 302); q = ChangeRequest.objects.get()
        self.assertEqual((q.employee_id, q.payload["partner_id"], q.payload["partner_nik"], q.payload["partner_name"]), (self.e3.pk, self.e4.pk, "004", self.e4.name))
        self.assertNotIn("shift_id", q.payload); self.assertEqual(q.status, "pending")

    def test_execute_duo_shift_swaps_both_and_leaves_master(self):
        mon = next_weekday(0); req = self.do_exec(self.duo_shift_req(mon))
        self.assertEqual(req.status, "executed")
        self.assertEqual({(a.employee_id, a.shift_id) for a in ShiftAssignment.objects.all()}, {(self.e3.pk, self.siang.pk), (self.e4.pk, self.pagi.pk)})
        self.assertEqual((self.sched(self.e3, mon), self.sched(self.e4, mon)), ("SIANG", "PAGI"))
        self.assertEqual((self.sched(self.e3, mon + timedelta(days=7)), self.sched(self.e4, mon + timedelta(days=7))), ("PAGI", "SIANG"))  # minggu depan normal
        for e, g in ((self.e3, self.gA), (self.e4, self.gB)): self.assertEqual(Employee.objects.get(pk=e.pk).shift_group_id, g.pk)
        self.assertTrue(all(a.request_id == req.pk and a.created_by_id == self.hrd.pk for a in ShiftAssignment.objects.all()))

    def test_execute_duo_libur_exchanges_off_days(self):
        wed, thu = next_weekday(2), next_weekday(3)  # e3 libur Rabu (e4 masuk PAGI); e4 libur Kamis (e3 masuk SIANG)
        self.assertEqual((self.sched(self.e3, wed), self.sched(self.e4, wed), self.sched(self.e3, thu), self.sched(self.e4, thu)), ("LIBUR", "PAGI", "SIANG", "LIBUR"))
        req = self.do_exec(self.duo_libur_req(wed, thu)); self.assertEqual(req.status, "executed"); self.assertEqual(ShiftAssignment.objects.filter(request=req).count(), 4)
        self.assertEqual((self.sched(self.e3, wed), self.sched(self.e4, wed), self.sched(self.e3, thu), self.sched(self.e4, thu)), ("PAGI", "LIBUR", "LIBUR", "SIANG"))

    def test_form_duo_libur_valid(self):
        self.login("adm1"); r = self.post_req(type="tukar_libur", date=next_weekday(2).isoformat(), date_to=next_weekday(3).isoformat())
        self.assertEqual(r.status_code, 302); self.assertEqual(ChangeRequest.objects.get().payload["partner_id"], self.e4.pk)

    def test_form_duo_libur_needs_both_directions(self):
        self.login("adm1")
        r = self.post_req(type="tukar_libur", date=next_weekday(2).isoformat(), date_to=next_weekday(1).isoformat())  # Selasa: rekan tidak libur
        self.assertContains(r, "harus sedang libur"); self.assertFalse(ChangeRequest.objects.exists())
        r = self.post_req(type="tukar_libur", date=next_weekday(1).isoformat(), date_to=next_weekday(3).isoformat())  # Selasa: pemohon tidak libur
        self.assertContains(r, "harus sedang libur")

    def test_form_duo_shift_rejections(self):
        self.login("adm1")
        self.assertContains(self.post_req(date=next_weekday(2).isoformat()), "sama-sama masuk")  # Rabu e3 libur
        self.assertContains(self.post_req(nik="003", partner_nik="006", date=next_weekday(0).isoformat()), "shift yang sama")  # satu kelompok → shift sama
        self.assertContains(self.post_req(partner_nik="003"), "harus karyawan lain")
        self.assertContains(self.post_req(partner_nik="999"), "tidak ditemukan")
        self.assertFalse(ChangeRequest.objects.exists())

    def test_dept_admin_cannot_pick_partner_from_other_department_hrd_can(self):
        self.login("adm1"); self.assertContains(self.post_req(partner_nik="005"), "tidak ditemukan")  # e5 di departemen lain: tidak bocor
        self.login("hrd"); self.assertEqual(self.post_req(partner_nik="005", date=next_weekday(0).isoformat()).status_code, 302)  # B Senin SIANG vs A PAGI

    def test_partner_busy_with_own_request_blocks_same_date(self):
        mon = next_weekday(0); self.duo_shift_req(mon, a=self.e4, b=self.e6, status="pending")  # e4 sudah dipakai sebagai pemohon
        self.login("adm1"); self.assertContains(self.post_req(date=mon.isoformat()), "sudah dipakai pengajuan tukar jadwal lain")
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_partner_busy_as_partner_of_other_request_blocks_same_date(self):
        mon = next_weekday(0); self.duo_shift_req(mon, a=self.e6, b=self.e4, status="approved")  # e4 terikat sebagai REKAN pada pengajuan lain
        self.login("adm1"); r = self.post_req(date=mon.isoformat()); self.assertContains(r, "sudah dipakai pengajuan tukar jadwal lain"); self.assertContains(r, self.e4.name)

    def test_partner_freed_after_cancel(self):
        mon = next_weekday(0); old = self.duo_shift_req(mon, a=self.e6, b=self.e4, status="pending"); self.login("adm1")
        self.act(old, "cancelled", "batal"); self.assertEqual(self.post_req(date=mon.isoformat()).status_code, 302)

    def test_partner_on_leave_that_day_blocks(self):
        mon = next_weekday(0); self.make_req("izin", {"start_date": mon.isoformat(), "end_date": mon.isoformat()}, status="approved", emp=self.e4)
        self.login("adm1"); r = self.post_req(date=mon.isoformat()); self.assertContains(r, "sedang izin/cuti/sakit"); self.assertContains(r, self.e4.name)

    def test_execute_all_or_nothing_when_partner_has_override(self):
        mon = next_weekday(0); req = self.duo_shift_req(mon, status="approved")
        other = self.make_req("tukar_shift", {"date": mon.isoformat(), "shift_id": self.pagi.pk}, emp=self.e4, status="executed")
        ShiftAssignment.objects.create(employee=self.e4, date=mon, kind="shift", shift=self.pagi, request=other)
        with self.assertRaisesMessage(ValueError, "sudah punya penyesuaian"): services.transition(req, "executed", self.hrd)
        req.refresh_from_db(); self.assertEqual(req.status, "approved"); self.assertEqual(ShiftAssignment.objects.count(), 1)  # pemohon tidak ikut tertulis

    def test_execute_refused_when_partner_inactive(self):
        req = self.duo_shift_req(next_weekday(0), status="approved"); Employee.objects.filter(pk=self.e4.pk).update(status="nonaktif")
        with self.assertRaisesMessage(ValueError, "Rekan tukar"): services.transition(req, "executed", self.hrd)
        self.assertEqual(ShiftAssignment.objects.count(), 0)

    def test_execute_refused_when_schedule_changed_since_approval(self):
        mon = next_weekday(0); req = self.duo_shift_req(mon, status="approved")
        Employee.objects.filter(pk=self.e4.pk).update(shift_group=self.gA)  # e4 pindah ke kelompok A → shift sama dengan e3
        with self.assertRaisesMessage(ValueError, "shift yang sama"): services.transition(req, "executed", self.hrd)
        self.assertEqual(ShiftAssignment.objects.count(), 0)

    def test_execute_refused_when_date_passed(self):
        req = self.duo_shift_req(TODAY - timedelta(days=1), status="approved")
        with self.assertRaisesMessage(ValueError, "sudah lewat"): services.transition(req, "executed", self.hrd)

    def test_execute_is_not_repeatable(self):
        req = self.do_exec(self.duo_shift_req(next_weekday(0)))
        with self.assertRaises(ValueError): services.transition(req, "executed", self.hrd)  # final → tidak bisa dilaksanakan lagi
        self.assertEqual(ShiftAssignment.objects.count(), 2)

    def test_detail_page_shows_partner_mode_and_both_rows(self):
        req = self.do_exec(self.duo_shift_req(next_weekday(0))); r = self.client.get(f"/requests/{req.pk}/")
        self.assertContains(r, "dengan rekan (2 orang)"); self.assertContains(r, "Penyesuaian jadwal yang ditulis")
        self.assertContains(r, self.e3.name); self.assertContains(r, self.e4.name)

    def test_detail_page_solo_mode_label(self):
        req = self.swap_shift_req(next_weekday(1), status="approved"); self.login("hrd"); r = self.client.get(f"/requests/{req.pk}/")
        self.assertContains(r, "sendiri (1 orang)"); self.assertContains(r, "tulis penyesuaian jadwal"); self.assertNotContains(r, "untuk kedua karyawan")

    def test_approve_detail_button_mentions_both_employees_for_duo(self):
        req = self.duo_shift_req(next_weekday(0), status="approved"); self.login("hrd"); self.assertContains(self.client.get(f"/requests/{req.pk}/"), "untuk kedua karyawan")

    def test_employee_detail_shows_14_day_schedule_with_rotation_and_swap_link(self):
        mon = next_weekday(0); req = self.do_exec(self.duo_shift_req(mon))
        r = self.client.get(f"/employees/{self.e3.pk}/"); self.assertContains(r, "Jadwal 14 hari ke depan"); self.assertContains(r, "Rotasi kelompok")
        if mon <= TODAY + timedelta(days=13): self.assertContains(r, f"Tukar #{req.pk}")

    def test_dept_admin_sees_schedule_only_in_scope(self):
        self.login("adm2"); self.assertEqual(self.client.get(f"/employees/{self.e3.pk}/").status_code, 404)  # e3 departemen lain

    def test_request_list_shows_partner(self):
        self.duo_shift_req(next_weekday(0)); self.login("hrd"); r = self.client.get("/requests/")
        self.assertContains(r, "⇄ 004"); self.assertContains(r, self.e4.name)

    def test_partner_request_visible_only_in_requester_scope(self):
        req = self.duo_shift_req(next_weekday(0), status="pending"); self.login("adm2"); self.assertEqual(self.client.get(f"/requests/{req.pk}/").status_code, 404)

    def test_form_page_has_partner_field_and_mode_script(self):
        self.login("adm1"); r = self.client.get("/requests/new/")
        self.assertContains(r, 'name="partner_nik"'); self.assertContains(r, "Kosong = tukar sendiri (1 orang)"); self.assertContains(r, "Tanggal libur rekan")

    def test_db_constraints_still_one_row_per_employee_date(self):
        mon = next_weekday(0); self.do_exec(self.duo_shift_req(mon))
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftAssignment.objects.create(employee=self.e3, date=mon, kind="off", request=ChangeRequest.objects.first())


# ------------------------------------------------------------------ jadwal mingguan + notifikasi pelaksanaan
class WeekPageAndNotifyTests(RotBase):
    def test_grid_matches_effective_schedule_with_constant_queries(self):
        mon = next_weekday(0); self.do_exec(self.duo_shift_req(mon)); start = mon
        emps = list(Employee.objects.filter(pk__in=[self.e1.pk, self.e3.pk, self.e4.pk, self.e6.pk]).select_related("shift", "shift_group"))
        with self.assertNumQueries(2):
            grid = schedule.schedule_grid(emps, start, 7)
        for e, cells in grid:
            for c in cells:
                x = schedule.effective_schedule(e, c["date"]); self.assertEqual((c["off"], c["shift"], c["source"]), (x["off"], x["shift"], x["source"]), (e.nik, c["date"]))

    def test_page_access_and_scope(self):
        self.client.logout(); self.assertEqual(self.client.get("/schedule/").status_code, 302)
        self.login("poli"); self.assertEqual(self.client.get("/schedule/").status_code, 403)
        self.login("hrd"); r = self.client.get("/schedule/"); self.assertContains(r, "Emp 003"); self.assertContains(r, "Emp 005")
        self.login("adm2"); r = self.client.get("/schedule/?department=%d" % self.d1.pk)  # filter departemen diabaikan untuk Admin Dept
        self.assertContains(r, "Emp 005"); self.assertNotContains(r, "Emp 003")

    def test_page_shows_swap_marker_filters_and_bad_input(self):
        mon = next_weekday(0); req = self.do_exec(self.duo_shift_req(mon)); self.login("hrd")
        r = self.client.get(f"/schedule/?start={mon.isoformat()}"); self.assertContains(r, f"Hasil tukar #{req.pk}"); self.assertContains(r, "SIANG")
        self.assertNotContains(self.client.get(f"/schedule/?start={mon}&group={self.gB.pk}"), "Emp 003")
        for bad in ("start=bukan-tanggal", "start=0001-01-01", "group=abc", "department=99999999999999", "q=%00"):
            self.assertEqual(self.client.get("/schedule/?" + bad).status_code in (200, 400), True, bad)

    def test_schedule_not_in_sidebar_but_linked_from_employees_page(self):  # putaran 18: submenu Jadwal Shift dihapus
        for u, want in (("hrd", True), ("adm1", True), ("poli", False)):
            self.login(u)
            self.assertNotIn('href="/schedule/"', self.client.get("/").content.decode(), u)
            self.assertEqual('href="/schedule/"' in self.client.get("/employees/").content.decode(), want, u)

    def test_notify_requester_and_partner_dept_admin_on_execute(self):
        req = self.do_exec(self.duo_shift_req(next_weekday(0), a=self.e3, b=self.e5))  # rekan di departemen lain
        from apps.core.models import Notification
        self.assertTrue(Notification.objects.filter(user=self.adm1, link=f"/requests/{req.pk}", title__contains="dilaksanakan").exists())
        n = Notification.objects.get(user=self.adm2); self.assertEqual(n.link, f"/employees/{self.e5.pk}/")
        self.login("adm2"); self.assertEqual(self.client.get(n.link).status_code, 200)  # tautan bisa dibuka (masih dalam scope)

    def test_no_partner_notice_for_solo_and_no_duplicate_for_requester(self):
        from apps.core.models import Notification
        self.do_exec(self.swap_shift_req(next_weekday(1))); self.assertEqual(Notification.objects.filter(user=self.adm2).count(), 0)
        req = self.do_exec(self.duo_shift_req(next_weekday(0))); self.assertEqual(Notification.objects.filter(user=self.adm1, link=f"/requests/{req.pk}", title__contains="dilaksanakan").count(), 1)
        self.assertFalse(Notification.objects.filter(user=self.adm1, link__startswith="/employees/").exists())  # adm1 = pemohon, tidak ditulis dobel


# ------------------------------------------------------------------ konkurensi (hanya jalan di PostgreSQL: butuh select_for_update)
class PartnerConcurrencyTests(TransactionTestCase):
    """Pelaksanaan tukar 2 orang bersamaan: rekan yang sama tidak boleh tertulis ganda, dan penguncian dua karyawan tidak boleh saling deadlock."""
    def setUp(self):
        d = Department.objects.create(code="Z", name="Z"); pos = Position.objects.create(name="S")
        self.sh = [Shift.objects.create(code=c, name=c, start=time(h), end=time(h + 1)) for c, h in (("P", 7), ("S", 12), ("M", 18))]
        self.a, self.b, self.c = (Employee.objects.create(nik=n, name=f"K{n}", gender="L", department=d, position=pos, shift=sh, join_date=date(2020, 1, 1)) for n, sh in zip("123", self.sh))
        self.hrd1, self.hrd2 = User.objects.create_user("h1", password=PW, role=Role.HRD), User.objects.create_user("h2", password=PW, role=Role.HRD)
        self.adm = User.objects.create_user("a1", password=PW, role=Role.DEPT_ADMIN, department=d)
        self.d1, self.d2 = next_weekday(1), next_weekday(2)  # Selasa & Rabu (bukan libur reguler)

    def req(self, a, b, d):
        return ChangeRequest.objects.create(type="tukar_shift", employee=a, department=a.department, status="approved", requested_by=self.adm,
                                            payload={"date": d.isoformat(), "partner_id": b.pk, "partner_nik": b.nik, "partner_name": b.name, "reason": "x"})

    def race(self, jobs):
        barrier, results = threading.Barrier(len(jobs)), []
        def worker(req, user):
            try: barrier.wait(timeout=5); services.transition(req, "executed", user); results.append("ok")
            except ValueError: results.append("ditolak")
            except Exception as ex: results.append(f"ERROR {type(ex).__name__}: {ex}")
            finally: connection.close()
        ts = [threading.Thread(target=worker, args=j) for j in jobs]; [t.start() for t in ts]; [t.join(20) for t in ts]
        return sorted(results)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_swaps_sharing_a_partner_same_date_only_one_applied(self):
        r1, r2 = self.req(self.a, self.b, self.d1), self.req(self.c, self.b, self.d1)  # B diminta bertukar dengan A dan C pada hari yang sama
        self.assertEqual(self.race([(r1, self.hrd1), (r2, self.hrd2)]), ["ditolak", "ok"])
        self.assertEqual(ShiftAssignment.objects.filter(employee=self.b).count(), 1); self.assertEqual(ShiftAssignment.objects.count(), 2)  # tidak ada baris setengah jadi

    @skipUnlessDBFeature("has_select_for_update")
    def test_opposite_lock_order_swaps_do_not_deadlock(self):
        r1, r2 = self.req(self.a, self.b, self.d1), self.req(self.b, self.a, self.d2)  # (A,B) dan (B,A) bersamaan, tanggal berbeda
        self.assertEqual(self.race([(r1, self.hrd1), (r2, self.hrd2)]), ["ok", "ok"])
        self.assertEqual(ShiftAssignment.objects.count(), 4)


class GsAndGroupCodeTests(TestCase):
    """Putaran 18: GS 08–16, sebelum libur GS jadi GS-14/GS-12; kode kelompok A7–G7 (3 shift) dan A7_pack–G7_pack (2 shift)."""
    @classmethod
    def setUpTestData(cls):
        cls.d = Department.objects.create(code="GS", name="Dept GS")
        cls.g16 = Shift.objects.create(code="GS-16", name="GS 16", start=time(8), end=time(16), is_gs=True)
        cls.g14 = Shift.objects.create(code="GS-14", name="GS 14", start=time(8), end=time(14), is_gs=True)
        cls.g12 = Shift.objects.create(code="GS-12", name="GS 12", start=time(8), end=time(12), is_gs=True)
        mk = lambda n, short: Employee.objects.create(nik=n, name=n, gender="L", join_date=date(2024, 1, 1), department=cls.d, shift=cls.g16, gs_short=short)
        cls.a, cls.b = mk("G01", "14"), mk("G02", "12")

    def day(self, wd):  # tanggal depan dengan weekday tertentu
        d = date.today() + timedelta(days=1)
        while d.weekday() != wd: d += timedelta(days=1)
        return d

    def test_gs_normal_days_are_8_to_16(self):
        for wd in range(0, 5):  # Senin–Jumat (Sabtu = sebelum libur Minggu)
            for e in (self.a, self.b): self.assertEqual(schedule.effective_schedule(e, self.day(wd))["shift"].code, "GS-16", (e.nik, wd))

    def test_gs_day_before_off_day_is_shortened_per_employee(self):
        sat = self.day(5)
        self.assertEqual(schedule.effective_schedule(self.a, sat)["shift"].code, "GS-14")
        self.assertEqual(schedule.effective_schedule(self.b, sat)["shift"].code, "GS-12")
        self.assertTrue(schedule.effective_schedule(self.a, self.day(6))["off"])  # Minggu libur

    def test_range_and_grid_agree_with_single_day(self):
        mon = self.day(0)
        for e, cells in schedule.schedule_grid([self.a, self.b], mon, 7):
            for c in cells: self.assertEqual(c["shift"], schedule.effective_schedule(e, c["date"])["shift"], (e.nik, c["date"]))
        self.assertEqual([c["shift"].code for c in schedule.schedule_range(self.b, mon, 7) if c["shift"]], ["GS-16"] * 5 + ["GS-12"])

    def test_missing_short_shift_falls_back_to_gs_shift(self):
        Shift.objects.filter(code__in=("GS-14", "GS-12")).delete()
        self.assertEqual(schedule.effective_schedule(self.a, self.day(5))["shift"].code, "GS-16")

    def test_group_code_suffix_rule(self):
        from django.db import IntegrityError, transaction
        ShiftGroup.objects.create(code="C7", pattern=ShiftGroup.P3); ShiftGroup.objects.create(code="C7_pack", pattern=ShiftGroup.P2)
        for code, pat in (("D7_pack", ShiftGroup.P3), ("D7", ShiftGroup.P2)):
            with self.assertRaises(IntegrityError), transaction.atomic(): ShiftGroup.objects.create(code=code, pattern=pat)
