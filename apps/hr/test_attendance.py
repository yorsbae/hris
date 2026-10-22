"""Putaran 48: Tahap 6 Absensi — impor Fingerspot (berbagai format), pemetaan PIN, pengolahan rekap harian, integrasi pengajuan/validasi/libur/lembur."""
import io
from datetime import date, datetime, time, timedelta
from django.test import override_settings
from django.utils import timezone
from apps.core.models import Role, User
from apps.hr import attendance_calc as calc, attendance_import as imp
from apps.hr.models import (AttendanceCheck, AttendanceCheckEvent, AttendanceDaily, AttendanceImport, AttendancePin, AttendanceRaw, ChangeRequest, Holiday, Shift, ShiftGroup,
                            ShiftRotation)
from apps.hrd.test_base import HrdBase


def workday(back=3):
    """Hari kerja (bukan Minggu) `back` hari kerja ke belakang dari hari ini."""
    d, n = date.today(), 0
    while n < back:
        d -= timedelta(days=1)
        if d.weekday() != 6: n += 1
    return d


def at(d, hh, mm=0, ss=0): return timezone.make_aware(datetime.combine(d, time(hh, mm, ss)))


def csv_bytes(*lines): return ("\n".join(lines) + "\n").encode("utf-8")


def xlsx_bytes(rows):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    for r in rows: ws.append(r)
    out = io.BytesIO(); wb.save(out); return out.getvalue()


class Base(HrdBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pagi = Shift.objects.create(name="Pagi", code="PAGI", start=time(7, 0), end=time(15, 0))
        cls.malam = Shift.objects.create(name="Malam", code="MALAM", start=time(22, 0), end=time(6, 0), crosses_midnight=True)
        cls.e1.shift = cls.pagi; cls.e1.save()
        cls.e3.shift = cls.pagi; cls.e3.save()

    def scan(self, emp, d, *hm, pin=None):
        for h, m in hm: AttendanceRaw.objects.create(employee=emp, pin=pin or emp.nik, device_id="FP1", punched_at=at(d, h, m))

    def run_calc(self, d1, d2=None, ids=None): return calc.process(d1, d2 or d1, ids)

    def row(self, emp, d): return AttendanceDaily.objects.get(employee=emp, date=d)


# ================================================================= pengolahan
class DayStatusTests(Base):
    def test_present_with_late_and_early(self):
        d = workday(); self.scan(self.e1, d, (7, 12), (14, 40)); self.run_calc(d)
        r = self.row(self.e1, d)
        self.assertEqual((r.status, r.source, r.late_minutes, r.early_minutes, r.scan_count, r.incomplete), ("hadir", "mesin", 12, 20, 2, False))
        self.assertEqual(timezone.localtime(r.first_in).strftime("%H:%M"), "07:12"); self.assertEqual(timezone.localtime(r.last_out).strftime("%H:%M"), "14:40")

    def test_on_time_has_zero_late(self):
        d = workday(); self.scan(self.e1, d, (6, 55), (15, 5)); self.run_calc(d)
        r = self.row(self.e1, d); self.assertEqual((r.late_minutes, r.early_minutes, r.status), (0, 0, "hadir"))

    @override_settings(ATTENDANCE_LATE_GRACE_MIN=10)
    def test_grace_minutes_are_configurable(self):
        d = workday(); self.scan(self.e1, d, (7, 8), (15, 0)); self.run_calc(d)
        self.assertEqual(self.row(self.e1, d).late_minutes, 0)
        d2 = workday(4); self.scan(self.e1, d2, (7, 25), (15, 0)); self.run_calc(d2); self.assertEqual(self.row(self.e1, d2).late_minutes, 15)

    def test_workday_without_scan_is_tanpa_scan_never_alfa(self):
        d = workday(); self.run_calc(d); r = self.row(self.e1, d)
        self.assertEqual((r.status, r.scan_count), ("tanpa_scan", 0)); self.assertTrue(r.needs_review)

    def test_sunday_is_libur_and_work_on_sunday_is_hadir_with_note(self):
        sun = workday(2)
        while sun.weekday() != 6: sun -= timedelta(days=1)
        self.run_calc(sun); self.assertEqual(self.row(self.e1, sun).status, "libur")
        self.scan(self.e3, sun, (8, 0), (12, 0)); self.run_calc(sun); r = self.row(self.e3, sun)
        self.assertEqual(r.status, "hadir"); self.assertIn("hari libur", r.notes.lower())

    def test_single_scan_morning_means_missing_out(self):
        d = workday(); self.scan(self.e1, d, (7, 5)); self.run_calc(d); r = self.row(self.e1, d)
        self.assertTrue(r.incomplete); self.assertIsNotNone(r.first_in); self.assertIsNone(r.last_out); self.assertEqual(r.late_minutes, 5); self.assertEqual(r.early_minutes, 0)

    def test_single_scan_afternoon_means_missing_in(self):
        d = workday(); self.scan(self.e1, d, (15, 2)); self.run_calc(d); r = self.row(self.e1, d)
        self.assertTrue(r.incomplete); self.assertIsNone(r.first_in); self.assertIsNotNone(r.last_out); self.assertEqual(r.late_minutes, 0)

    def test_double_tap_counts_as_single_event(self):
        d = workday(); self.scan(self.e1, d, (7, 0), (7, 1)); self.run_calc(d); self.assertTrue(self.row(self.e1, d).incomplete)

    def test_night_shift_belongs_to_the_day_it_started(self):
        g = ShiftGroup.objects.create(code="A_pack", pattern="3_SHIFT")
        for wd in range(7): ShiftRotation.objects.create(group=g, weekday=wd, shift=self.malam)
        self.e1.shift_group = g; self.e1.save()
        d = workday(3); self.scan(self.e1, d, (21, 50)); AttendanceRaw.objects.create(employee=self.e1, pin="001", device_id="FP1", punched_at=at(d + timedelta(days=1), 6, 3))
        self.run_calc(d, d + timedelta(days=1)); r = self.row(self.e1, d)
        self.assertEqual((r.status, r.scan_count, r.incomplete, r.late_minutes, r.early_minutes), ("hadir", 2, False, 0, 0))
        nxt = self.row(self.e1, d + timedelta(days=1)); self.assertEqual(nxt.scan_count, 0)   # scan 06:03 tidak dihitung ke hari berikutnya

    def test_today_and_future_are_not_processed(self):
        r = calc.process(date.today(), date.today() + timedelta(days=3)); self.assertEqual(r["rows"], 0); self.assertEqual(AttendanceDaily.objects.count(), 0)

    def test_process_is_idempotent_and_updates(self):
        d = workday(); self.run_calc(d); n = AttendanceDaily.objects.count(); self.run_calc(d); self.assertEqual(AttendanceDaily.objects.count(), n)
        self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")
        self.scan(self.e1, d, (7, 0), (15, 0)); self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "hadir")

    def test_inactive_and_not_yet_joined_employees_skipped(self):
        d = workday(); self.run_calc(d)
        self.assertFalse(AttendanceDaily.objects.filter(employee=self.e_off).exists())
        self.e3.join_date = date.today(); self.e3.save(); AttendanceDaily.objects.all().delete(); self.run_calc(d); self.assertFalse(AttendanceDaily.objects.filter(employee=self.e3).exists())

    def test_separation_last_day_stops_rows(self):
        from apps.hrd.models import Separation
        d1, d2 = workday(4), workday(2)
        Separation.objects.create(employee=self.e1, kind="resign", last_date=d1, emp_name="Budi", department_name="Produksi", join_date=self.e1.join_date)
        self.e1.status = "nonaktif"; self.e1.save(); self.run_calc(d1 - timedelta(days=1), d2)
        got = set(AttendanceDaily.objects.filter(employee=self.e1).values_list("date", flat=True)); self.assertIn(d1, got); self.assertNotIn(d2, got)


class IntegrationTests(Base):
    def req(self, type_, d1, d2=None, **p):
        return ChangeRequest.objects.create(type=type_, employee=self.e1, department=self.d1, status="executed", requested_by=self.adm,
                                            payload={"start_date": d1.isoformat(), "end_date": (d2 or d1).isoformat(), **p})

    def test_executed_leave_sets_status_and_flags_scan_conflict(self):
        d = workday(); r = self.req("cuti", d); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual((x.status, x.source, x.conflict), ("cuti", "pengajuan", ""))
        self.scan(self.e1, d, (8, 0), (12, 0)); self.run_calc(d); x = self.row(self.e1, d); self.assertEqual(x.status, "cuti"); self.assertIn(f"#{r.pk}", x.conflict)

    def test_sick_and_permission_map_to_status(self):
        d, d2 = workday(3), workday(2); self.req("sakit", d); self.req("izin_khusus", d2); self.run_calc(d, d2)
        self.assertEqual(self.row(self.e1, d).status, "sakit"); self.assertEqual(self.row(self.e1, d2).status, "izin")

    def test_pending_or_rejected_requests_do_not_count(self):
        d = workday(); r = self.req("cuti", d); r.status = "pending"; r.save(); self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")

    def test_late_permission_excuses_late_minutes(self):
        d = workday(); self.scan(self.e1, d, (7, 40), (15, 0))
        ChangeRequest.objects.create(type="izin_terlambat", employee=self.e1, department=self.d1, status="executed", requested_by=self.adm, payload={"date": d.isoformat(), "time": "08:00"})
        self.run_calc(d); x = self.row(self.e1, d); self.assertEqual(x.late_minutes, 0); self.assertIn("Izin terlambat", x.notes)

    def test_maternity_leave(self):
        from apps.hrd.models import MaternityLeave
        d = workday(); MaternityLeave.objects.create(employee=self.e3, due_date=d + timedelta(days=30), start_date=d - timedelta(days=5), end_date=d + timedelta(days=5)); self.run_calc(d)
        self.assertEqual(self.row(self.e3, d).status, "cuti_hamil")

    def test_verified_check_overrides_machine_and_correction_wins(self):
        d = workday(); self.scan(self.e1, d, (7, 0), (15, 0))
        a = AttendanceCheck.objects.create(employee=self.e1, department=self.d1, date=d, status="diverifikasi", due_date=d, answer="alfa", final_answer="alfa", requested_by=self.hrd)
        self.run_calc(d); x = self.row(self.e1, d); self.assertEqual((x.status, x.source), ("alfa", "validasi"))
        AttendanceCheckEvent.objects.create(att=a, user=self.hrd, action="koreksi", value="sakit", note="Surat dokter menyusul"); self.run_calc(d)
        self.assertEqual(self.row(self.e1, d).status, "sakit")

    def test_unverified_check_does_not_override(self):
        d = workday(); AttendanceCheck.objects.create(employee=self.e1, department=self.d1, date=d, status="dijawab", due_date=d, answer="alfa", requested_by=self.hrd)
        self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")

    def test_verify_service_recomputes_the_day(self):
        from apps.hr import attendance_check as svc
        d = workday(); self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")
        svc.create_checks(self.hrd, [self.e1], [d], "Mohon konfirmasi"); a = AttendanceCheck.objects.get(employee=self.e1, date=d)
        self.assertEqual(a.hint, "Mesin: tidak ada scan")
        svc.answer(self.adm, a.pk, "alfa", "Tanpa kabar"); svc.verify(self.hrd, a.pk, "terima")
        x = self.row(self.e1, d); self.assertEqual((x.status, x.source), ("alfa", "validasi"))
        svc.correct(self.hrd, a.pk, "izin", "Ternyata izin lisan"); self.assertEqual(self.row(self.e1, d).status, "izin")

    def test_holiday_non_rotation_vs_rotation(self):
        d = workday(); Holiday.objects.create(date=d, name="Hari Raya", applies="non_rotation")
        g = ShiftGroup.objects.create(code="B", pattern="2_SHIFT")
        for wd in range(7): ShiftRotation.objects.create(group=g, weekday=wd, shift=self.pagi)
        self.e3.shift_group = g; self.e3.save(); self.run_calc(d)
        self.assertEqual(self.row(self.e1, d).status, "libur_nasional"); self.assertEqual(self.row(self.e3, d).status, "tanpa_scan")   # rotasi tetap jalan
        Holiday.objects.filter(date=d).update(applies="all"); self.run_calc(d); self.assertEqual(self.row(self.e3, d).status, "libur_nasional")
        self.scan(self.e1, d, (8, 0), (12, 0)); self.run_calc(d); x = self.row(self.e1, d); self.assertEqual(x.status, "hadir"); self.assertIn("Hari Raya", x.notes)

    def test_holiday_where_company_still_works(self):
        """Libur nasional tapi perusahaan tetap berangkat: wajib masuk = hari kerja biasa; yang masuk dihitung Hari Libur (bisa dimatikan)."""
        d = workday(); h = Holiday.objects.create(date=d, name="Hari Raya", applies="all", duty="dept"); h.duty_departments.add(self.e1.department)
        self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")            # departemen wajib masuk: tanpa scan ≠ libur
        self.assertEqual(self.row(self.e3, d).status, "libur_nasional") if self.e3.department_id != self.e1.department_id else None
        self.scan(self.e1, d, (7, 0), (15, 0)); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual(x.status, "hadir"); self.assertTrue(x.notes.startswith("Masuk di hari libur"))   # Payroll membaca awalan ini
        Holiday.objects.filter(pk=h.pk).update(duty="all", duty_premium=False); self.run_calc(d); self.assertEqual(self.row(self.e1, d).notes, "")   # hari kerja biasa: tanpa premi

    def test_inactive_holiday_ignored(self):
        d = workday(); Holiday.objects.create(date=d, name="X", active=False); self.run_calc(d); self.assertEqual(self.row(self.e1, d).status, "tanpa_scan")


class OvertimeTests(Base):
    def ot(self, d, t0, mins, kind="lembur", status="executed"):
        return ChangeRequest.objects.create(type=kind, employee=self.e1, department=self.d1, status=status, requested_by=self.adm,
                                            payload={"date": d.isoformat(), "time": t0, "time_to": "-", "minutes": mins, "reason": "x", "batch": "b"})

    def test_overtime_proved_by_scan(self):
        d = workday(); self.ot(d, "15:00", 120); self.scan(self.e1, d, (7, 0), (17, 5)); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual((x.overtime_approved_minutes, x.overtime_minutes, x.conflict), (120, 120, ""))

    def test_overtime_within_tolerance_counts_in_full(self):
        d = workday(); self.ot(d, "15:00", 120); self.scan(self.e1, d, (7, 0), (16, 50)); self.run_calc(d); self.assertEqual(self.row(self.e1, d).overtime_minutes, 120)

    def test_overtime_not_proved_counts_only_overlap_and_flags(self):
        d = workday(); self.ot(d, "15:00", 120); self.scan(self.e1, d, (7, 0), (15, 30)); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual((x.overtime_approved_minutes, x.overtime_minutes), (120, 30)); self.assertIn("terbukti scan 30", x.conflict)

    def test_overtime_without_scan_counts_zero(self):
        d = workday(); self.ot(d, "15:00", 60); self.run_calc(d); x = self.row(self.e1, d); self.assertEqual((x.overtime_approved_minutes, x.overtime_minutes), (60, 0)); self.assertTrue(x.conflict)

    def test_pending_overtime_not_counted(self):
        d = workday(); self.ot(d, "15:00", 60, status="pending"); self.scan(self.e1, d, (7, 0), (16, 5)); self.run_calc(d); self.assertEqual(self.row(self.e1, d).overtime_approved_minutes, 0)

    def test_machine_extra_without_request_is_noted_not_paid(self):
        d = workday(); self.scan(self.e1, d, (7, 0), (17, 0)); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual((x.overtime_minutes, x.extra_minutes), (0, 120)); self.assertIn("tanpa pengajuan lembur", x.notes)

    def test_standby_copied_and_flagged_when_no_scan(self):
        d = workday(); self.ot(d, "12:00", 60, kind="standby"); self.run_calc(d); x = self.row(self.e1, d)
        self.assertEqual(x.standby_minutes, 60); self.assertIn("Stand by disetujui", x.conflict)

    def test_summary_aggregates(self):
        d1, d2 = workday(2), workday(1); self.scan(self.e1, d1, (7, 30), (15, 0)); self.scan(self.e1, d2, (7, 0), (14, 0)); self.run_calc(d1, d2)
        s = {r["employee__nik"]: r for r in calc.summary(AttendanceDaily.objects.filter(date__range=(d1, d2)))}["001"]
        self.assertEqual((s["hadir"], s["terlambat"], s["terlambat_menit"], s["pulang_cepat"], s["pulang_cepat_menit"], s["alfa"]), (2, 1, 30, 1, 60, 0))


# ================================================================= impor
LOG_HDR = "PIN,Nama,Tanggal Scan,Verifikasi,Mesin"


class ParserTests(Base):
    def taps(self, raw, name):
        grid = imp.read_grid(raw, name); m = imp.detect(grid); return imp.extract(grid, m), m

    def test_csv_log_with_datetime_column(self):
        ex, m = self.taps(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,Finger,M1", "001,Budi,2026-10-01 15:03:00,Finger,M1"), "scan.csv")
        self.assertEqual((ex["invalid"], len(ex["taps"])), (0, 2)); self.assertEqual(imp.layout_of(m), "log")
        self.assertEqual(ex["taps"][0], ("001", "Budi", datetime(2026, 10, 1, 7, 2, 11), "M1"))

    def test_indonesian_date_format_and_semicolon(self):
        ex, _ = self.taps(csv_bytes("PIN;Nama;Tanggal Scan", "1001;Budi;01/10/2026 07:02:11", "1001;Budi;02-10-2026 07:00"), "x.csv")
        self.assertEqual([t[2] for t in ex["taps"]], [datetime(2026, 10, 1, 7, 2, 11), datetime(2026, 10, 2, 7, 0)])

    def test_separate_date_and_time_columns(self):
        ex, m = self.taps(csv_bytes("NIP,Nama,Tanggal,Jam", "001,Budi,01/10/2026,07:02", "001,Budi,01/10/2026,15:01"), "x.csv")
        self.assertEqual([t[2] for t in ex["taps"]], [datetime(2026, 10, 1, 7, 2), datetime(2026, 10, 1, 15, 1)])

    def test_xlsx_with_real_datetime_cells_and_title_rows(self):
        raw = xlsx_bytes([["LAPORAN SCAN LOG"], ["Periode Oktober 2026"], [], ["No", "PIN", "Nama", "Tanggal Scan", "Mesin"],
                          [1, 1001, "Budi", datetime(2026, 10, 1, 7, 2, 11), "M1"], [2, 1001, "Budi", datetime(2026, 10, 1, 15, 0, 0), "M1"]])
        ex, m = self.taps(raw, "scan.xlsx"); self.assertEqual((len(ex["taps"]), ex["invalid"]), (2, 0)); self.assertEqual(ex["taps"][0][0], "1001"); self.assertEqual(m["header_row"], 2)  # baris kosong dibuang saat membaca XLSX

    def test_xlsx_date_cell_plus_time_cell(self):
        raw = xlsx_bytes([["PIN", "Tanggal", "Jam"], ["001", datetime(2026, 10, 1), time(7, 2)], ["001", datetime(2026, 10, 1), time(15, 1)]])
        ex, _ = self.taps(raw, "x.xlsx"); self.assertEqual([t[2] for t in ex["taps"]], [datetime(2026, 10, 1, 7, 2), datetime(2026, 10, 1, 15, 1)])

    def test_daily_layout_with_scan_columns_and_overnight(self):
        raw = csv_bytes("PIN,Nama,Tanggal,Scan 1,Scan 2", "001,Budi,01/10/2026,07:02,15:01", "002,Sari,01/10/2026,21:55,06:04", "003,Dewi,01/10/2026,,")
        ex, m = self.taps(raw, "harian.csv"); self.assertEqual(imp.layout_of(m), "harian")
        self.assertEqual([(t[0], t[2]) for t in ex["taps"]], [("001", datetime(2026, 10, 1, 7, 2)), ("001", datetime(2026, 10, 1, 15, 1)), ("002", datetime(2026, 10, 1, 21, 55)), ("002", datetime(2026, 10, 2, 6, 4))])
        self.assertEqual(ex["invalid"], 0)   # baris tanpa jam = hari tanpa scan, bukan galat

    def test_cell_with_multiple_times(self):
        ex, _ = self.taps(csv_bytes("PIN,Tanggal,Scan", '001,01/10/2026,"07:02\n12:00\n15:01"'), "x.csv"); self.assertEqual(len(ex["taps"]), 3)

    def test_headerless_dat_tab_separated(self):
        raw = b"   1001\t2026-10-01 07:02:11\t1\t1\t0\t0\n   1001\t2026-10-01 15:00:05\t1\t1\t0\t0\n   1002\t2026-10-01 07:10:00\t1\t1\t0\t0\n"
        ex, m = self.taps(raw, "1_attlog.dat"); self.assertEqual((len(ex["taps"]), ex["invalid"]), (3, 0)); self.assertEqual(ex["taps"][0][0], "1001"); self.assertEqual(m["header_row"], -1)

    def test_headerless_dat_space_separated(self):
        ex, _ = self.taps(b"1001 2026-10-01 07:02:11 1 0\n1001 2026-10-01 15:00:05 1 0\n", "attlog.txt"); self.assertEqual(len(ex["taps"]), 2)

    def test_html_disguised_as_xls(self):
        html = b"<html><body><table><tr><th>PIN</th><th>Nama</th><th>Tanggal Scan</th></tr><tr><td>001</td><td>Budi</td><td>01/10/2026 07:02:11</td></tr></table></body></html>"
        ex, _ = self.taps(html, "scan.xls"); self.assertEqual(len(ex["taps"]), 1)

    def test_utf16_tab_export(self):
        raw = ("PIN\tNama\tTanggal Scan\n001\tBudi\t2026-10-01 07:02:11\n").encode("utf-16")
        ex, _ = self.taps(raw, "scan.txt"); self.assertEqual(len(ex["taps"]), 1)

    def test_invalid_rows_counted_and_reported_with_line_numbers(self):
        ex, _ = self.taps(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", ",Budi,2026-10-01 07:03:11,F,M1", "001,Budi,bukan-tanggal,F,M1"), "x.csv")
        self.assertEqual((len(ex["taps"]), ex["invalid"]), (1, 2)); self.assertEqual([e[0] for e in ex["errors"]], [3, 4])

    def test_formula_cells_rejected(self):
        ex, _ = self.taps(csv_bytes(LOG_HDR, "=cmd|calc,Budi,2026-10-01 07:02:11,F,M1"), "x.csv"); self.assertEqual((len(ex["taps"]), ex["invalid"]), (0, 1))

    def test_future_and_absurd_timestamps_rejected(self):
        fut = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        ex, _ = self.taps(csv_bytes(LOG_HDR, f"001,Budi,{fut} 07:00:00,F,M1", "001,Budi,1999-01-01 07:00:00,F,M1"), "x.csv"); self.assertEqual((len(ex["taps"]), ex["invalid"]), (0, 2))

    def test_unrecognized_columns_give_friendly_error(self):
        grid = imp.read_grid(csv_bytes("foo,bar", "1,2"), "x.csv"); m = imp.detect(grid)
        with self.assertRaises(ValueError): imp.extract(grid, m)

    def test_empty_and_oversize_rejected(self):
        with self.assertRaises(ValueError): imp.read_grid(b"  \n ", "x.csv")
        with override_settings(), self.assertRaises(ValueError):
            old = imp.MAX_BYTES; imp.MAX_BYTES = 10
            try: imp.read_grid(b"a" * 50, "x.csv")
            finally: imp.MAX_BYTES = old

    def test_garbage_binary_does_not_crash(self):
        try: imp.read_grid(bytes(range(256)) * 4, "x.dat")
        except ValueError: pass


class ImportServiceTests(Base):
    def stage(self, raw, name="scan.csv"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return imp.stage_upload(SimpleUploadedFile(name, raw), self.hrd)

    FILE = csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", "001,Budi,2026-10-01 15:03:00,F,M1", "003,Dewi,2026-10-01 07:00:00,F,M1", "777,Orang Asing,2026-10-01 07:00:00,F,M1")

    def test_preview_saves_no_scans_and_reports_unmapped(self):
        i = self.stage(self.FILE); r = imp.analyze(i)
        self.assertEqual(AttendanceRaw.objects.count(), 0); self.assertEqual((i.taps_valid, i.taps_new, i.pins_total, i.pins_unmapped), (4, 4, 3, 1))
        self.assertEqual(r["unmapped"][0]["pin"], "777"); self.assertEqual(i.state, "draft")

    def test_commit_links_by_nik_creates_pins_and_keeps_unmapped_orphans(self):
        i = self.stage(self.FILE); imp.commit(i, self.hrd); i.refresh_from_db()
        self.assertEqual(i.state, "selesai"); self.assertEqual(AttendanceRaw.objects.count(), 4)
        self.assertEqual(AttendanceRaw.objects.filter(employee__isnull=True).values_list("pin", flat=True)[0], "777")
        self.assertEqual(set(AttendancePin.objects.values_list("pin", "source")), {("001", "nik"), ("003", "nik")}); self.assertEqual(i.staged_name, "")

    def test_reimport_same_file_is_idempotent(self):
        imp.commit(self.stage(self.FILE), self.hrd); i2 = self.stage(self.FILE); r = imp.analyze(i2); self.assertEqual((i2.taps_new, i2.taps_duplicate), (0, 4))
        imp.commit(i2, self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 4)

    def test_overlapping_file_adds_only_new_taps(self):
        imp.commit(self.stage(self.FILE), self.hrd)
        i = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 15:03:00,F,M1", "001,Budi,2026-10-02 07:00:00,F,M1")); imp.commit(i, self.hrd); i.refresh_from_db()
        self.assertEqual((i.taps_new, i.taps_duplicate), (1, 1)); self.assertEqual(AttendanceRaw.objects.count(), 5)

    def test_same_taps_from_different_devices_are_distinct(self):
        imp.commit(self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:00:00,F,M1", "001,Budi,2026-10-01 07:00:00,F,M2")), self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 2)

    def test_invalid_rows_block_commit_unless_ignored(self):
        i = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:00:00,F,M1", "001,Budi,rusak,F,M1"))
        with self.assertRaises(ValueError): imp.commit(i, self.hrd)
        self.assertEqual(AttendanceRaw.objects.count(), 0)
        imp.commit(i, self.hrd, ignore_invalid=True); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_pin_table_mapping_beats_nik_and_zero_padding_matches(self):
        AttendancePin.objects.create(pin="9001", employee=self.e3, source="manual")
        self.e1.nik = "00123"; self.e1.save()
        i = self.stage(csv_bytes(LOG_HDR, "9001,X,2026-10-01 07:00:00,F,M1", "123,X,2026-10-01 07:00:00,F,M1")); imp.commit(i, self.hrd)
        self.assertEqual(AttendanceRaw.objects.get(pin="9001").employee, self.e3); self.assertEqual(AttendanceRaw.objects.get(pin="123").employee, self.e1)

    def test_link_pin_relinks_orphans_and_move_pin(self):
        imp.commit(self.stage(self.FILE), self.hrd)
        n = imp.link_pin(self.hrd, "777", self.e2); self.assertEqual(n, 1); self.assertEqual(AttendanceRaw.objects.get(pin="777").employee, self.e2)
        with self.assertRaises(ValueError): imp.link_pin(self.hrd, "777", self.e3)   # sudah dipetakan
        old, moved = imp.move_pin(self.hrd, "777", self.e3, "Salah orang"); self.assertEqual((old, moved), (self.e2, 1)); self.assertEqual(AttendanceRaw.objects.get(pin="777").employee, self.e3)
        with self.assertRaises(ValueError): imp.move_pin(self.hrd, "777", self.e2, "")

    def test_raw_log_is_immutable(self):
        imp.commit(self.stage(self.FILE), self.hrd); r = AttendanceRaw.objects.first()
        with self.assertRaises(PermissionError): r.save()
        with self.assertRaises(PermissionError): r.delete()

    def test_name_suggestion_for_unmapped_pin(self):
        i = self.stage(csv_bytes(LOG_HDR, "5555,Sari,2026-10-01 07:00:00,F,M1")); r = imp.analyze(i); self.assertEqual(r["unmapped"][0]["suggest"], self.e2)

    def test_manual_mapping_override(self):
        raw = csv_bytes("A,B,C", "001,x,2026-10-01 07:00:00"); i = self.stage(raw, "weird.csv")
        with self.assertRaises(ValueError): imp.analyze(i)
        from django.http import QueryDict
        m = imp.mapping_from_post(QueryDict("map_key=0&map_datetime=2&map_header=0"), 3); r = imp.analyze(i, m); self.assertEqual(i.taps_valid, 1)
        with self.assertRaises(ValueError): imp.mapping_from_post(QueryDict("map_key=9"), 3)

    def test_cancel_and_purge_remove_staged_file(self):
        from pathlib import Path
        i = self.stage(self.FILE); p = imp._stage_path(i.staged_name); self.assertTrue(p.exists()); imp.cancel(i); self.assertFalse(p.exists()); i.refresh_from_db(); self.assertEqual(i.state, "dibatalkan")
        j = self.stage(self.FILE); AttendanceImport.objects.filter(pk=j.pk).update(created_at=timezone.now() - timedelta(hours=30)); self.assertEqual(imp.purge_stale(), 1)

    def test_pin_table_import_all_or_nothing(self):
        ok = csv_bytes("pin,nik", "9001,001", "9002,003"); r = imp.import_pin_table(self.hrd, ok, "m.csv", False); self.assertTrue(r["ok"]); self.assertEqual(AttendancePin.objects.count(), 0)
        r = imp.import_pin_table(self.hrd, ok, "m.csv", True); self.assertEqual(r["created"], 2); self.assertEqual(AttendancePin.objects.count(), 2)
        bad = csv_bytes("pin,nik", "9003,001", "9004,NOPE", "9003,002"); r = imp.import_pin_table(self.hrd, bad, "m.csv", True); self.assertFalse(r["ok"]); self.assertEqual(AttendancePin.objects.count(), 2)

    def test_imported_orphans_become_rows_after_linking(self):
        d = workday(); raw = csv_bytes(LOG_HDR, f"777,X,{d:%Y-%m-%d} 07:00:00,F,M1", f"777,X,{d:%Y-%m-%d} 15:00:00,F,M1")
        imp.commit(self.stage(raw), self.hrd); self.e2.shift = self.pagi; self.e2.save()
        calc.process(d, d); self.assertEqual(AttendanceDaily.objects.get(employee=self.e2, date=d).status, "tanpa_scan")
        imp.link_pin(self.hrd, "777", self.e2); calc.process(d, d); x = AttendanceDaily.objects.get(employee=self.e2, date=d); self.assertEqual((x.status, x.scan_count), ("hadir", 2))
