import json, threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from apps.hr.attendance_sync import SyncError, check_host, sync_device
from apps.hr.models import AttendanceDevice, AttendanceRaw

DATA = [{"SN": "FP01", "ScanDate": "2026-10-27 07:58:10", "PIN": "101", "VerifyMode": 1, "IOMode": 1},
        {"SN": "FP01", "ScanDate": "2026-10-27 16:03:44", "PIN": "101", "VerifyMode": 1, "IOMode": 2}]


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps({"Result": self.server.ok, "Data": DATA, "Message": "bad"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


class SyncTests(TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), H); self.srv.ok = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.shutdown)
        self.dev = AttendanceDevice.objects.create(code="FP01", name="Gerbang", host="127.0.0.1", port=self.srv.server_port, sync_enabled=True, comm_key="0")

    def test_sync_idempotent(self):
        r = sync_device(self.dev, date(2026, 10, 26), date(2026, 10, 28))
        self.assertTrue(r["ok"]); self.assertEqual((r["fetched"], r["new"]), (2, 2)); self.assertEqual(AttendanceRaw.objects.count(), 2)
        r = sync_device(self.dev, date(2026, 10, 26), date(2026, 10, 28))
        self.assertTrue(r["ok"]); self.assertEqual(AttendanceRaw.objects.count(), 2)
        self.dev.refresh_from_db(); self.assertTrue(self.dev.last_sync_ok)

    def test_dry_run_saves_nothing(self):
        sync_device(self.dev, date(2026, 10, 26), date(2026, 10, 28), commit=False); self.assertEqual(AttendanceRaw.objects.count(), 0)

    def test_server_refuses(self):
        self.srv.ok = False; r = sync_device(self.dev)
        self.assertFalse(r["ok"]); self.dev.refresh_from_db(); self.assertFalse(self.dev.last_sync_ok)

    def test_unreachable(self):
        self.dev.port = 1; self.assertFalse(sync_device(self.dev)["ok"])

    def test_public_host_rejected(self):
        with self.assertRaises(SyncError): check_host("8.8.8.8")
        with self.assertRaises(SyncError): check_host("10.0.0.1/evil")

    def test_command(self):
        call_command("sync_attendance", "--from", "2026-10-26", "--to", "2026-10-28"); self.assertEqual(AttendanceRaw.objects.count(), 2)
        AttendanceDevice.objects.update(sync_enabled=False)
        with self.assertRaises(CommandError): call_command("sync_attendance")
