"""Batalkan pelaksanaan (izin/cuti/sakit/tukar jadwal) oleh HRD + hitung ulang absensi otomatis saat dilaksanakan/dibatalkan."""
from datetime import timedelta
from decimal import Decimal
from . import leave, schedule
from .models import AttendanceDaily, LeaveLedger, ShiftAssignment
from .test_schedule_leave import TODAY, Base, next_weekday


def past_workday():
    d = TODAY - timedelta(days=3)
    while d.weekday() == 6: d -= timedelta(days=1)
    return d


class UndoTests(Base):
    def izin(self, d, status="approved"): return self.make_req("izin", {"start_date": d.isoformat(), "end_date": d.isoformat(), "reason": "urusan keluarga"}, status=status)

    def test_cuti_cancel_refunds_balance_and_notifies(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved"); self.login("hrd"); self.act(req, "executed")
        self.assertEqual(leave.balance(self.e1, 2026), Decimal("9"))
        self.act(req, "cancelled", "salah input"); req.refresh_from_db()
        self.assertEqual(req.status, "cancelled"); self.assertEqual(leave.balance(self.e1, 2026), Decimal("12"))
        self.assertTrue(LeaveLedger.objects.filter(request=req, kind="adjust", days=3).exists())
        self.assertTrue(self.adm1.notifications.filter(title__contains="dibatalkan HRD").exists())

    def test_requires_reason_and_only_hrd(self):
        self.grant(); req = self.cuti_req("2026-11-02", "2026-11-04", status="approved"); self.login("hrd"); self.act(req, "executed")
        self.act(req, "cancelled", ""); req.refresh_from_db(); self.assertEqual(req.status, "executed")        # alasan wajib
        self.login("adm1"); self.act(req, "cancelled", "mau batal"); req.refresh_from_db(); self.assertEqual(req.status, "executed")  # admin dept tidak boleh
        self.assertEqual(leave.balance(self.e1, 2026), Decimal("9"))

    def test_swap_cancel_voids_rows_restores_schedule_and_allows_new_swap(self):
        d = next_weekday(2); req = self.swap_shift_req(d, status="approved"); self.login("hrd"); self.act(req, "executed")
        self.assertEqual(ShiftAssignment.objects.filter(employee=self.e1).count(), 1)
        self.act(req, "cancelled", "batal tukar"); req.refresh_from_db(); self.assertEqual(req.status, "cancelled")
        self.assertEqual(ShiftAssignment.objects.count(), 0)                                                   # tidak berlaku lagi
        self.assertEqual(ShiftAssignment.all_objects.filter(voided_at__isnull=False).count(), 1)               # jejak tetap ada
        self.assertNotEqual(schedule.effective_schedule(self.e1, d).get("source"), "override")
        again = self.swap_shift_req(d, status="approved"); self.act(again, "executed"); again.refresh_from_db(); self.assertEqual(again.status, "executed")

    def test_swap_on_past_date_cannot_be_undone(self):
        d = TODAY - timedelta(days=2); req = self.swap_shift_req(d, status="executed")
        ShiftAssignment.all_objects.create(employee=self.e1, date=d, kind="shift", shift=self.s_malam, request=req)
        self.login("hrd"); r = self.act(req, "cancelled", "coba"); req.refresh_from_db()
        self.assertEqual(req.status, "executed"); self.assertEqual(ShiftAssignment.objects.count(), 1)

    def test_execute_izin_updates_attendance_then_cancel_reverts(self):
        d = past_workday(); req = self.izin(d); self.login("hrd")
        self.act(req, "executed"); self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "izin")   # otomatis, tanpa menunggu cron
        self.act(req, "cancelled", "izin tidak jadi"); req.refresh_from_db(); self.assertEqual(req.status, "cancelled")
        self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "tanpa_scan")                         # kembali ke belum jelas → bisa divalidasi

    def test_action_button_offered_for_executed_izin(self):
        req = self.izin(past_workday(), status="executed"); self.login("hrd")
        self.assertContains(self.client.get(f"/requests/{req.pk}/"), "Batalkan pelaksanaan")
        self.login("adm1"); self.assertNotContains(self.client.get(f"/requests/{req.pk}/"), "Batalkan pelaksanaan")
