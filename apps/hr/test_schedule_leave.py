"""Putaran 9 — sisa Tahap 3: penerapan tukar shift/libur (ShiftAssignment), saldo cuti (LeaveLedger), pembatalan pengajuan."""
import threading
from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from apps.core.models import AuditLog, Notification, Role, User
from . import leave, schedule, services
from .models import ChangeRequest, Department, Employee, LeaveLedger, Position, Shift, ShiftAssignment

PW = "kata-sandi-panjang-123"
TODAY = date.today()


def next_weekday(n, start=None):
    """Tanggal terdekat (>= start, bawaan besok) dengan weekday n (0=Senin … 6=Minggu)."""
    d = (start or TODAY) + timedelta(days=1)
    while d.weekday() != n: d += timedelta(days=1)
    return d


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.pos = Position.objects.create(name="Staff")
        cls.s_pagi = Shift.objects.create(name="Pagi", start=time(7), end=time(15))
        cls.s_malam = Shift.objects.create(name="Malam", start=time(23), end=time(7), crosses_midnight=True)
        mk = lambda nik, d, **kw: Employee.objects.create(nik=nik, name=f"Emp {nik}", gender="L", department=d, position=cls.pos,
                                                          join_date=kw.pop("join", date(2020, 1, 1)), shift=cls.s_pagi, **kw)
        cls.e1, cls.e2 = mk("001", cls.d1), mk("002", cls.d2)
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.adm1 = User.objects.create_user("adm1", password=PW, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.adm1b = User.objects.create_user("adm1b", password=PW, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.adm2 = User.objects.create_user("adm2", password=PW, role=Role.DEPT_ADMIN, department=cls.d2)
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI)

    def login(self, name): self.client.force_login(User.objects.get(username=name))

    def grant(self, emp=None, year=2026, days=12): return LeaveLedger.objects.create(employee=emp or self.e1, year=year, kind="grant", days=days)

    def make_req(self, type_, payload, status="pending", emp=None, by=None):
        emp = emp or self.e1
        return ChangeRequest.objects.create(type=type_, employee=emp, department=emp.department, status=status, payload=payload, requested_by=by or self.adm1)

    def swap_shift_req(self, d, shift=None, **kw):
        sh = shift or self.s_malam
        return self.make_req("tukar_shift", {"date": d.isoformat(), "shift_id": sh.pk, "shift_name": sh.name, "reason": "x"}, **kw)

    def swap_off_req(self, d, d_to, **kw):
        return self.make_req("tukar_libur", {"date": d.isoformat(), "date_to": d_to.isoformat(), "reason": "x"}, **kw)

    def cuti_req(self, start, end, **kw): return self.make_req("cuti", {"start_date": start, "end_date": end, "reason": "x"}, **kw)

    def act(self, req, action, note=""): return self.client.post(f"/requests/{req.pk}/{action}/", {"note": note})

    def msgs(self, r): return [str(m) for m in r.context["messages"]] if r.context else []


# ------------------------------------------------------------------ jadwal
class ScheduleTests(Base):
    def test_master_shift_untouched_and_no_rows_before_execute(self):
        d = next_weekday(1); req = self.swap_shift_req(d)
        self.login("hrd"); self.act(req, "approved")
        self.assertEqual(ShiftAssignment.objects.count(), 0)  # disetujui ≠ dilaksanakan: jadwal belum berubah (VISION)
        self.assertEqual(schedule.effective_schedule(self.e1, d)["shift"], self.s_pagi)

    def test_execute_tukar_shift_writes_override_not_master(self):
        d = next_weekday(1); req = self.swap_shift_req(d)
        self.login("hrd"); self.act(req, "approved"); self.act(req, "executed")
        req.refresh_from_db(); self.assertEqual(req.status, "executed")
        a = ShiftAssignment.objects.get(); self.assertEqual((a.employee_id, a.date, a.kind, a.shift_id, a.request_id, a.created_by_id),
                                                           (self.e1.pk, d, "shift", self.s_malam.pk, req.pk, self.hrd.pk))
        self.e1.refresh_from_db(); self.assertEqual(self.e1.shift_id, self.s_pagi.pk)  # master tidak berubah
        eff = schedule.effective_schedule(self.e1, d); self.assertEqual((eff["shift"], eff["source"], eff["off"]), (self.s_malam, "override", False))
        self.assertEqual(schedule.effective_schedule(self.e1, d + timedelta(days=1))["source"], "master")  # hari lain tidak terpengaruh
        self.assertContains(self.client.get(f"/requests/{req.pk}/"), "Penyesuaian jadwal yang ditulis")

    def test_execute_tukar_libur_moves_off_day(self):
        sun, mon = next_weekday(6), next_weekday(0, next_weekday(6))  # Minggu libur reguler dipindah ke Senin berikutnya
        req = self.swap_off_req(sun, mon); self.login("hrd"); self.act(req, "approved"); self.act(req, "executed")
        self.assertEqual({(a.date, a.kind) for a in ShiftAssignment.objects.all()}, {(sun, "work"), (mon, "off")})
        self.assertFalse(schedule.effective_schedule(self.e1, sun)["off"]); self.assertEqual(schedule.effective_schedule(self.e1, sun)["shift"], self.s_pagi)
        self.assertTrue(schedule.effective_schedule(self.e1, mon)["off"])

    def test_master_sunday_is_off_by_default(self):
        self.assertTrue(schedule.effective_schedule(self.e1, next_weekday(6))["off"])
        self.assertFalse(schedule.effective_schedule(self.e1, next_weekday(2))["off"])

    def test_execute_refused_when_date_already_passed(self):
        req = self.swap_shift_req(TODAY - timedelta(days=1), status="approved"); self.login("hrd")
        r = self.act(req, "executed", ); req.refresh_from_db()
        self.assertEqual(req.status, "approved"); self.assertEqual(ShiftAssignment.objects.count(), 0)
        self.assertTrue(any("sudah lewat" in m for m in self.msgs(self.client.get(f"/requests/{req.pk}/"))) or r.status_code == 302)

    def test_execute_rolls_back_atomically_when_second_date_conflicts(self):
        sun, mon = next_weekday(6), next_weekday(0, next_weekday(6))
        other = self.swap_shift_req(mon, status="executed")
        ShiftAssignment.objects.create(employee=self.e1, date=mon, kind="shift", shift=self.s_malam, request=other)
        req = self.swap_off_req(sun, mon, status="approved")
        with self.assertRaisesMessage(ValueError, "sudah punya penyesuaian"): services.transition(req, "executed", self.hrd)
        req.refresh_from_db(); self.assertEqual(req.status, "approved")
        self.assertEqual(ShiftAssignment.objects.count(), 1)  # tidak ada baris setengah jadi (tanggal pertama ikut dibatalkan)

    def test_execute_refused_for_inactive_employee(self):
        req = self.swap_shift_req(next_weekday(1), status="approved"); Employee.objects.filter(pk=self.e1.pk).update(status="nonaktif")
        with self.assertRaisesMessage(ValueError, "tidak aktif"): services.transition(req, "executed", self.hrd)

    def test_execute_refused_if_target_equals_current_master_shift(self):
        req = self.swap_shift_req(next_weekday(1), shift=self.s_malam, status="approved")
        Employee.objects.filter(pk=self.e1.pk).update(shift=self.s_malam)  # master berubah setelah pengajuan dibuat
        with self.assertRaisesMessage(ValueError, "sama dengan shift reguler"): services.transition(req, "executed", self.hrd)

    # --- validasi form
    def post_swap(self, **kw):
        data = {"type": "tukar_shift", "nik": "001", "date": next_weekday(1).isoformat(), "shift": self.s_malam.pk, "reason": "x", "submit": "1"}
        data.update(kw); return self.client.post("/requests/new/", data)

    def test_form_rejects_past_date(self):
        self.login("adm1"); r = self.post_swap(date=(TODAY - timedelta(days=1)).isoformat())
        self.assertContains(r, "sudah lewat"); self.assertFalse(ChangeRequest.objects.exists())

    def test_form_rejects_same_as_master_shift(self):
        self.login("adm1"); r = self.post_swap(shift=self.s_pagi.pk); self.assertContains(r, "Sama dengan shift reguler")

    def test_form_rejects_tukar_libur_same_day(self):
        self.login("adm1"); d = next_weekday(6).isoformat()
        r = self.post_swap(type="tukar_libur", date=d, date_to=d); self.assertContains(r, "Harus berbeda")

    def test_form_rejects_duplicate_live_swap_same_date(self):
        d = next_weekday(1); self.swap_shift_req(d, status="pending"); self.login("adm1")
        r = self.post_swap(date=d.isoformat()); self.assertContains(r, "sudah dipakai pengajuan tukar jadwal lain")
        self.assertEqual(ChangeRequest.objects.count(), 1)

    def test_form_allows_same_date_after_cancel(self):
        d = next_weekday(1); old = self.swap_shift_req(d, status="pending"); self.login("adm1")
        self.act(old, "cancelled", "salah tanggal"); r = self.post_swap(date=d.isoformat()); self.assertEqual(r.status_code, 302)
        self.assertEqual(ChangeRequest.objects.filter(status="pending").count(), 1)

    def test_form_rejects_date_with_existing_assignment(self):
        d = next_weekday(1); done = self.swap_shift_req(d, status="executed")
        ShiftAssignment.objects.create(employee=self.e1, date=d, kind="shift", shift=self.s_malam, request=done)
        self.login("adm1"); self.assertContains(self.post_swap(date=d.isoformat()), "punya penyesuaian")

    def test_form_rejects_swap_while_on_leave(self):
        d = next_weekday(2); self.cuti_req(d.isoformat(), d.isoformat(), status="approved"); self.login("adm1")
        self.assertContains(self.post_swap(date=d.isoformat()), "sedang izin/cuti/sakit")

    def test_dept_admin_cannot_swap_other_department_employee(self):
        self.login("adm2"); r = self.post_swap(nik="001"); self.assertContains(r, "tidak ditemukan")

    # --- integritas DB
    def test_assignment_is_append_only_and_unique(self):
        d = next_weekday(1); req = self.swap_shift_req(d, status="executed")
        a = ShiftAssignment.objects.create(employee=self.e1, date=d, kind="shift", shift=self.s_malam, request=req)
        a.kind = "off"
        with self.assertRaises(PermissionError): a.save()
        with self.assertRaises(PermissionError): a.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftAssignment.objects.create(employee=self.e1, date=d, kind="work", request=req)  # satu baris per karyawan+tanggal

    def test_db_requires_shift_iff_kind_shift(self):
        req = self.swap_shift_req(next_weekday(1), status="executed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftAssignment.objects.create(employee=self.e1, date=next_weekday(2), kind="shift", shift=None, request=req)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShiftAssignment.objects.create(employee=self.e1, date=next_weekday(3), kind="off", shift=self.s_malam, request=req)

    def test_employee_detail_shows_schedule_to_scope_but_not_poli(self):
        d = next_weekday(1); req = self.swap_shift_req(d, status="executed")
        ShiftAssignment.objects.create(employee=self.e1, date=d, kind="shift", shift=self.s_malam, request=req)
        self.login("adm1"); self.assertContains(self.client.get(f"/employees/{self.e1.pk}/"), "Malam")
        self.login("adm2"); self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/").status_code, 404)
        self.login("poli"); self.assertNotContains(self.client.get(f"/employees/{self.e1.pk}/"), "Penyesuaian jadwal")


# ------------------------------------------------------------------ saldo cuti
class LeaveCountTests(Base):
    def test_sunday_not_counted(self):
        self.assertEqual(leave.charge_by_year(date(2026, 11, 2), date(2026, 11, 4)), {2026: 3})  # Sen–Rab
        self.assertEqual(leave.charge_by_year(date(2026, 11, 7), date(2026, 11, 9)), {2026: 2})  # Sab, (Min), Sen
        self.assertEqual(leave.charge_by_year(date(2026, 11, 8), date(2026, 11, 8)), {})          # hanya Minggu

    def test_cross_year_split(self):
        self.assertEqual(leave.charge_by_year(date(2026, 12, 30), date(2027, 1, 4)), {2026: 2, 2027: 3})  # 30,31 | 1(Jum),2(Sab),4(Sen)

    @override_settings(REGULAR_OFF_WEEKDAYS=(5, 6))
    def test_setting_changes_counting(self): self.assertEqual(leave.charge_by_year(date(2026, 11, 2), date(2026, 11, 8)), {2026: 5})

    def test_months_of_service(self):
        self.assertEqual(leave.months_of_service(date(2025, 1, 15), date(2026, 1, 14)), 11)
        self.assertEqual(leave.months_of_service(date(2025, 1, 15), date(2026, 1, 15)), 12)


class LeaveWorkflowTests(Base):
    def post_cuti(self, start="2026-11-02", end="2026-11-04", nik="001"):
        return self.client.post("/requests/new/", {"type": "cuti", "nik": nik, "start_date": start, "end_date": end, "reason": "x", "submit": "1"})

    def test_blocked_without_balance(self):
        self.login("adm1"); r = self.post_cuti(); self.assertContains(r, "Saldo cuti 2026 tidak cukup"); self.assertFalse(ChangeRequest.objects.exists())

    def test_blocked_when_more_than_balance(self):
        self.grant(days=2); self.login("adm1"); r = self.post_cuti()
        self.assertContains(r, "tersedia 2 hari"); self.assertContains(r, "dibutuhkan 3 hari"); self.assertFalse(ChangeRequest.objects.exists())

    def test_range_with_only_sunday_rejected(self):
        self.grant(); self.login("adm1"); self.assertContains(self.post_cuti("2026-11-08", "2026-11-08"), "tidak berisi hari kerja")

    def test_pending_requests_reserve_balance(self):
        self.grant(days=4); self.login("adm1")
        self.assertEqual(self.post_cuti("2026-11-02", "2026-11-04").status_code, 302)       # 3 hari → sisa tersedia 1
        self.assertContains(self.post_cuti("2026-11-10", "2026-11-11"), "tersedia 1 hari")  # 2 hari > 1
        self.assertEqual(leave.available(self.e1, 2026), Decimal("1")); self.assertEqual(leave.balance(self.e1, 2026), Decimal("4"))  # belum dipotong

    def test_execute_deducts_and_records_ledger(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved"); self.login("hrd")
        self.act(req, "executed"); req.refresh_from_db(); self.assertEqual(req.status, "executed")
        row = LeaveLedger.objects.get(kind="use"); self.assertEqual((row.year, row.days, row.request_id, row.created_by_id), (2026, Decimal("-3"), req.pk, self.hrd.pk))
        self.assertEqual(leave.balance(self.e1, 2026), Decimal("9")); self.assertEqual(leave.reserved(self.e1, 2026), Decimal("0"))
        self.assertContains(self.client.get(f"/requests/{req.pk}/"), "Pemotongan saldo cuti")

    def test_execute_refused_when_balance_dropped_after_approval(self):
        self.grant(days=3); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved")
        LeaveLedger.objects.create(employee=self.e1, year=2026, kind="adjust", days=-2, note="koreksi")  # saldo turun setelah disetujui
        with self.assertRaisesMessage(ValueError, "Saldo cuti 2026 tidak cukup"): services.transition(req, "executed", self.hrd)
        req.refresh_from_db(); self.assertEqual(req.status, "approved"); self.assertFalse(LeaveLedger.objects.filter(kind="use").exists())

    def test_execute_via_ui_shows_error_and_keeps_status(self):
        self.grant(days=1); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved"); self.login("hrd")
        r = self.client.post(f"/requests/{req.pk}/executed/", {}, follow=True)
        self.assertTrue(any("Saldo cuti" in m for m in self.msgs(r))); req.refresh_from_db(); self.assertEqual(req.status, "approved")

    def test_charge_is_idempotent(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="executed")
        leave.charge(req, self.hrd); leave.charge(req, self.hrd)
        self.assertEqual(LeaveLedger.objects.filter(kind="use").count(), 1); self.assertEqual(leave.balance(self.e1, 2026), Decimal("9"))

    def test_db_blocks_duplicate_use_row(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="executed")
        LeaveLedger.objects.create(employee=self.e1, year=2026, kind="use", days=-3, request=req)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeaveLedger.objects.create(employee=self.e1, year=2026, kind="use", days=-3, request=req)

    def test_cross_year_execute_splits_per_year(self):
        self.grant(year=2026, days=5); self.grant(year=2027, days=5); req = self.cuti_req("2026-12-30", "2027-01-04", status="approved")
        services.transition(req, "executed", self.hrd)
        self.assertEqual((leave.balance(self.e1, 2026), leave.balance(self.e1, 2027)), (Decimal("3"), Decimal("2")))

    def test_cross_year_needs_balance_in_each_year(self):
        self.grant(year=2026, days=5); self.login("adm1"); self.assertContains(self.post_cuti("2026-12-30", "2027-01-04"), "Saldo cuti 2027")

    def test_other_leave_types_do_not_deduct(self):
        for t in ("izin", "sakit", "izin_khusus"):
            req = self.make_req(t, {"start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "x"}, status="approved"); services.transition(req, "executed", self.hrd)
        self.assertFalse(LeaveLedger.objects.exists())

    def test_execute_refused_for_inactive_employee(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved"); Employee.objects.filter(pk=self.e1.pk).update(status="nonaktif")
        with self.assertRaisesMessage(ValueError, "tidak aktif"): services.transition(req, "executed", self.hrd)


class LeaveLedgerIntegrityTests(Base):
    def test_append_only(self):
        row = self.grant()
        row.days = 99
        with self.assertRaises(PermissionError): row.save()
        with self.assertRaises(PermissionError): row.delete()

    def test_use_must_be_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic(): LeaveLedger.objects.create(employee=self.e1, year=2026, kind="use", days=1)

    def test_one_grant_per_employee_year(self):
        self.grant()
        with self.assertRaises(IntegrityError), transaction.atomic(): self.grant()
        self.grant(year=2027); self.grant(emp=self.e2)  # tahun/karyawan lain boleh


# ------------------------------------------------------------------ halaman HRD saldo cuti
class LeavePagesTests(Base):
    def entry(self, **kw):
        d = {"kind": "grant", "year": TODAY.year, "days": "12", "note": ""}; d.update(kw)
        return self.client.post(f"/leave/{self.e1.pk}/", d)

    def test_rbac(self):
        for who, code in (("adm1", 403), ("poli", 403), ("hrd", 200), ("su", 200)):
            self.login(who); self.assertEqual(self.client.get("/leave/").status_code, code, who); self.assertEqual(self.client.get(f"/leave/{self.e1.pk}/").status_code, code, who)
        self.login("adm1"); self.assertEqual(self.client.post(f"/leave/{self.e1.pk}/", {"kind": "grant", "year": TODAY.year, "days": "12"}).status_code, 403)
        self.assertFalse(LeaveLedger.objects.exists()); self.client.logout(); self.assertIn(self.client.get("/leave/").status_code, (302, 401))

    def test_grant_then_duplicate_rejected_and_audited(self):
        self.login("hrd"); self.assertEqual(self.entry().status_code, 302)
        row = LeaveLedger.objects.get(); self.assertEqual((row.kind, row.days, row.created_by_id), ("grant", Decimal("12.0"), self.hrd.pk))
        self.assertTrue(AuditLog.objects.filter(action="leave_grant", object_id=str(self.e1.pk)).exists())
        self.assertContains(self.entry(), "sudah diberikan"); self.assertEqual(LeaveLedger.objects.count(), 1)

    def test_adjust_requires_note_and_cannot_go_negative(self):
        self.login("hrd"); self.entry()
        self.assertContains(self.entry(kind="adjust", days="-1"), "Wajib diisi untuk koreksi")
        self.assertContains(self.entry(kind="adjust", days="-13", note="salah"), "tidak boleh menjadi negatif")
        self.assertEqual(self.entry(kind="adjust", days="-2", note="cuti bersama").status_code, 302)
        self.assertEqual(leave.balance(self.e1, TODAY.year), Decimal("10"))
        self.assertEqual(self.entry(kind="adjust", days="0.5", note="tambah").status_code, 302); self.assertEqual(leave.balance(self.e1, TODAY.year), Decimal("10.5"))

    def test_rejects_bad_values(self):
        self.login("hrd")
        self.assertContains(self.entry(days="0"), "Tidak boleh 0"); self.assertContains(self.entry(days="1.3"), "kelipatan 0,5")
        self.assertContains(self.entry(days="-3"), "Jatah harus positif"); self.assertContains(self.entry(year=TODAY.year + 5), "Tahun harus")
        self.assertFalse(LeaveLedger.objects.exists())

    def test_list_filters_and_pagination_params_safe(self):
        self.grant(year=TODAY.year, days=2); self.login("hrd")
        r = self.client.get(f"/leave/?year={TODAY.year}&filter=nogrant"); self.assertEqual([e.nik for e in r.context["page"]], ["002"])
        r = self.client.get(f"/leave/?year={TODAY.year}&filter=low"); self.assertEqual([e.nik for e in r.context["page"]], ["001"])
        self.assertEqual(self.client.get("/leave/?year=abc&department=xyz&page=zzz").status_code, 200)  # input aneh tidak 500
        self.assertEqual(self.client.get("/leave/?q=%00").status_code, 400)  # NUL ditolak terpusat

    def test_employee_detail_shows_balance_to_dept_admin_in_scope(self):
        self.grant(year=TODAY.year, days=7); self.login("adm1")
        r = self.client.get(f"/employees/{self.e1.pk}/"); self.assertContains(r, "Saldo cuti"); self.assertContains(r, "7,0 hari")
        self.assertNotContains(r, "Kartu saldo")  # tombol kartu hanya HRD
        self.login("hrd"); self.assertContains(self.client.get(f"/employees/{self.e1.pk}/"), "Kartu saldo")

    def test_no_nav_link_for_non_hrd(self):
        self.login("adm1"); self.assertNotContains(self.client.get("/employees/"), "/leave/")
        self.login("hrd"); self.assertContains(self.client.get("/employees/"), "/leave/")


class GrantCommandTests(Base):
    def run_cmd(self, *a):
        out = StringIO(); call_command("grant_annual_leave", *a, stdout=out); return out.getvalue()

    def test_grants_only_eligible_and_is_idempotent(self):
        newbie = Employee.objects.create(nik="003", name="Baru", gender="L", department=self.d1, position=self.pos, join_date=date(2026, 6, 1))
        off = Employee.objects.create(nik="004", name="Off", gender="L", department=self.d1, position=self.pos, join_date=date(2019, 1, 1), status="nonaktif")
        out = self.run_cmd("--year", "2026")
        self.assertIn("diberi jatah=2", out); self.assertIn("belum berhak=1", out)
        self.assertEqual({r.employee_id for r in LeaveLedger.objects.all()}, {self.e1.pk, self.e2.pk})
        self.assertFalse(LeaveLedger.objects.filter(employee__in=[newbie, off]).exists())
        self.assertIn("diberi jatah=0", self.run_cmd("--year", "2026")); self.assertEqual(LeaveLedger.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="leave_grant_batch").count(), 1)  # eksekusi kedua tanpa perubahan tidak mencatat audit

    def test_as_of_changes_eligibility(self):
        Employee.objects.create(nik="003", name="Baru", gender="L", department=self.d1, position=self.pos, join_date=date(2026, 6, 1))
        self.assertIn("belum berhak=1", self.run_cmd("--year", "2027", "--dry-run"))  # dry-run: tidak menulis apa pun
        self.assertFalse(LeaveLedger.objects.exists())
        self.assertIn("diberi jatah=3", self.run_cmd("--year", "2027", "--as-of", "2027-07-01"))

    def test_dry_run_writes_nothing(self):
        out = self.run_cmd("--year", "2026", "--dry-run"); self.assertIn("[SIMULASI]", out); self.assertFalse(LeaveLedger.objects.exists()); self.assertFalse(AuditLog.objects.filter(action="leave_grant_batch").exists())


# ------------------------------------------------------------------ pembatalan
class CancelTests(Base):
    def test_requester_cancels_own_draft_without_reason(self):
        req = self.cuti_req("2026-11-02", "2026-11-03", status="draft"); self.login("adm1")
        self.act(req, "cancelled"); req.refresh_from_db(); self.assertEqual(req.status, "cancelled")
        self.assertTrue(AuditLog.objects.filter(action="request_cancelled", object_id=str(req.pk)).exists())

    def test_pending_cancel_needs_reason(self):
        req = self.cuti_req("2026-11-02", "2026-11-03"); self.login("adm1")
        self.act(req, "cancelled"); req.refresh_from_db(); self.assertEqual(req.status, "pending")
        self.act(req, "cancelled", "salah input"); req.refresh_from_db(); self.assertEqual(req.status, "cancelled"); self.assertIn("salah input", req.note)

    def test_other_dept_admin_same_department_cannot_cancel(self):
        req = self.cuti_req("2026-11-02", "2026-11-03"); self.login("adm1b"); self.act(req, "cancelled", "iseng"); req.refresh_from_db(); self.assertEqual(req.status, "pending")

    def test_other_department_gets_404(self):
        req = self.cuti_req("2026-11-02", "2026-11-03"); self.login("adm2"); self.assertEqual(self.act(req, "cancelled", "x").status_code, 404)

    def test_dept_admin_cannot_cancel_approved_but_hrd_can(self):
        req = self.cuti_req("2026-11-02", "2026-11-03", status="approved"); self.login("adm1")
        self.act(req, "cancelled", "x"); req.refresh_from_db(); self.assertEqual(req.status, "approved")
        self.assertNotContains(self.client.get(f"/requests/{req.pk}/"), "Batalkan")
        self.login("hrd"); self.assertContains(self.client.get(f"/requests/{req.pk}/"), "Batalkan")
        self.act(req, "cancelled", "tanggal sudah lewat"); req.refresh_from_db(); self.assertEqual(req.status, "cancelled")
        self.assertTrue(Notification.objects.filter(user=self.adm1, title__contains="dibatalkan HRD").exists())

    def test_cancelled_is_final(self):
        req = self.cuti_req("2026-11-02", "2026-11-03", status="cancelled"); self.login("hrd")
        for a in ("approved", "executed", "submit", "cancelled"): self.act(req, a, "x")
        req.refresh_from_db(); self.assertEqual(req.status, "cancelled"); self.assertFalse(LeaveLedger.objects.exists())

    def test_cannot_cancel_executed(self):
        req = self.cuti_req("2026-11-02", "2026-11-03", status="executed"); self.login("hrd"); self.act(req, "cancelled", "x"); req.refresh_from_db(); self.assertEqual(req.status, "executed")

    def test_cancel_releases_reserved_balance_and_overlap(self):
        self.grant(days=3); req = self.cuti_req("2026-11-02", "2026-11-04"); self.assertEqual(leave.available(self.e1, 2026), Decimal("0"))
        self.login("adm1"); self.act(req, "cancelled", "batal")
        self.assertEqual(leave.available(self.e1, 2026), Decimal("3"))
        r = self.client.post("/requests/new/", {"type": "cuti", "nik": "001", "start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "x", "submit": "1"})
        self.assertEqual(r.status_code, 302)  # tidak lagi dianggap tumpang tindih

    def test_api_transition_cancel(self):
        req = self.cuti_req("2026-11-02", "2026-11-03"); self.login("adm1")
        self.assertEqual(self.client.post(f"/api/requests/{req.pk}/cancelled/").status_code, 400)  # tanpa alasan → ditolak
        req.refresh_from_db(); self.assertEqual(req.status, "pending")

    def test_status_label_and_filter(self):
        self.cuti_req("2026-11-02", "2026-11-03", status="cancelled"); self.login("hrd")
        r = self.client.get("/requests/?status=cancelled"); self.assertContains(r, "Dibatalkan"); self.assertEqual(len(r.context["page"]), 1)


# ------------------------------------------------------------------ konkurensi (bermakna di PostgreSQL)
class ConcurrencyTests(TransactionTestCase):
    """Pelaksanaan bersamaan oleh dua HRD: saldo tidak boleh negatif, jadwal tidak boleh ganda, tidak ada 500 (hasil ditolak rapi)."""
    def setUp(self):
        d = Department.objects.create(code="Z", name="Z"); pos = Position.objects.create(name="S")
        self.shift = Shift.objects.create(name="Pagi", start=time(7), end=time(15)); self.other = Shift.objects.create(name="Malam", start=time(23), end=time(7))
        self.e = Employee.objects.create(nik="C1", name="Konkuren", gender="L", department=d, position=pos, shift=self.shift, join_date=date(2020, 1, 1))
        self.hrd = User.objects.create_user("h1", password=PW, role=Role.HRD); self.hrd2 = User.objects.create_user("h2", password=PW, role=Role.HRD)
        self.adm = User.objects.create_user("a1", password=PW, role=Role.DEPT_ADMIN, department=d)

    def req(self, type_, payload): return ChangeRequest.objects.create(type=type_, employee=self.e, department=self.e.department, status="approved", payload=payload, requested_by=self.adm)

    def race(self, jobs):
        barrier, results = threading.Barrier(len(jobs)), []
        def worker(req, user):
            try:
                barrier.wait(timeout=5); services.transition(req, "executed", user); results.append("ok")
            except ValueError: results.append("ditolak")
            except Exception as ex: results.append(f"ERROR {type(ex).__name__}: {ex}")
            finally: connection.close()
        ts = [threading.Thread(target=worker, args=j) for j in jobs]
        [t.start() for t in ts]; [t.join(15) for t in ts]
        return sorted(results)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_leave_requests_competing_for_same_balance(self):
        LeaveLedger.objects.create(employee=self.e, year=2026, kind="grant", days=3)
        a = self.req("cuti", {"start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "x"})  # masing-masing 3 hari, saldo hanya 3
        b = self.req("cuti", {"start_date": "2026-11-10", "end_date": "2026-11-12", "reason": "x"})
        self.assertEqual(self.race([(a, self.hrd), (b, self.hrd2)]), ["ditolak", "ok"])
        self.assertEqual(leave.balance(self.e, 2026), Decimal("0"))  # tidak pernah negatif
        self.assertEqual(LeaveLedger.objects.filter(kind="use").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_same_leave_request_executed_twice_deducts_once(self):
        LeaveLedger.objects.create(employee=self.e, year=2026, kind="grant", days=12)
        a = self.req("cuti", {"start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "x"})
        self.assertEqual(self.race([(a, self.hrd), (a, self.hrd2)]), ["ditolak", "ok"])
        self.assertEqual(leave.balance(self.e, 2026), Decimal("9")); self.assertEqual(LeaveLedger.objects.filter(kind="use").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_swaps_same_date_only_one_applied(self):
        d = next_weekday(1).isoformat()  # hari kerja (bukan Minggu): tukar shift pada hari libur ditolak sejak putaran 17b
        a = self.req("tukar_shift", {"date": d, "shift_id": self.other.pk, "reason": "x"}); b = self.req("tukar_shift", {"date": d, "shift_id": self.other.pk, "reason": "y"})
        self.assertEqual(self.race([(a, self.hrd), (b, self.hrd2)]), ["ditolak", "ok"])
        self.assertEqual(ShiftAssignment.objects.filter(employee=self.e).count(), 1)
