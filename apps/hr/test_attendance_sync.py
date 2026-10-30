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


# ------------------------------------------------------------------ Download user / Upload user (+ sidik jari) & popup progres
import base64
from types import SimpleNamespace as NS
from apps.hr.models import DeviceFingerprint, DeviceUser


class FUser:
    def __init__(self, uid, name, privilege, password="", group_id="", user_id="", card=0):
        self.uid, self.name, self.privilege, self.password, self.group_id, self.user_id, self.card = uid, name, privilege, password, group_id, user_id, card


class FFinger:
    def __init__(self, uid, fid, valid, template): self.uid, self.fid, self.valid, self.template = uid, fid, valid, template


class UserZK:
    """Mesin palsu dengan user & template; state di level kelas supaya bisa diperiksa tes."""
    users, templates, ver, calls = [], [], 10, []
    def __init__(self, host, port=4370, timeout=5, password=0, ommit_ping=False): pass
    def connect(self): return self
    def disable_device(self): pass
    def enable_device(self): pass
    def disconnect(self): pass
    def refresh_data(self): UserZK.calls.append(("refresh",))
    def get_users(self): return list(UserZK.users)
    def get_templates(self): return list(UserZK.templates)
    def get_fp_version(self): return UserZK.ver
    def save_user_template(self, user, fingers): UserZK.calls.append(("save", user.uid, user.user_id, [(f.uid, f.fid, bytes(f.template)) for f in fingers]))
    def set_user(self, **kw): UserZK.calls.append(("set", kw["uid"], kw["user_id"], kw["name"]))


class UserSyncTests(TestCase):
    def setUp(self):
        UserZK.users = [FUser(1, "Budi\x00", 0, "1234", "1", "101", 777), FUser(2, "Sari", 14, "", "", "102", 0), FUser(3, "Tanpa Jari", 0, "", "", "103", 0)]
        UserZK.templates = [FFinger(1, 0, 1, b"TPL-BUDI-0"), FFinger(1, 6, 1, b"TPL-BUDI-6"), FFinger(2, 1, 1, b"TPL-SARI-1")]; UserZK.ver = 10; UserZK.calls = []
        p = mock.patch.dict("sys.modules", {"zk": NS(ZK=UserZK), "zk.base": NS(User=FUser), "zk.finger": NS(Finger=FFinger)}); p.start(); self.addCleanup(p.stop)
        c = mock.patch.object(sy, "check_host"); c.start(); self.addCleanup(c.stop)
        self.dev = AttendanceDevice.objects.create(code="M1", name="Mesin 1", host="192.168.10.1", protocol="zk")
        self.dev2 = AttendanceDevice.objects.create(code="M2", name="Mesin 2", host="192.168.10.2", protocol="zk")
        self.user = User.objects.create_user("hrd", password="Demo#HRIS-2026-x", role=Role.HRD)

    def test_download_stores_users_and_fingers_idempotent(self):
        r = sy.download_users(self.dev)
        self.assertTrue(r["ok"]); self.assertEqual((r["users"], r["new"], r["updated"], r["fingers"]), (3, 3, 0, 3))
        b = DeviceUser.objects.get(pin="101"); self.assertEqual((b.name, b.card, b.password, b.source_device), ("Budi", 777, "1234", "M1"))
        self.assertEqual(DeviceUser.objects.get(pin="102").privilege, 14); self.assertEqual(DeviceFingerprint.objects.filter(user=b).count(), 2)
        self.assertEqual(DeviceFingerprint.objects.get(user=b, finger=6).fp_version, 10)
        r = sy.download_users(self.dev); self.assertEqual((r["new"], r["updated"], r["fingers"]), (0, 0, 3)); self.assertEqual(DeviceFingerprint.objects.count(), 3)
        UserZK.users[0].name = "Budi S"; UserZK.templates[0].template = b"TPL-BUDI-0-NEW"
        r = sy.download_users(self.dev); self.assertEqual(r["updated"], 1)
        self.assertEqual(base64.b64decode(DeviceFingerprint.objects.get(user=b, finger=0).template), b"TPL-BUDI-0-NEW")

    def test_biometrics_and_password_encrypted_at_rest(self):
        from django.db import connection
        sy.download_users(self.dev)
        with connection.cursor() as c:
            c.execute("select template from hr_devicefingerprint"); self.assertTrue(all(row[0].startswith("enc1:") for row in c.fetchall()))
            c.execute("select password from hr_deviceuser where pin='101'"); self.assertTrue(c.fetchone()[0].startswith("enc1:"))

    def test_upload_maps_uid_keeps_existing_and_skips_wrong_version(self):
        sy.download_users(self.dev)
        UserZK.users = [FUser(5, "Budi lama", 0, "", "", "101", 0), FUser(1, "Lain", 0, "", "", "999", 0)]      # mesin tujuan: 101 sudah ada (uid 5), 999 milik orang lain
        r = sy.upload_users(self.dev2); self.assertTrue(r["ok"]); self.assertEqual((r["users"], r["fingers"], r["failed"], r["skipped"]), (3, 3, 0, 0))
        saves = {c[2]: c for c in UserZK.calls if c[0] == "save"}; sets = [c for c in UserZK.calls if c[0] == "set"]
        self.assertEqual(saves["101"][1], 5); self.assertEqual(sorted(f[:2] for f in saves["101"][3]), [(5, 0), (5, 6)])      # uid mesin tujuan dipakai, bukan uid sumber
        self.assertEqual(saves["102"][1], 2); self.assertEqual(sets[0][1:3], (3, "103")); self.assertIn(("refresh",), UserZK.calls)   # uid 1 dipakai 999 → uid kosong berikutnya
        UserZK.calls = []; UserZK.ver = 9
        r = sy.upload_users(self.dev2); self.assertEqual((r["users"], r["fingers"], r["skipped"]), (3, 0, 3)); self.assertIn("dilewati", r["note"])
        self.assertFalse([c for c in UserZK.calls if c[0] == "save"])                                                               # tanpa jari: hanya user yang dikirim
        UserZK.calls = []; UserZK.ver = 10
        r = sy.upload_users(self.dev2, pins=["102"], fingerprints=False); self.assertEqual((r["users"], r["fingers"]), (1, 0))
        self.assertEqual([c[2] for c in UserZK.calls if c[0] == "set"], ["102"]); self.assertFalse([c for c in UserZK.calls if c[0] == "save"])

    def test_upload_without_stored_users_and_wrong_protocol(self):
        r = sy.upload_users(self.dev); self.assertFalse(r["ok"]); self.assertIn("Download user", r["note"])
        self.dev.protocol = "easylink"; self.assertFalse(sy.download_users(self.dev)["ok"]); self.assertIn("EasyLink", sy.download_users(self.dev)["note"])

    def test_job_endpoint_json_never_leaks_templates(self):
        url = reverse("att_device_job")
        self.assertEqual(self.client.post(url, {"action": "users_down", "id": self.dev.pk}).status_code, 302)            # wajib login
        self.client.force_login(self.user)
        r = self.client.post(url, {"action": "users_down", "id": self.dev.pk}); j = r.json()
        self.assertEqual((r.status_code, j["ok"], j["users"], j["fingers"], j["name"]), (200, True, 3, 3, "Mesin 1")); self.assertNotIn("TPL", r.content.decode()); self.assertNotIn("VFBM", r.content.decode())
        j = self.client.post(url, {"action": "users_up", "id": self.dev2.pk, "pins": "101,102", "fingerprints": "1"}).json(); self.assertEqual((j["ok"], j["users"], j["fingers"]), (True, 2, 3))
        self.assertEqual(self.client.post(url, {"action": "hapus", "id": self.dev.pk}).status_code, 400)
        self.assertEqual(self.client.post(url, {"action": "sync", "id": 99999}).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "sync", "id": self.dev.pk, "mode": "range"}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        from apps.core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="attendance_device_users_down").exists())

    def test_page_has_popup_and_fallback_post(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("att_devices")); [self.assertContains(r, t) for t in ("Download user", "Upload user", 'id="jobdlg"', reverse("att_device_job"))]
        r = self.client.post(reverse("att_devices"), {"action": "users_down", "ids": [self.dev.pk, self.dev2.pk]}, follow=True)
        self.assertContains(r, "Download user: 2 dari 2 mesin berhasil"); self.assertEqual(DeviceUser.objects.count(), 3)


class ImportMesinCommandTests(TestCase):
    def test_bundled_list_loads_idempotently(self):
        from io import StringIO
        call_command("import_mesin", stdout=StringIO())
        self.assertEqual(AttendanceDevice.objects.count(), 32); self.assertEqual(AttendanceDevice.objects.filter(sync_enabled=True).count(), 32)
        d = AttendanceDevice.objects.get(code="FIO616202022322344"); self.assertEqual((d.name, d.host, d.machine_no, d.protocol, d.port), ("POLISI", "192.168.10.36", "11", "fk", 5005))
        out = StringIO(); call_command("import_mesin", stdout=out); self.assertEqual(AttendanceDevice.objects.count(), 32); self.assertIn("0 baru, 32 diperbarui", out.getvalue())

    def test_no_sync_enable_and_bad_file(self):
        from io import StringIO
        call_command("import_mesin", "--no-sync-enable", stdout=StringIO()); self.assertEqual(AttendanceDevice.objects.filter(sync_enabled=True).count(), 0)
        with self.assertRaises(CommandError): call_command("import_mesin", "/tidak/ada.xls")


class ProbeCommandTests(TestCase):
    def test_probe_covers_machines_without_auto_pull(self):
        from io import StringIO
        AttendanceDevice.objects.create(code="P1", name="P1", host="127.0.0.1", port=1, sync_enabled=False)
        out, err = StringIO(), StringIO()
        with self.assertRaises(CommandError): call_command("sync_attendance", "--probe", stdout=out, stderr=err)     # port tertutup → gagal, tapi mesin ikut dites
        self.assertIn("P1", err.getvalue())
        with self.assertRaises(CommandError): call_command("sync_attendance")                                          # tarikan sungguhan tetap hanya 'Tarik otomatis'


class ApplyPortsTests(TestCase):
    def test_apply_ports_switches_to_the_open_port(self):
        from io import StringIO
        s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(5); self.addCleanup(s.close); port = s.getsockname()[1]
        d = AttendanceDevice.objects.create(code="A", name="A", host="127.0.0.1", port=1, protocol="easylink")
        orig = sy.probe
        with mock.patch.object(sy, "probe", lambda dev: orig(dev, ports=(port,))):
            with self.assertRaises(CommandError): call_command("sync_attendance", "--probe", "--apply-ports", stdout=StringIO(), stderr=StringIO())
        d.refresh_from_db(); self.assertEqual(d.port, port)
        d.port = 1; d.save()
        with mock.patch.object(sy, "probe", lambda dev: orig(dev, ports=(port,))):
            with self.assertRaises(CommandError): call_command("sync_attendance", "--probe", stdout=StringIO(), stderr=StringIO())
        d.refresh_from_db(); self.assertEqual(d.port, 1)                    # tanpa --apply-ports: tidak mengubah apa pun


class FkDriverTests(TestCase):
    """Driver FK (FKAttend.dll lewat proses terpisah): subprocess dipalsukan; jalur nyata hanya bisa diuji di Windows + mesin."""
    def _dev(self, **kw):
        return AttendanceDevice.objects.create(code="FK1", name="FK", host="192.168.11.40", port=5005, protocol="fk", machine_no="12", **kw)

    def test_fetch_filters_range_and_passes_machine_no(self):
        import json, subprocess
        from unittest import mock
        seen = {}
        def run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": True, "rows": [["101", "2026-10-28 07:01:02"], ["0", "2026-10-28 07:02:00"], ["102", "2026-09-01 07:00:00"]]}), "")
        with mock.patch("subprocess.run", run):
            taps, err = sy.fetch_device(self._dev(), date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual((taps, err), ([("101", "2026-10-28 07:01:02")], ""))
        self.assertEqual(seen["cmd"][seen["cmd"].index("--machine") + 1], "12")

    def test_error_from_reader_is_clean(self):
        import json, subprocess
        from unittest import mock
        run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, json.dumps({"ok": False, "error": "FK_ConnectNet gagal"}), "")   # noqa: E731
        with mock.patch("subprocess.run", run):
            taps, err = sy.fetch_device(self._dev(), date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual(taps, []); self.assertIn("FK_ConnectNet gagal", err)


class ProtocolReimportTests(TestCase):
    def test_import_mesin_switches_existing_devices_to_fk(self):
        AttendanceDevice.objects.create(code="FIO616202022322344", name="POLISI", host="192.168.10.36", protocol="zk", port=5005)
        call_command("import_mesin", verbosity=0)
        self.assertEqual(AttendanceDevice.objects.get(code="FIO616202022322344").protocol, "fk")


class DevicesPageLayoutTests(TestCase):
    def test_footer_stays_inside_main_column(self):
        """Regresi: </div> berlebih di halaman Mesin pernah menutup .col lebih awal → footer jatuh ke samping konten & halaman menyempit."""
        from django.contrib.auth import get_user_model
        from html.parser import HTMLParser
        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@a.a", "pw"))
        class P(HTMLParser):
            def __init__(s): super().__init__(); s.st = []; s.foot = None
            def handle_starttag(s, t, a):
                if t in ("meta", "link", "br", "img", "input", "hr", "use", "path", "source", "col"): return
                if t == "footer": s.foot = [c for _, c in s.st]
                s.st.append((t, dict(a).get("class") or ""))
            def handle_endtag(s, t):
                for i in range(len(s.st) - 1, -1, -1):
                    if s.st[i][0] == t: del s.st[i:]; return
        p = P(); p.feed(self.client.get("/absensi/mesin/").content.decode())
        self.assertIn("col", p.foot or [])
