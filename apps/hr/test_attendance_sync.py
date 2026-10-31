import ctypes, json, threading
from unittest import mock
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
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

    def test_latest_is_last_24_hours_and_bridges_gaps(self):
        from datetime import datetime, timedelta
        d = self._dev(); now = timezone.localtime().replace(tzinfo=None)
        a, _ = sy.window(d, "latest"); self.assertEqual(a, (now - timedelta(hours=24)).date())              # mesin tanpa data: 24 jam saja
        self.assertLess(abs((sy.latest_since(d) - (now - timedelta(hours=24))).total_seconds()), 5)
        AttendanceRaw.objects.create(device_id=d.code, pin="101", punched_at=timezone.make_aware(now - timedelta(hours=5)))   # scan terakhir 5 jam lalu → tetap 24 jam
        self.assertLess(abs((sy.latest_since(d) - (now - timedelta(hours=24))).total_seconds()), 5)
        AttendanceRaw.objects.all().delete(); old = now - timedelta(days=3)
        AttendanceRaw.objects.create(device_id=d.code, pin="101", punched_at=timezone.make_aware(old))        # terakhir tersimpan 3 hari lalu → mundur ke sana (−1 jam): tanpa celah
        self.assertLess(abs((sy.latest_since(d) - (old - timedelta(hours=1))).total_seconds()), 5); self.assertEqual(sy.window(d, "latest")[0], (old - timedelta(hours=1)).date())
        self.assertEqual(sy.window(d, "all", None, date(2026, 10, 28))[0], sy.ALL_FROM)
        self.assertEqual(sy.window(d, "range", date(2026, 10, 1), date(2026, 10, 5)), (date(2026, 10, 1), date(2026, 10, 5)))   # rentang = tanggal apa adanya

    def test_latest_filters_by_exact_time_not_just_date(self):
        from datetime import timedelta
        d = self._dev(); now = timezone.localtime().replace(tzinfo=None); f = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")   # noqa: E731
        taps = [("101", f(now - timedelta(hours=30))), ("102", f(now - timedelta(hours=23))), ("103", f(now - timedelta(hours=1)))]
        with mock.patch.object(sy, "fetch_device", lambda dev, a, b: (list(taps), "")), mock.patch.object(sy, "check_host"):
            r = sy.sync_device(d, mode="latest")
        self.assertEqual((r["fetched"], r["new"]), (2, 2)); self.assertEqual(set(AttendanceRaw.objects.values_list("pin", flat=True)), {"102", "103"})   # 30 jam lalu tidak ikut

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


class FkReadLogsTests(TestCase):
    """read_logs memakai DLL palsu: jalur numerik, jalur ID teks (PIN 0 → muat ulang + StringID), dan penghitungan rekaman yang dibuang."""
    class Dll:
        def __init__(self, numeric, texts): self.numeric, self.texts, self.loads, self.i = list(numeric), list(texts), 0, 0; self.FK_GetGeneralLogData_StringID = lambda h, sbuf, *refs: self._str(h, sbuf, *refs)      # fungsi biasa agar boleh diberi .argtypes seperti fungsi DLL
        def FK_LoadGeneralLogData(self, h, m): self.loads += 1; self.i = 0; return 1
        def FK_GetLastError(self, h): return 0
        def _set(self, refs, vals):
            for r, x in zip(refs, vals): r._obj.value = x
        def FK_GetGeneralLogData_1(self, h, *refs):
            if self.i >= len(self.numeric): return 0
            self._set(refs, self.numeric[self.i]); self.i += 1; return 1
        def _str(self, h, sbuf, *refs):
            if self.i >= len(self.texts): return 0
            pin, rest = self.texts[self.i]; self._set(refs, rest); ctypes.memmove(sbuf, pin.encode(), len(pin)); self.i += 1; return 1

    def _run(self, dll):
        from apps.hr.fk import fk_dump
        sent = []
        with mock.patch.object(fk_dump, "_line", lambda d: sent.append(d)): st = fk_dump.read_logs(dll, 1)
        return st, [r for d in sent if "rows" in d and "ok" not in d for r in d["rows"]]

    def test_numeric_ids(self):
        st, rows = self._run(self.Dll([(101, 1, 0, 2026, 10, 28, 7, 1, 2), (0, 1, 0, 2026, 10, 28, 8, 0, 0), (102, 1, 0, 26, 13, 40, 8, 0, 0), (102, 1, 0, 26, 10, 28, 9, 0, 0)], []))
        self.assertEqual(rows, [["101", "2026-10-28 07:01:02"], ["102", "2026-10-28 09:00:00"]]); self.assertEqual((st["raw"], st["pin0"], st["baddate"], st["stringid"]), (4, 1, 1, False))

    def test_text_ids_switch_to_stringid_and_reload_log(self):
        dll = self.Dll([(0, 1, 0, 2026, 10, 28, 7, 0, 0)] * 3, [("A101", (1, 0, 2026, 10, 28, 7, 1, 2)), ("A102", (1, 0, 2026, 10, 28, 8, 0, 0))])
        st, rows = self._run(dll)
        self.assertEqual(rows, [["A101", "2026-10-28 07:01:02"], ["A102", "2026-10-28 08:00:00"]]); self.assertTrue(st["stringid"]); self.assertEqual(dll.loads, 1)


class FkArgcTests(TestCase):
    """Pembaca jumlah argumen stdcall dari tabel ekspor PE: diuji dengan DLL sintetis (dua fungsi: ret 8 dan ret 0x28; satu thunk jmp)."""
    def _pe(self):
        import struct
        b = bytearray(0x800); struct.pack_into("<H", b, 0, 0x5A4D); struct.pack_into("<I", b, 0x3C, 0x80); b[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", b, 0x84, 0x14C, 1); struct.pack_into("<H", b, 0x80 + 20, 0xE0); opt = 0x80 + 24; struct.pack_into("<H", b, opt, 0x10B); struct.pack_into("<I", b, opt + 96, 0x1000)
        sec = opt + 0xE0; struct.pack_into("<IIII", b, sec + 8, 0x400, 0x1000, 0x400, 0x400)                 # VirtualSize, VA, RawSize, RawPtr
        e = 0x400; struct.pack_into("<I", b, e + 24, 3); struct.pack_into("<III", b, e + 28, 0x1100, 0x1110, 0x1120)
        names = [b"FK_A", b"FK_B", b"FK_JMP"]; codes = [b"\x55\x8B\xEC\x5D\xC2\x08\x00\xCC\xCC", b"\x55\xC2\x28\x00\xCC", b"\xE9\x00\x00\x00\x00\xCC"]
        for i, (n, c) in enumerate(zip(names, codes)):
            struct.pack_into("<I", b, 0x500 + 4 * i, 0x1200 + 0x20 * i); struct.pack_into("<I", b, 0x510 + 4 * i, 0x1130 + 0x10 * i); struct.pack_into("<H", b, 0x520 + 2 * i, i)
            b[0x530 + 0x10 * i:0x530 + 0x10 * i + len(n)] = n; b[0x600 + 0x20 * i:0x600 + 0x20 * i + len(c)] = c
        return bytes(b)

    def test_argc_from_ret_operand(self):
        import tempfile, os
        from apps.hr.fk import fk_dump
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".dll"); f.write(self._pe()); f.close(); self.addCleanup(os.remove, f.name)
        self.assertEqual((fk_dump.argc(f.name, "FK_A"), fk_dump.argc(f.name, "FK_B"), fk_dump.argc(f.name, "FK_JMP"), fk_dump.argc(f.name, "TIDAK_ADA")), (2, 10, None, None))


class FkDriverTests(TestCase):
    """Driver FK (FKAttend.dll lewat proses terpisah): subprocess dipalsukan; jalur nyata hanya bisa diuji di Windows + mesin."""
    def _dev(self, **kw):
        return AttendanceDevice.objects.create(code="FK1", name="FK", host="192.168.11.40", port=5005, protocol="fk", machine_no="12", **kw)

    def _fake(self, lines, seen=None, rc=0, delay=0):
        def _exec(inst, cmd, on_line, limit):
            if seen is not None: seen["cmd"] = cmd; seen["limit"] = limit
            for ln in lines: on_line(json.dumps(ln) + "\n")
            return rc, ""
        return mock.patch.object(sy._Fk, "_exec", _exec)

    def test_fetch_filters_range_and_passes_machine_no(self):
        seen = {}
        rows = [["101", "2026-10-28 07:01:02"], ["0", "2026-10-28 07:02:00"], ["102", "2026-09-01 07:00:00"]]
        with self._fake([{"p": 15, "stage": "load"}, {"ok": True, "rows": rows}], seen):
            taps, err = sy.fetch_device(self._dev(), date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual((taps, err), ([("101", "2026-10-28 07:01:02")], ""))
        self.assertEqual(seen["cmd"][seen["cmd"].index("--machine") + 1], "12"); self.assertGreaterEqual(seen["limit"], 600)   # batas baca jauh di atas 120 dtk lama

    def test_error_from_reader_is_clean(self):
        with self._fake([{"ok": False, "error": "FK_ConnectNet gagal"}], rc=1):
            taps, err = sy.fetch_device(self._dev(), date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual(taps, []); self.assertIn("FK_ConnectNet gagal", err)

    def test_timeout_reports_stage_where_it_hung(self):
        import time
        with mock.patch.object(sy.settings, "ATTENDANCE_FK_TIMEOUT", 0, create=True), self._fake([{"p": 15, "stage": "load", "n": 0}, {"p": 40, "stage": "read", "n": 4000}]):
            taps, err = sy.fetch_device(self._dev(), date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual(taps, []); self.assertIn("tahap 'read'", err); self.assertIn("4000 scan", err); self.assertIn("ATTENDANCE_FK_TIMEOUT", err)

    def test_progress_published_for_job(self):
        from django.core.cache import cache
        d = self._dev(); cache.clear(); seen = []
        orig = sy.set_progress
        with self._fake([{"p": 15, "stage": "load"}, {"p": 60, "stage": "read", "n": 4000}, {"ok": True, "rows": [["101", "2026-10-28 07:01:02"]]}]), \
                mock.patch.object(sy, "set_progress", lambda pct, note="": (seen.append(pct), orig(pct, note))), mock.patch.object(sy, "check_host"):
            r = sy.run_action(d, "sync", mode="all", job="abc-123")
        self.assertTrue(r["ok"]); self.assertEqual(sy.get_progress("abc-123", d.pk)["pct"], 100); self.assertEqual(seen, sorted(seen))   # naik terus, berakhir 100
        self.assertIn(88, seen); self.assertTrue(any(0 < x < 85 for x in seen))

    def test_zero_scans_are_explained_not_reported_ok(self):
        """Dulu 'OK, 0 scan' tanpa alasan. Kini: DLL tidak mengembalikan apa pun / semua PIN 0 / tanggal rusak → galat yang menjelaskan + contoh rekaman mentah."""
        d = self._dev(); win = (date(2026, 10, 1), date(2026, 10, 30))
        with self._fake([{"ok": True, "count": 0, "raw": 0}]): taps, err = sy.fetch_device(d, *win)
        self.assertEqual(taps, []); self.assertIn("tidak mengembalikan satu pun scan", err)
        with self._fake([{"ok": True, "count": 0, "raw": 500, "pin0": 500, "baddate": 0, "stringid": False, "sample": [0, 1, 0, 2026, 10, 28, 7, 0, 0]}]): taps, err = sy.fetch_device(d, *win)
        self.assertEqual(taps, []); self.assertIn("500 scan", err); self.assertIn("PIN 0", err); self.assertIn("[0, 1, 0, 2026, 10, 28, 7, 0, 0]", err)
        rows = [["101", "2026-09-01 07:00:00"]]                              # ada data terpakai tetapi di luar jendela → bukan galat (memang tidak ada yang baru)
        with self._fake([{"ok": True, "count": 1, "raw": 1, "rows": rows}]): taps, err = sy.fetch_device(d, *win)
        self.assertEqual((taps, err), ([], ""))


    def test_bydate_used_when_enabled_and_falls_back_to_full_read(self):
        d = self._dev(); win = (date(2026, 10, 30), date(2026, 10, 31)); calls = []
        def run(lines):
            def _exec(inst, cmd, on_line, limit):
                calls.append(cmd)
                for ln in lines(cmd): on_line(json.dumps(ln) + "\n")
                return 0, ""
            return mock.patch.object(sy._Fk, "_exec", _exec)
        row = ["101", "2026-10-31 07:00:00"]
        with mock.patch.object(sy.settings, "ATTENDANCE_FK_BYDATE", "i4", create=True):
            with run(lambda c: [{"ok": True, "rows": [row], "raw": 1}]): taps, err = sy.fetch_device(d, *win)       # ByDate berhasil → satu panggilan
            self.assertEqual((taps, err, len(calls)), ([("101", row[1])], "", 1)); self.assertEqual(calls[0][calls[0].index("--bydate") + 1], "i4"); self.assertEqual(calls[0][calls[0].index("--from") + 1], "2026-10-30")
            calls.clear()                                                                                    # ByDate gagal → otomatis pembacaan penuh
            with run(lambda c: [{"ok": False, "error": "FK_LoadGeneralLogDataByDate gagal"}] if "--bydate" in c else [{"ok": True, "rows": [row], "raw": 1}]): taps, err = sy.fetch_device(d, *win)
            self.assertEqual((taps, err, len(calls)), ([("101", row[1])], "", 2)); self.assertNotIn("--bydate", calls[1])
            calls.clear()                                                                                    # ByDate kosong → dipastikan dengan pembacaan penuh (tidak ada data terlewat)
            with run(lambda c: [{"ok": True, "rows": [], "raw": 0}] if "--bydate" in c else [{"ok": True, "rows": [row], "raw": 1}]): taps, err = sy.fetch_device(d, *win)
            self.assertEqual((len(taps), len(calls)), (1, 2))
            calls.clear(); taps, err = None, None                                                            # tarikan 'Semua' (tanpa batas awal) selalu penuh
            with run(lambda c: [{"ok": True, "rows": [row], "raw": 1}]): sy.fetch_device(d, sy.ALL_FROM, win[1])
            self.assertEqual(len(calls), 1); self.assertNotIn("--bydate", calls[0])

    def test_users_download_via_fk(self):
        """Download user mesin FK: pembaca dipalsukan; user + jari + password + kartu tersimpan di HRIS, idempoten, jari ditandai tanpa versi algoritma."""
        import base64
        from apps.hr.models import DeviceFingerprint, DeviceUser
        d = self._dev(); seen = {}
        users = [{"pin": "101", "name": "Budi", "priv": 0, "password": "1234", "card": 777, "fps": [[0, base64.b64encode(b"TPL-FK-101-0").decode()], [1, base64.b64encode(b"TPL-FK-101-1").decode()]]},
                 {"pin": "102", "name": "Sari", "priv": 1, "password": "", "card": 0, "fps": []}]
        with mock.patch.object(sy, "check_host"), self._fake([{"p": 30, "stage": "read", "n": 1}, {"users": users}, {"ok": True, "count": 2, "fps": 2}], seen):
            r = sy.download_users(d); r2 = sy.download_users(d)
        self.assertIn("--users-down", seen["cmd"]); self.assertTrue(r["ok"], r["note"]); self.assertEqual((r["users"], r["new"], r["updated"], r["fingers"]), (2, 2, 0, 2))
        self.assertEqual((r2["new"], r2["updated"]), (0, 0)); b = DeviceUser.objects.get(pin="101")
        self.assertEqual((b.name, b.password, b.card, b.source_device), ("Budi", "1234", 777, "FK1")); self.assertEqual(DeviceFingerprint.objects.filter(user=b).count(), 2)
        self.assertEqual(DeviceFingerprint.objects.filter(user=b).first().fp_version, 0)

    def test_users_upload_via_fk_filters_zk_templates_and_cleans_temp_file(self):
        """Upload user ke mesin FK: hanya jari dari mesin FK yang dikirim (template ZK ≠ FK); berkas sementara berisi template dihapus; PIN non-angka dilaporkan gagal oleh pembaca."""
        import base64, os
        from apps.hr.models import DeviceFingerprint, DeviceUser
        d = self._dev(); AttendanceDevice.objects.create(code="ZK1", name="ZK", host="192.168.11.41", port=4370, protocol="zk")
        u = DeviceUser.objects.create(pin="101", name="Budi", privilege=14, card=5)
        DeviceFingerprint.objects.create(user=u, finger=0, template=base64.b64encode(b"FKTPL").decode(), source_device="FK1")
        DeviceFingerprint.objects.create(user=u, finger=1, template=base64.b64encode(b"ZKTPL").decode(), source_device="ZK1")
        seen = {}; sent_json = {}
        def _exec(inst, cmd, on_line, limit):
            path = cmd[cmd.index("--users-up") + 1]; sent_json.update(path=path, data=json.load(open(path, encoding="utf-8")))
            on_line(json.dumps({"ok": True, "sent": 1, "fps": 1, "bad": []}) + "\n"); return 0, ""
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy._Fk, "_exec", _exec):
            r = sy.upload_users(d)
        self.assertTrue(r["ok"], r["note"]); self.assertEqual((r["users"], r["fingers"], r["skipped"]), (1, 1, 1)); self.assertIn("dilewati", r["note"])
        item = sent_json["data"][0]; self.assertEqual(item["priv"], 1); self.assertEqual([f[0] for f in item["fps"]], [0])        # privilege ZK 14 → admin FK 1; hanya jari FK
        self.assertFalse(os.path.exists(sent_json["path"]))

    def test_exec_streams_lines_and_kills_at_limit(self):
        import sys, time
        slow = "print('{\"p\": 5}'); import time; time.sleep(30)"; fast = "print('{\"ok\": true}')"
        got = []; t = time.time(); fk = sy._Fk(self._dev())
        rc, _ = fk._exec([sys.executable, "-u", "-c", slow], got.append, 1)
        self.assertLess(time.time() - t, 10); self.assertEqual(got, ['{"p": 5}\n']); self.assertNotEqual(rc, 0)      # baris terbaca & proses dibunuh setelah batas
        got.clear(); rc, _ = fk._exec([sys.executable, "-c", fast], got.append, 30); self.assertEqual((rc, got), (0, ['{"ok": true}\n']))


class DevicesPopupScriptTests(TestCase):
    """Regresi: skrip popup menu Mesin memakai bar progres per mesin (`pg`); bila tidak dibuat, SEMUA tombol (scanlog, user, tes koneksi) mati karena ReferenceError."""
    def test_popup_script_creates_row_progress_bar(self):
        u = User.objects.create_superuser("adm", "a@x.id", "pw"); self.client.force_login(u)
        html = self.client.get(reverse("att_devices")).content.decode()
        self.assertIn("pg=document.createElement('progress')", html)
        for act in ("sync", "users_down", "users_up", "test"): self.assertIn(f'name="action" value="{act}"', html)


class ProgressEndpointTests(TestCase):
    def test_endpoint_requires_login_and_validates(self):
        from django.contrib.auth import get_user_model
        from django.core.cache import cache
        url = reverse("att_device_progress"); self.assertEqual(self.client.get(url, {"job": "a1", "id": "1"}).status_code, 302)
        u = get_user_model().objects.create_superuser("p", "p@x.id", "pw"); self.client.force_login(u)
        self.assertEqual(self.client.get(url, {"job": "../x", "id": "1"}).status_code, 400)
        cache.set(sy.progress_key("a1", 1), {"pct": 42, "note": "Membaca scan"}); j = self.client.get(url, {"job": "a1", "id": "1"}).json()
        self.assertEqual((j["pct"], j["note"]), (42, "Membaca scan")); self.assertEqual(self.client.get(url, {"job": "zz", "id": "1"}).json()["pct"], 0)


class BigLogTests(ModeTests):
    """Log scanlog besar: dipecah per bagian agar tidak menabrak batas baris impor; hasil FK terpotong ditolak."""
    def _taps(self, n):
        from datetime import datetime, timedelta
        t0 = datetime(2026, 10, 1, 6, 0, 0)
        return [(str(100 + i % 7), (t0 + timedelta(minutes=3 * i)).strftime("%Y-%m-%d %H:%M:%S")) for i in range(n)]

    def test_log_over_import_row_limit_is_split_not_rejected(self):
        from apps.hr import attendance_import as imp
        d = self._dev(); taps = self._taps(95)
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy, "fetch_device", lambda *a: (taps, "")), mock.patch.object(imp, "MAX_ROWS", 30), mock.patch.object(sy, "CHUNK", 25):
            r = sy.sync_device(d, mode="all")
        self.assertTrue(r["ok"], r["note"]); self.assertEqual((r["fetched"], r["new"], r["dup"]), (95, 95, 0)); self.assertEqual(AttendanceRaw.objects.count(), 95)
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy, "fetch_device", lambda *a: (taps, "")), mock.patch.object(imp, "MAX_ROWS", 30), mock.patch.object(sy, "CHUNK", 25):
            r = sy.sync_device(d, mode="all")                                                       # unduh ulang: semua dilewati, tak ada data ganda
        self.assertEqual((r["new"], r["dup"]), (0, 95)); self.assertEqual(AttendanceRaw.objects.count(), 95)

    def test_single_chunk_unchanged_and_progress_goes_up(self):
        d = self._dev(); seen = []
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy, "fetch_device", lambda *a: (self._taps(10), "")), mock.patch.object(sy, "CHUNK", 4), \
                mock.patch.object(sy, "set_progress", lambda pct, note="": seen.append((round(pct), note))):
            r = sy.sync_device(d, mode="all")
        self.assertEqual(r["new"], 10); self.assertTrue(any("bagian 3 dari 3" in n for _, n in seen)); self.assertEqual([p for p, _ in seen], sorted(p for p, _ in seen))

    def test_fk_rows_streamed_in_batches_and_truncated_is_rejected(self):
        fk = self._fk(); rows = [["101", "2026-10-28 07:01:02"], ["102", "2026-10-28 07:02:02"]]
        with FkDriverTests._fake(self, [{"p": 40, "stage": "read", "n": 1}, {"rows": rows[:1]}, {"rows": rows[1:]}, {"ok": True, "count": 2, "truncated": False}]):
            self.assertEqual(sy.fetch_device(fk, date(2026, 10, 1), date(2026, 10, 30)), ([("101", "2026-10-28 07:01:02"), ("102", "2026-10-28 07:02:02")], ""))
        with FkDriverTests._fake(self, [{"rows": rows}, {"ok": True, "count": 2, "truncated": True}]):
            taps, err = sy.fetch_device(fk, date(2026, 10, 1), date(2026, 10, 30))
        self.assertEqual(taps, []); self.assertIn("terpotong", err)

    def _fk(self): return AttendanceDevice.objects.create(code="FKB", name="FK", host="192.168.11.40", port=5005, protocol="fk", machine_no="12")


class NoDoubleDataTests(TestCase):
    """Jaminan tanpa data ganda: mesin kembar diblokir, format PIN berbeda tidak menggandakan, unduh ulang/tumpang tindih aman."""
    def _d(self, code, host="192.168.11.40", port=5005, proto="fk", **kw): return AttendanceDevice.objects.create(code=code, name=f"M-{code}", host=host, port=port, protocol=proto, sync_enabled=True, **kw)

    def test_same_machine_registered_twice_is_blocked_older_still_syncs(self):
        old, new = self._d("A1"), self._d("B2"); taps = [("101", "2026-10-28 07:01:02")]; calls = []
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy, "fetch_device", lambda d, *a: (calls.append(d.code), (taps, ""))[1]):
            self.assertTrue(sy.sync_device(old, mode="all")["ok"])
            r = sy.sync_device(new, mode="all")
            self.assertFalse(r["ok"]); self.assertIn("terdaftar dua kali", r["note"]); self.assertEqual(calls, ["A1"])       # jaringan mesin kembar tidak disentuh
            old.active = False; old.save(); self.assertTrue(sy.sync_device(new, mode="all")["ok"])                          # yang lama dinonaktifkan → boleh
        self.assertEqual(sorted(AttendanceRaw.objects.values_list("device_id", flat=True)), ["A1", "B2"])                   # (dua kode hanya bila pengguna sendiri mengizinkan)

    def test_sync_many_skips_twin_without_network(self):
        old, new = self._d("A1"), self._d("B2"); calls = []
        with mock.patch.object(sy, "check_host"), mock.patch.object(sy, "fetch_device", lambda d, *a: (calls.append(d.code), ([("101", "2026-10-28 07:01:02")], ""))[1]):
            res = sy.sync_many([old, new], mode="all")
        self.assertEqual(calls, ["A1"]); self.assertEqual([r["ok"] for r in res], [True, False]); self.assertEqual(AttendanceRaw.objects.count(), 1)

    def test_different_ip_or_easylink_shared_server_not_blocked(self):
        a = self._d("A1"); b = self._d("B2", host="192.168.11.41"); e1 = self._d("E1", host="srv", port=8080, proto="easylink"); e2 = self._d("E2", host="srv", port=8080, proto="easylink")
        self.assertEqual([sy.alias_conflict(x) for x in (a, b, e1, e2)], ["", "", "", ""])

    def test_machine_list_import_warns_and_disables_twin(self):
        self._d("A1", host="192.168.11.40", port=4370, proto="zk")
        table = "SN\tIP Address / URL\tNama\nB2\t192.168.11.40\tDouble\n"
        made, upd, warn = sy.import_machine_list(table.encode(), "paste.txt")
        twin = AttendanceDevice.objects.get(code="B2"); self.assertFalse(twin.sync_enabled); self.assertTrue(any("terdaftar dua kali" in w for w in warn))

    def test_pin_format_variants_and_overlapping_windows_never_double(self):
        d = self._d("A1"); t1 = [("101", "2026-10-28 07:01:02"), ("102", "2026-10-28 07:02:02")]; t2 = [("0101", "2026-10-28 07:01:02"), ("102", "2026-10-28 07:02:02"), ("101", "2026-10-29 07:00:00")]
        with mock.patch.object(sy, "check_host"):
            with mock.patch.object(sy, "fetch_device", lambda *a: (t1, "")): self.assertEqual(sy.sync_device(d, mode="all")["new"], 2)
            with mock.patch.object(sy, "fetch_device", lambda *a: (t2, "")): r = sy.sync_device(d, mode="all")
        self.assertEqual((r["new"], r["dup"]), (1, 2)); self.assertEqual(AttendanceRaw.objects.count(), 3)                  # '0101' = '101'; hanya 29 Okt yang baru

    def test_audit_command_reports_twin_machines_and_cross_code_scans(self):
        from io import StringIO
        from datetime import datetime
        from django.utils import timezone
        a, b = self._d("A1"), self._d("B2")
        for dev in ("A1", "B2"): AttendanceRaw.objects.create(pin="101", device_id=dev, punched_at=timezone.make_aware(datetime(2026, 10, 28, 7, 1, 2)))
        out = StringIO(); call_command("audit_attendance_dupes", stdout=out); t = out.getvalue()
        self.assertIn("MESIN KEMBAR 192.168.11.40:5005", t); self.assertIn("1 scan tercatat di lebih dari satu kode", t)


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
