"""Putaran 62: impor scanlog tanpa input ganda — duplikat dilewati, kegagalan dijelaskan, tidak ada data/riwayat redundan."""
import threading
from datetime import date, timedelta
from io import StringIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from apps.hr import attendance_import as imp
from apps.hr.models import AttendanceImport, AttendancePin, AttendanceRaw
from apps.hr.test_attendance import Base, LOG_HDR, csv_bytes


class DupBase(Base):
    def stage(self, raw, name="scan.csv"): return imp.stage_upload(SimpleUploadedFile(name, raw), self.hrd)


class NoDuplicateTests(DupBase):
    def test_same_file_twice_stores_nothing_and_leaves_no_empty_history(self):
        raw = csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", "003,Dewi,2026-10-01 07:00:00,F,M1")
        imp.commit(self.stage(raw), self.hrd)
        j = self.stage(raw); imp.commit(j, self.hrd); j.refresh_from_db()
        self.assertEqual(AttendanceRaw.objects.count(), 2)
        self.assertEqual((j.state, j.taps_new, j.taps_duplicate), ("dibatalkan", 0, 2)); self.assertIn("semua 2 scan sudah ada", j.note); self.assertEqual(j.staged_name, "")
        self.assertEqual(AttendanceImport.objects.filter(state="selesai").count(), 1)   # tidak ada riwayat "selesai" kosong

    def test_preview_warns_when_file_is_identical_to_a_finished_import(self):
        raw = csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1")
        imp.commit(self.stage(raw), self.hrd); r = imp.analyze(self.stage(raw)); self.assertIsNotNone(r["same_file"]); self.assertEqual(r["imp"].taps_new, 0)

    def test_same_scan_in_file_without_device_column_is_not_doubled(self):
        imp.commit(self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1")), self.hrd)
        j = self.stage(csv_bytes("PIN,Nama,Tanggal Scan", "001,Budi,2026-10-01 07:02:11")); imp.analyze(j)
        self.assertEqual((j.taps_new, j.taps_duplicate), (0, 1)); imp.commit(j, self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_reverse_direction_unknown_device_first_then_named(self):
        imp.commit(self.stage(csv_bytes("PIN,Nama,Tanggal Scan", "001,Budi,2026-10-01 07:02:11")), self.hrd)
        j = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1")); imp.commit(j, self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_leading_zero_pin_variants_are_the_same_scan(self):
        imp.commit(self.stage(csv_bytes(LOG_HDR, "0012,Budi,2026-10-01 07:02:11,F,M1")), self.hrd)
        j = self.stage(csv_bytes(LOG_HDR, "12,Budi,2026-10-01 07:02:11,F,M1")); imp.analyze(j); self.assertEqual((j.taps_new, j.taps_duplicate), (0, 1))

    def test_twin_rows_inside_one_file_are_counted_and_skipped(self):
        j = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", "001,Budi,2026-10-01 07:02:11,F,M1", "0001,Budi,2026-10-01 07:02:11,F,M1"))
        r = imp.analyze(j); self.assertEqual((j.taps_valid, j.taps_new, r["dup_file"]), (1, 1, 2)); imp.commit(j, self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_two_named_devices_remain_distinct(self):
        imp.commit(self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:00:00,F,M1", "001,Budi,2026-10-01 07:00:00,F,M2")), self.hrd); self.assertEqual(AttendanceRaw.objects.count(), 2)

    def test_no_second_pin_row_for_equivalent_pin(self):
        self.e1.nik = "00123"; self.e1.save()
        imp.commit(self.stage(csv_bytes(LOG_HDR, "00123,X,2026-10-01 07:00:00,F,M1")), self.hrd)
        imp.commit(self.stage(csv_bytes(LOG_HDR, "123,X,2026-10-02 07:00:00,F,M1")), self.hrd)
        self.assertEqual(AttendancePin.objects.filter(employee=self.e1).count(), 1)
        self.assertEqual(AttendanceRaw.objects.filter(employee=self.e1).count(), 2)   # kedua scan tetap terhubung ke karyawan

    def test_committed_count_matches_what_was_really_stored(self):
        j = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", "003,Dewi,2026-10-01 07:00:00,F,M1", "003,Dewi,2026-10-01 15:00:00,F,M1"))
        imp.commit(j, self.hrd); j.refresh_from_db(); self.assertEqual(j.taps_new, AttendanceRaw.objects.filter(batch=j).count()); self.assertEqual(j.taps_new, 3)

    def test_second_commit_of_same_draft_is_rejected(self):
        j = self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1")); imp.commit(j, self.hrd)
        stale = AttendanceImport.objects.get(pk=j.pk); stale.state = "draft"   # salinan lama di tab lain yang masih mengira draft
        with self.assertRaises(ValueError): imp.commit(stale, self.hrd)
        self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_pin_table_reports_already_mapped_as_skipped(self):
        AttendancePin.objects.create(pin="9001", employee=self.e1, source="manual")
        r = imp.import_pin_table(self.hrd, csv_bytes("pin,nik", "9001,001", "9002,003"), "m.csv", True)
        self.assertEqual((r["created"], r["skipped"]), (1, 1)); self.assertEqual(AttendancePin.objects.count(), 2)

    def test_export_then_import_roundtrip_adds_nothing(self):
        imp.commit(self.stage(csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1", "003,Dewi,2026-10-01 07:00:00,F,M2")), self.hrd)
        self.login("hrd"); r = self.client.get(reverse("att_scans"), {"from": "2026-10-01", "to": "2026-10-01", "export": "1"}); self.assertEqual(r.status_code, 200)
        j = self.stage(r.content, "scanlog.csv"); imp.analyze(j); self.assertEqual((j.taps_new, j.taps_duplicate), (0, 2))


class FailureExplanationTests(DupBase):
    BAD = ("001,Budi,2026-10-01 07:00:00,F,M1", ",Budi,2026-10-01 07:01:00,F,M1", "001,Budi,bukan-tanggal,F,M1", "=cmd|x,Budi,2026-10-01 07:02:00,F,M1", "001,Budi,1999-01-01 07:00:00,F,M1")

    def test_groups_and_row_details_are_reported_with_hints(self):
        j = self.stage(csv_bytes(LOG_HDR, *self.BAD)); r = imp.analyze(j)
        codes = {g["code"]: g["n"] for g in r["report"]["groups"]}; self.assertEqual(codes, {"key": 1, "time": 1, "formula": 1, "absurd": 1})
        self.assertTrue(all(g["hint"] and g["label"] for g in r["report"]["groups"])); self.assertEqual([x["row"] for x in r["report"]["rows"]], [3, 4, 5, 6])

    def test_report_survives_commit_with_ignore_invalid(self):
        j = self.stage(csv_bytes(LOG_HDR, *self.BAD)); imp.commit(j, self.hrd, ignore_invalid=True); j.refresh_from_db()
        self.assertEqual((j.state, j.rows_invalid), ("selesai", 4)); self.assertEqual(len(j.report["rows"]), 4); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_formula_chars_not_echoed_into_report(self):
        j = self.stage(csv_bytes(LOG_HDR, "=cmd|x,Budi,2026-10-01 07:02:00,F,M1", "001,Budi,2026-10-01 07:00:00,F,M1")); r = imp.analyze(j)
        self.assertFalse(r["report"]["rows"][0]["pin"].startswith(("=", "@", "+", "-")))

    def test_error_list_download_and_pages_show_reasons(self):
        self.login("hrd"); j = self.stage(csv_bytes(LOG_HDR, *self.BAD)); u = reverse("att_import_detail", args=[j.pk])
        r = self.client.get(u); self.assertContains(r, "baris tidak bisa diimpor"); self.assertContains(r, "Tanggal/jam tidak terbaca")
        self.client.post(u, {"action": "commit", "ignore_invalid": "on"}); r = self.client.get(u); self.assertContains(r, "4 baris tidak diimpor"); self.assertContains(r, "Unduh daftar baris gagal")
        d = self.client.get(reverse("att_import_errors", args=[j.pk]), {"format": "csv"}); body = d.content.decode()
        self.assertEqual(d.status_code, 200); self.assertIn("baris_di_berkas", body); self.assertIn("Tanggal/jam tidak terbaca", body); self.assertEqual(len(body.strip().splitlines()), 5)
        self.client.logout(); self.assertEqual(self.client.get(reverse("att_import_errors", args=[j.pk])).status_code, 302)
        self.login("poli"); self.assertEqual(self.client.get(reverse("att_import_errors", args=[j.pk])).status_code, 403)

    def test_all_duplicate_commit_via_web_explains_instead_of_silent_empty_import(self):
        self.login("hrd"); raw = csv_bytes(LOG_HDR, "001,Budi,2026-10-01 07:02:11,F,M1"); imp.commit(self.stage(raw), self.hrd); j = self.stage(raw)
        r = self.client.post(reverse("att_import_detail", args=[j.pk]), {"action": "commit"}, follow=True); self.assertContains(r, "Tidak ada scan baru"); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_cli_prints_reasons_and_skips_identical_file(self):
        import os, tempfile
        d = tempfile.mkdtemp(); p = os.path.join(d, "a.csv"); open(p, "wb").write(csv_bytes(LOG_HDR, *self.BAD))
        out = StringIO(); call_command("import_attendance", p, "--commit", "--ignore-invalid", stdout=out); t = out.getvalue()
        self.assertIn("Tanggal/jam tidak terbaca", t); self.assertIn("baris 3", t)
        out = StringIO(); call_command("import_attendance", p, "--commit", "--ignore-invalid", stdout=out); self.assertIn("sudah pernah diimpor", out.getvalue()); self.assertEqual(AttendanceRaw.objects.count(), 1)


class ConcurrentCommitTests(TransactionTestCase):
    """Dua sesi mengimpor berkas yang sama bersamaan: hasil akhir tetap tepat satu salinan tiap scan (kunci unik + penguncian draft)."""
    def test_parallel_commits_of_overlapping_files_do_not_duplicate(self):
        from apps.core.models import Role, User
        from apps.hr.models import Department, Employee, Position
        dep = Department.objects.create(code="PRD", name="Produksi"); pos = Position.objects.create(name="Staff")
        Employee.objects.create(nik="001", name="Budi", gender="L", department=dep, position=pos, join_date=date(2024, 1, 1))
        u = User.objects.create_user("h", password="x-Sandi-Kuat-123", role=Role.HRD)
        raw = csv_bytes(LOG_HDR, *[f"001,Budi,2026-10-01 {h:02d}:00:00,F,M1" for h in range(1, 20)])
        drafts = [imp.stage_upload(SimpleUploadedFile("a.csv", raw), u) for _ in range(3)]; errs = []

        def go(i):
            try: imp.commit(AttendanceImport.objects.get(pk=i.pk), u)
            except Exception as e: errs.append(e)
            finally: connection.close()
        ts = [threading.Thread(target=go, args=(i,)) for i in drafts]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(AttendanceRaw.objects.count(), 19, errs)
        self.assertEqual(sum(AttendanceRaw.objects.filter(batch=i).count() for i in drafts), 19)   # setiap scan dimiliki tepat satu impor


class AuditCommandTests(DupBase):
    def test_audit_reports_legacy_doubles_without_deleting(self):
        from apps.hr.test_attendance import at
        d = date(2026, 10, 1)
        for pin, dev in (("001", "FINGERSPOT"), ("001", "M1"), ("0003", "M1"), ("3", "M1")): AttendanceRaw.objects.create(pin=pin, device_id=dev, punched_at=at(d, 7, 0))
        out = StringIO(); call_command("audit_attendance_dupes", stdout=out); t = out.getvalue()
        self.assertIn("2 kelompok ganda", t); self.assertIn("2 baris berlebih", t); self.assertEqual(AttendanceRaw.objects.count(), 4)


class BackupScriptGuards(__import__("django.test", fromlist=["SimpleTestCase"]).SimpleTestCase):
    """backup.sh / restore.sh diuji manual di PostgreSQL 16 (putaran 62); tes ini menjaga pengaman utamanya tidak hilang."""
    def read(self, n):
        from pathlib import Path
        return (Path(__file__).resolve().parents[2] / "scripts" / n).read_text(encoding="utf-8")

    def test_backup_has_checksum_manifest_and_skips_staging(self):
        s = self.read("backup.sh")
        for k in ("sha256sum", ".manifest.json", "--exclude='media/attendance_stage'", "information_schema.tables", "key_fingerprint", "hris_private_", "BACKUP_RSYNC_TARGET", "$SUM"): self.assertIn(k, s, k)   # manifest = SEMUA tabel (bukan daftar tetap)

    def test_restore_verifies_confirms_snapshots_and_is_atomic(self):
        s = self.read("restore.sh")
        for k in ("sha256sum -c", "RESTORE_YES", "pre_restore_", "--single-transaction", "--exit-on-error", "attendance_stage", "purge_stale", "migrate --noinput", "manifest.json", "verify_restore"): self.assertIn(k, s, k)

    def test_restore_drill_never_touches_production_db_and_cleans_up(self):
        s = self.read("restore_drill.sh")
        for k in ("create database", "drop database if exists", "trap cleanup EXIT", "verify_restore", "restore_drill.json", "send_alert", "sha256sum -c", "--single-transaction", "DRILL_DB sama dengan database produksi"): self.assertIn(k, s, k)
        self.assertNotIn("--clean", s)   # latihan tidak boleh memakai --clean (itu untuk restore sungguhan)
