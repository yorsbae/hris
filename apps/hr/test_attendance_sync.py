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
        self.dev = AttendanceDevice.objects.create(code="FP01", name="Gerbang", host="127.0.0.1", protocol="easylink", port=self.srv.server_port, sync_enabled=True, comm_key="0")

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


# ------------------------------------------------------------------ langsung ke mesin (seperti Fingerspot Personnel) + halaman Mesin
import socket
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from django.urls import reverse
from apps.core.models import Role, User
from apps.hr import attendance_sync as sy
from apps.hr.models import AttendancePin

TABLE = ("Pilih\tNama\tSN\tNo Mesin\tKoneksi\tIP Address / URL\tLayar\n"
         "\tA.PENDAFTARAN 1\tFIO616202022322344\t11\tEthernet\t192.168.10.36\tTFT\n"
         "\tPACK 1A\t616202022322406\t5\tEthernet\t192.168.10.248\tTFT\n"
         "\tLUAR\t616202022999999\t6\tEthernet\t8.8.8.8\tTFT\n")


class FakeZK:
    """Meniru pustaka pyzk: satu 'mesin' dengan 3 scan (satu di luar rentang)."""
    log = []
    def __init__(self, host, port=4370, timeout=5, password=0, ommit_ping=False): self.host, self.port = host, port; FakeZK.log.append((host, port, password))
    def connect(self): return self
    def disable_device(self): pass
    def enable_device(self): FakeZK.log.append("enabled")
    def disconnect(self): FakeZK.log.append("bye")
    def get_serialnumber(self): return "FIO616202022322344"
    def get_time(self): return datetime(2026, 10, 28, 8, 0)
    def get_users(self): return [1, 2]
    def get_attendance(self):
        a = lambda u, t: SimpleNamespace(user_id=u, timestamp=t)       # noqa: E731
        return [a("101", datetime(2026, 10, 27, 7, 58, 10)), a("101", datetime(2026, 10, 27, 16, 3, 44)), a("101", datetime(2026, 9, 1, 8, 0, 0))]


class DirectMachineTests(TestCase):
    def setUp(self):
        FakeZK.log = []; p = mock.patch.dict("sys.modules", {"zk": SimpleNamespace(ZK=FakeZK)}); p.start(); self.addCleanup(p.stop)
        self.user = User.objects.create_user("hrd", password="Demo#HRIS-2026-x", role=Role.HRD)

    def test_import_list_then_download(self):
        made, upd, warn = sy.import_machine_list(TABLE.encode(), "paste.txt")
        self.assertEqual((made, upd, len(warn)), (3, 0, 1))
        d = AttendanceDevice.objects.get(code="FIO616202022322344")
        self.assertEqual((d.name, d.machine_no, d.host, d.connection, d.screen, d.protocol, d.port, d.sync_enabled), ("A.PENDAFTARAN 1", "11", "192.168.10.36", "Ethernet", "TFT", "zk", 4370, True))
        self.assertFalse(AttendanceDevice.objects.get(code="616202022999999").sync_enabled)       # IP publik: tidak diaktifkan
        self.assertEqual(sy.import_machine_list(TABLE.encode(), "paste.txt")[:2], (0, 3))          # impor ulang = pembaruan
        d.comm_key = "7"; d.save()
        with mock.patch.object(sy, "check_host"):
            r = sy.sync_device(d, date(2026, 10, 1), date(2026, 10, 28))
        self.assertTrue(r["ok"]); self.assertEqual((r["fetched"], r["new"]), (2, 2))               # scan 1 Sep di luar rentang
        raw = AttendanceRaw.objects.get(punched_at__hour=7, punched_at__minute=58); self.assertEqual((raw.pin, raw.device_id), ("101", "FIO616202022322344"))
        self.assertIn(("192.168.10.36", 4370, 7), FakeZK.log); self.assertEqual(FakeZK.log[-2:], ["enabled", "bye"])   # mesin diaktifkan kembali & koneksi ditutup
        with mock.patch.object(sy, "check_host"): sy.sync_device(d, date(2026, 10, 1), date(2026, 10, 28))
        self.assertEqual(AttendanceRaw.objects.count(), 2)                                           # idempoten

    def test_import_list_bad_columns(self):
        with self.assertRaises(ValueError): sy.import_machine_list(b"a,b\n1,2\n", "x.csv")

    def test_probe_reads_serial(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5); self.addCleanup(s.close)
        d = AttendanceDevice.objects.create(code="X", name="X", serial="FIO616202022322344", host="127.0.0.1", port=s.getsockname()[1])
        r = sy.probe(d); self.assertTrue(r["ok"]); self.assertIn("SN mesin FIO616202022322344", r["note"]); self.assertNotIn("PERINGATAN", r["note"])
        d.serial = "LAIN"; self.assertIn("PERINGATAN", sy.probe(d)["note"])
        d.port = 1; r = sy.probe(d); self.assertFalse(r["ok"])

    def test_page_buttons(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("att_devices"), {"action": "import_list", "paste": TABLE}, follow=True); self.assertContains(r, "3 baru")
        r = self.client.get(reverse("att_devices")); self.assertContains(r, "A.PENDAFTARAN 1"); self.assertContains(r, "192.168.10.36:4370"); self.assertContains(r, "Download scanlog")
        ids = list(AttendanceDevice.objects.filter(code="FIO616202022322344").values_list("pk", flat=True))
        with mock.patch.object(sy, "check_host"):
            r = self.client.post(reverse("att_devices"), {"action": "sync", "ids": ids, "from": "2026-10-01", "to": "2026-10-28"}, follow=True)
        self.assertContains(r, "1 dari 1 mesin berhasil"); self.assertContains(r, "2 baru"); self.assertEqual(AttendanceRaw.objects.count(), 2)
        r = self.client.post(reverse("att_devices"), {"action": "sync"}, follow=True); self.assertContains(r, "Centang minimal")
        self.client.post(reverse("att_devices"), {"edit": "1", "code": "FIO616202022322344", "name": "Pendaftaran", "host": "192.168.10.99", "port": "5005", "comm_key": "12", "protocol": "zk", "sync_enabled": "1", "active": "1"})
        d = AttendanceDevice.objects.get(code="FIO616202022322344"); self.assertEqual((d.host, d.port, d.comm_key, d.sync_enabled), ("192.168.10.99", 5005, "12", True))
        self.client.post(reverse("att_devices"), {"edit": "1", "code": d.code, "name": "P", "active": "1"}); d.refresh_from_db(); self.assertEqual(d.comm_key, "12")      # kolom kunci dikosongkan = tidak diubah

    def test_real_pyzk_unreachable_is_clean_error(self):
        mock.patch.stopall(); d = AttendanceDevice.objects.create(code="Z", name="Z", host="127.0.0.1", port=1)
        with mock.patch.object(sy, "TIMEOUT", 2): r = sy.sync_device(d)
        self.assertFalse(r["ok"]); self.assertIn("tidak merespons", r["note"])


class ModeTests(DirectMachineTests):
    """Semua / terbaru / rentang + tidak menumpuk riwayat."""
    def _dev(self):
        sy.import_machine_list(TABLE.encode(), "paste.txt"); return AttendanceDevice.objects.get(code="FIO616202022322344")

    def test_all_then_latest_skips_everything_and_leaves_no_history(self):
        from apps.hr.models import AttendanceImport
        d = self._dev()
        with mock.patch.object(sy, "check_host"):
            self.assertIsNone(sy.last_stored(d))
            r = sy.sync_device(d, mode="all"); self.assertEqual((r["fetched"], r["new"]), (3, 3))             # seluruh log (termasuk 1 Sep)
            self.assertEqual(sy.last_stored(d).day, 27); n_imp = AttendanceImport.objects.count(); self.assertEqual(n_imp, 1)
            r = sy.sync_device(d, mode="latest"); self.assertTrue(r["ok"]); self.assertEqual(r["new"], 0)    # 1 Sep di luar jendela terbaru
            r = sy.sync_device(d, mode="all"); self.assertEqual((r["fetched"], r["new"], r["dup"]), (3, 0, 3))  # semua sudah ada → dilewati
        self.assertEqual(AttendanceRaw.objects.count(), 3); self.assertEqual(AttendanceImport.objects.count(), n_imp)   # tidak ada riwayat kosong menumpuk

    def test_latest_window_starts_at_last_stored_minus_overlap(self):
        d = self._dev()
        with mock.patch.object(sy, "check_host"): sy.sync_device(d, mode="all")
        a, b = sy.window(d, "latest", None, date(2026, 10, 28)); self.assertEqual(a, date(2026, 10, 26))
        self.assertEqual(sy.window(d, "all", None, date(2026, 10, 28))[0], sy.ALL_FROM)

    def test_new_machine_latest_means_everything(self):
        d = self._dev()
        self.assertEqual(sy.window(d, "latest")[0], sy.ALL_FROM)

    def test_page_mode_all(self):
        self.client.force_login(self.user); self._dev(); ids = list(AttendanceDevice.objects.values_list("pk", flat=True)[:1])
        d = AttendanceDevice.objects.get(pk=ids[0]); d.host = "192.168.10.36"; d.save()
        with mock.patch.object(sy, "check_host"):
            r = self.client.post(reverse("att_devices"), {"action": "sync", "ids": ids, "mode": "all"}, follow=True)
        self.assertContains(r, "3 baru"); self.assertContains(self.client.get(reverse("att_devices")), "Semua scanlog")
        r = self.client.post(reverse("att_devices"), {"action": "sync", "ids": ids, "mode": "range"}, follow=True); self.assertContains(r, "Isi tanggal")
