"""Putaran 48: halaman Absensi — izin akses, alur impor web, pemetaan PIN, hari libur, olah ulang, minta validasi, dasbor, perintah server."""
import os, tempfile
from datetime import timedelta
from io import StringIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.hrd.test_base import KEY
from apps.core.models import AuditLog, Role, User
from apps.hr import attendance_calc as calc
from apps.hr.attendance_import import _stage_path as _stage
from apps.hr.models import AttendanceCheck, AttendanceDaily, AttendanceImport, AttendancePin, AttendanceRaw, Holiday
from apps.hr.test_attendance import Base, LOG_HDR, csv_bytes, workday, xlsx_bytes


class PageBase(Base):
    def up(self, raw, name="scan.csv"):
        self.login("hrd"); r = self.client.post(reverse("att_import"), {"file": SimpleUploadedFile(name, raw)}); return r

    def file_for(self, d, *niks):
        lines = [LOG_HDR]
        for n in niks: lines += [f"{n},X,{d:%Y-%m-%d} 07:10:00,F,M1", f"{n},X,{d:%Y-%m-%d} 15:00:00,F,M1"]
        return csv_bytes(*lines)


class AccessTests(PageBase):
    URLS = ["att_daily", "att_monthly", "att_import", "att_pins", "att_holidays"]

    def test_poli_forbidden_and_anonymous_blocked(self):
        for n in self.URLS + ["att_employee"]:
            url = reverse(n, args=[self.e1.pk]) if n == "att_employee" else reverse(n)
            self.client.logout(); self.assertIn(self.client.get(url).status_code, (401, 302), n)
            self.login("poli"); self.assertEqual(self.client.get(url).status_code, 403, n)
        for n in ("att_process", "att_ask"): self.login("poli"); self.assertEqual(self.client.post(reverse(n)).status_code, 403, n)

    def test_dept_admin_read_only_scope(self):
        self.login("adm")
        for n in ("att_daily", "att_monthly"): self.assertEqual(self.client.get(reverse(n)).status_code, 200, n)
        for n in ("att_import", "att_pins", "att_holidays"): self.assertEqual(self.client.get(reverse(n)).status_code, 403, n)
        for n in ("att_process", "att_ask"): self.assertEqual(self.client.post(reverse(n)).status_code, 403, n)
        self.assertEqual(self.client.get(reverse("att_employee", args=[self.e1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("att_employee", args=[self.e2.pk])).status_code, 404)   # departemen lain

    def test_dept_admin_sees_only_own_department_and_no_raw_scans(self):
        d = workday(); self.e2.shift = self.pagi; self.e2.save(); self.scan(self.e1, d, (7, 0), (15, 0)); self.scan(self.e2, d, (7, 0), (15, 0)); calc.process(d, d)
        self.login("adm"); r = self.client.get(reverse("att_daily"), {"from": d, "to": d}); self.assertContains(r, "Budi"); self.assertNotContains(r, "Sari")
        r = self.client.get(reverse("att_employee", args=[self.e1.pk]), {"month": d.strftime("%Y-%m")}); self.assertNotContains(r, "Semua scan")
        self.login("hrd"); r = self.client.get(reverse("att_employee", args=[self.e1.pk]), {"month": d.strftime("%Y-%m")}); self.assertContains(r, "Semua scan")

    def test_nav_per_role(self):
        self.login("hrd"); h = self.client.get("/").content.decode(); self.assertIn("/absensi/scanlog/", h); self.assertNotIn('href="/absensi/impor/"', h); self.assertIn("/absensi/pin/", h)
        self.login("adm"); h = self.client.get("/").content.decode(); self.assertIn("/absensi/bulanan/", h); self.assertNotIn("/absensi/impor/", h)


class ImportFlowTests(PageBase):
    def test_upload_preview_commit_processes_and_audits_without_row_data(self):
        d = workday(); r = self.up(self.file_for(d, "001", "003")); self.assertEqual(r.status_code, 302); i = AttendanceImport.objects.get()
        r = self.client.get(reverse("att_import_detail", args=[i.pk])); self.assertContains(r, "Pratinjau"); self.assertEqual(AttendanceRaw.objects.count(), 0)
        r = self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "commit"}, follow=True); self.assertContains(r, "Impor selesai")
        self.assertEqual(AttendanceRaw.objects.count(), 4); x = AttendanceDaily.objects.get(employee=self.e1, date=d); self.assertEqual((x.status, x.late_minutes), ("hadir", 10))
        a = AuditLog.objects.get(action="attendance_import"); self.assertEqual(a.after["new"], 4); self.assertNotIn("07:10", str(a.after))
        self.assertEqual(AttendanceImport.objects.get().state, "selesai")

    def test_bad_file_reports_friendly_error(self):
        r = self.up(b"hanya,teks\nbiasa,saja", "x.csv"); self.assertEqual(r.status_code, 302); r = self.client.get(r["Location"]); self.assertContains(r, "Pemetaan kolom")
        r = self.up(b"   ", "x.csv"); self.assertEqual(r.status_code, 200); self.assertContains(r, "kosong")

    def test_preview_post_with_mapping_then_commit(self):
        d = workday(); self.up(csv_bytes("A,B,C", f"001,x,{d:%Y-%m-%d} 07:00:00", f"001,x,{d:%Y-%m-%d} 15:00:00")); i = AttendanceImport.objects.get(); url = reverse("att_import_detail", args=[i.pk])
        r = self.client.post(url, {"action": "preview", "map_posted": "1", "map_key": "0", "map_datetime": "2", "map_header": "0"}); self.assertContains(r, "scan terbaca")
        self.client.post(url, {"action": "commit", "map_posted": "1", "map_key": "0", "map_datetime": "2", "map_header": "0"}); self.assertEqual(AttendanceRaw.objects.count(), 2)

    def test_invalid_mapping_value_rejected_gracefully(self):
        self.up(self.file_for(workday(), "001")); i = AttendanceImport.objects.get()
        r = self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "commit", "map_posted": "1", "map_key": "99"}); self.assertContains(r, "tidak valid"); self.assertEqual(AttendanceRaw.objects.count(), 0)

    def test_cancel_removes_draft_file(self):
        self.up(self.file_for(workday(), "001")); i = AttendanceImport.objects.get(); p = _stage(i.staged_name); self.assertTrue(p.exists())
        self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "cancel"}); self.assertFalse(p.exists()); self.assertEqual(AttendanceImport.objects.get().state, "dibatalkan")

    def test_finished_import_shows_summary_and_cannot_recommit(self):
        d = workday(); self.up(self.file_for(d, "001")); i = AttendanceImport.objects.get(); u = reverse("att_import_detail", args=[i.pk]); self.client.post(u, {"action": "commit"})
        self.assertContains(self.client.get(u), "scan baru"); self.client.post(u, {"action": "commit"}); self.assertEqual(AttendanceRaw.objects.count(), 2)

    def test_unknown_import_404_and_xlsx_upload(self):
        self.login("hrd"); self.assertEqual(self.client.get(reverse("att_import_detail", args=[9999])).status_code, 404)
        d = workday(); raw = xlsx_bytes([["PIN", "Nama", "Tanggal Scan"], ["001", "Budi", f"{d:%d/%m/%Y} 07:00:00"], ["001", "Budi", f"{d:%d/%m/%Y} 15:00:00"]])
        self.up(raw, "scan.xlsx"); i = AttendanceImport.objects.get(); self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "commit"}); self.assertEqual(AttendanceRaw.objects.count(), 2)


class PinHolidayTests(PageBase):
    def test_link_orphan_pin_via_page_reprocesses(self):
        d = workday(); self.e2.shift = self.pagi; self.e2.save(); self.up(self.file_for(d, "777")); i = AttendanceImport.objects.get(); self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "commit"})
        self.assertEqual(AttendanceDaily.objects.get(employee=self.e2, date=d).status, "tanpa_scan")
        r = self.client.get(reverse("att_pins")); self.assertContains(r, "777")
        r = self.client.post(reverse("att_pins"), {"action": "link", "pin": "777", "nik": "002"}, follow=True); self.assertContains(r, "2 scan lama")
        self.assertEqual(AttendanceDaily.objects.get(employee=self.e2, date=d).status, "hadir")
        self.assertEqual(AuditLog.objects.get(action="attendance_pin_link").after["scans"], 2)

    def test_link_unknown_nik_and_move_needs_reason(self):
        self.login("hrd"); r = self.client.post(reverse("att_pins"), {"action": "link", "pin": "5", "nik": "NOPE"}, follow=True); self.assertContains(r, "NIK tidak ditemukan")
        AttendancePin.objects.create(pin="5", employee=self.e1)
        r = self.client.post(reverse("att_pins"), {"action": "move", "pin": "5", "nik": "003", "reason": ""}, follow=True); self.assertContains(r, "Alasan wajib")
        self.client.post(reverse("att_pins"), {"action": "move", "pin": "5", "nik": "003", "reason": "Salah orang"}); self.assertEqual(AttendancePin.objects.get(pin="5").employee, self.e3)

    def test_pin_csv_import_via_page(self):
        self.login("hrd"); f = SimpleUploadedFile("m.csv", csv_bytes("pin,nik", "9001,001"))
        r = self.client.post(reverse("att_pins"), {"action": "import", "file": f}, follow=True); self.assertEqual(AttendancePin.objects.get().pin, "9001")

    def test_holiday_crud_and_effect_after_reprocess(self):
        self.login("hrd"); d = workday(); r = self.client.post(reverse("att_holidays"), {"action": "add", "date": d.isoformat(), "name": "Libur Uji", "kind": "nasional", "applies": "all"}, follow=True)
        self.assertContains(r, "Libur Uji"); self.assertContains(self.client.post(reverse("att_holidays"), {"action": "add", "date": d.isoformat(), "name": "Dobel"}, follow=True), "sudah ada di daftar"); self.assertEqual(Holiday.objects.count(), 1)
        self.client.post(reverse("att_process"), {"from": d.isoformat(), "to": d.isoformat()}); self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "libur_nasional")
        h = Holiday.objects.get(); self.client.post(reverse("att_holidays"), {"action": "toggle", "id": h.pk}); self.assertFalse(Holiday.objects.get().active)
        r = self.client.post(reverse("att_holidays"), {"action": "delete", "id": h.pk}, follow=True); self.assertContains(r, "sudah lewat"); self.assertEqual(Holiday.objects.count(), 1)

    def test_holiday_form_rejects_garbage(self):
        self.login("hrd"); self.client.post(reverse("att_holidays"), {"action": "add", "date": "bukan", "name": "x"}); self.assertEqual(Holiday.objects.count(), 0)
        self.assertEqual(self.client.get(reverse("att_holidays"), {"year": "abc"}).status_code, 200)


class ListAndActionTests(PageBase):
    def seed(self):
        d1, d2 = workday(2), workday(1); self.scan(self.e1, d1, (7, 30), (15, 0)); self.scan(self.e1, d2, (7, 0), (15, 0)); calc.process(d1, d2); return d1, d2

    def test_daily_filters_and_review_queue(self):
        d1, d2 = self.seed(); self.login("hrd"); u = reverse("att_daily"); p = {"from": d1, "to": d2}
        self.assertContains(self.client.get(u, {**p, "status": "late"}), "+30m")
        r = self.client.get(u, {**p, "status": "review"}); self.assertContains(r, "Dewi"); self.assertNotContains(r, "Budi")   # Dewi tanpa scan
        self.assertContains(self.client.get(u, {**p, "q": "Dewi"}), "Dewi"); self.assertNotContains(self.client.get(u, {**p, "q": "Dewi"}), "Budi")
        self.assertEqual(self.client.get(u, {"from": "ngawur"}).status_code, 200)

    def test_daily_and_monthly_exports(self):
        d1, d2 = self.seed(); self.login("hrd")
        r = self.client.get(reverse("att_daily"), {"from": d1, "to": d2, "export": 1, "format": "xlsx"}); self.assertEqual(r.content[:2], b"PK")
        r = self.client.get(reverse("att_daily"), {"from": d1, "to": d2, "export": 1}); self.assertIn("001,Budi", r.content.decode("utf-8-sig")); self.assertTrue(AuditLog.objects.filter(action="attendance_daily_export").exists())
        r = self.client.get(reverse("att_monthly"), {"month": d2.strftime("%Y-%m"), "export": 1}); self.assertIn("rekap-absensi", r["Content-Disposition"]); self.assertIn("001", r.content.decode("utf-8-sig"))
        self.assertContains(self.client.get(reverse("att_monthly"), {"month": "ngawur"}), "Rekap bulanan")

    def test_reprocess_rejects_bad_ranges(self):
        self.login("hrd"); r = self.client.post(reverse("att_process"), {"from": "x"}, follow=True); self.assertContains(r, "tidak valid")
        r = self.client.post(reverse("att_process"), {"from": "2020-01-01", "to": "2026-01-01"}, follow=True); self.assertContains(r, "maksimal 93")

    def test_ask_validation_creates_checks_skips_existing_and_noadmin(self):
        d = workday(); self.e2.shift = self.pagi; self.e2.save(); self.scan(self.e3, d, (7, 0)); calc.process(d, d)   # e1 tanpa scan, e3 satu scan, e2 (Gudang, tanpa admin) tanpa scan
        self.login("hrd"); r = self.client.post(reverse("att_ask"), {"from": d.isoformat(), "to": d.isoformat()}, follow=True)
        self.assertEqual(set(AttendanceCheck.objects.values_list("employee__nik", flat=True)), {"001", "003"}); self.assertContains(r, "Gudang")
        self.assertIn("Mesin:", AttendanceCheck.objects.get(employee=self.e3).hint)
        r = self.client.post(reverse("att_ask"), {"from": d.isoformat(), "to": d.isoformat()}, follow=True); self.assertEqual(AttendanceCheck.objects.count(), 2); self.assertContains(r, "sudah punya permintaan aktif")

    def test_full_loop_validation_to_recap(self):
        from apps.hr import attendance_check as svc
        d = workday(); calc.process(d, d); self.login("hrd"); self.client.post(reverse("att_ask"), {"from": d.isoformat(), "to": d.isoformat()})
        a = AttendanceCheck.objects.get(employee=self.e1); svc.answer(self.adm, a.pk, "sakit", ""); svc.verify(self.hrd, a.pk, "terima")
        self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "sakit")

    def test_employee_page_shows_pins_and_scans(self):
        d = workday(); self.scan(self.e1, d, (7, 0), (15, 0)); AttendancePin.objects.create(pin="001", employee=self.e1); calc.process(d, d); self.login("hrd")
        r = self.client.get(reverse("att_employee", args=[self.e1.pk]), {"month": d.strftime("%Y-%m")}); self.assertContains(r, "PIN mesin: 001"); self.assertContains(r, "07:00 · 15:00")

    def test_employee_detail_links_to_attendance(self):
        self.login("hrd"); self.assertContains(self.client.get(reverse("employee_detail_page", args=[self.e1.pk])), reverse("att_employee", args=[self.e1.pk]))


class DashboardTests(PageBase):
    def test_panel_empty_then_real_numbers_scoped(self):
        self.login("hrd"); self.assertIsNone(self.client.get("/api/dashboard/panels/").json()["attendance"])
        d = workday(); self.scan(self.e1, d, (7, 0), (15, 0)); calc.process(d, d)
        a = self.client.get("/api/dashboard/panels/").json()["attendance"]; self.assertEqual(a["total"], 3); self.assertEqual({i["label"]: i["n"] for i in a["items"]}["Hadir"], 1)
        self.login("adm"); self.assertEqual(self.client.get("/api/dashboard/panels/").json()["attendance"]["total"], 2)   # e1 + e3 (Produksi)
        self.login("poli"); self.assertNotIn("attendance", self.client.get("/api/dashboard/panels/").json())

    def test_cards_review_and_unmapped(self):
        d = workday(); calc.process(d, d); self.login("hrd"); j = self.client.get("/api/dashboard/").json(); self.assertGreaterEqual(j["att_review"], 2); self.assertNotIn("att_unmapped", j)
        AttendanceRaw.objects.create(pin="888", device_id="FP", punched_at=at_(d)); self.assertEqual(self.client.get("/api/dashboard/").json()["att_unmapped"], 1)
        self.login("adm"); j = self.client.get("/api/dashboard/").json(); self.assertIn("att_review", j); self.assertNotIn("att_unmapped", j)


def at_(d):
    from apps.hr.test_attendance import at
    return at(d, 7, 0)


class CommandTests(PageBase):
    def test_process_attendance_command(self):
        d = workday(1); self.scan(self.e1, d, (7, 0), (15, 0)); out = StringIO(); call_command("process_attendance", "--days", "5", stdout=out)
        self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "hadir"); self.assertIn("Rekap diolah", out.getvalue())

    def test_import_attendance_command_preview_and_commit_and_dedupe(self):
        d = workday(); out = StringIO()
        with tempfile.TemporaryDirectory() as t:
            open(os.path.join(t, "a.csv"), "wb").write(self.file_for(d, "001"))
            call_command("import_attendance", t, stdout=out); self.assertEqual(AttendanceRaw.objects.count(), 0); self.assertIn("Pratinjau saja", out.getvalue())
            call_command("import_attendance", t, "--commit", stdout=out); self.assertEqual(AttendanceRaw.objects.count(), 2); self.assertEqual(AttendanceDaily.objects.get(employee=self.e1, date=d).status, "hadir")
            out2 = StringIO(); call_command("import_attendance", t, "--commit", stdout=out2); self.assertIn("sudah pernah diimpor", out2.getvalue()); self.assertEqual(AttendanceRaw.objects.count(), 2)


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class SeedDemoAttendanceTests(TestCase):
    def test_seed_demo_fills_machine_scans_and_recap(self):
        call_command("seed_demo", "--employees", "30", stdout=StringIO())   # kelas dasar sudah menyetel FIELD_ENCRYPTION_KEY
        self.assertGreater(AttendanceRaw.objects.count(), 100); self.assertEqual(AttendanceRaw.objects.filter(employee__isnull=True).values("pin").distinct().count(), 2)
        st = set(AttendanceDaily.objects.values_list("status", flat=True)); self.assertIn("hadir", st); self.assertIn("tanpa_scan", st)
        self.assertTrue(AttendancePin.objects.filter(source="nik").exists()); self.assertEqual(AttendanceImport.objects.get(state="selesai").original_name, "demo-scan-fingerspot.csv")
