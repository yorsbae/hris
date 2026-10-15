"""Pemantauan: /health/, /healthz, check_health (alarm anti-banjir), send_alert, dan scripts/backup.sh (dijalankan sungguhan dengan pg_dump palsu)."""
import json
import logging
import os
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone as dt_tz
from io import StringIO
from pathlib import Path
from unittest import mock
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from apps.core import alerts, health

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"


def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TmpMixin:
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup); self.tmp = Path(self._tmp.name)
        self.status = self.tmp / "status.json"

    def write_status(self, ok=True, age_h=1, error=""):
        self.status.write_text(json.dumps({"ok": ok, "finished_at": iso(datetime.now(dt_tz.utc) - timedelta(hours=age_h)), "file": "hris_x.dump", "error": error}))


class HealthEndpointTests(TmpMixin, TestCase):
    def get(self, **kw):
        with override_settings(BACKUP_STATUS_FILE=str(self.status), **kw): return self.client.get("/health/")

    def test_ok_without_login_and_not_cached(self):
        self.write_status(); r = self.get(FIELD_ENCRYPTION_KEY="x" * 10)
        self.assertEqual(r.status_code, 200); d = r.json(); self.assertEqual(d["status"], "ok")
        self.assertEqual(set(d["checks"]), {"database", "migrasi", "cache", "disk", "kunci_enkripsi", "backup"}); self.assertTrue(all(c["ok"] for c in d["checks"].values()))
        self.assertIn("no-cache", r["Cache-Control"])

    def test_no_secrets_or_paths_in_output(self):
        self.write_status(); body = self.get(FIELD_ENCRYPTION_KEY="rahasia-kunci-123").content.decode()
        for needle in ("rahasia-kunci-123", str(self.tmp), "hris_x.dump", "/root", "Traceback", settings.DATABASES["default"]["NAME"] if "sqlite" not in settings.DATABASES["default"]["ENGINE"] else "zzz"):
            self.assertNotIn(needle, body)

    def test_missing_encryption_key_fails(self):
        self.write_status(); r = self.get(FIELD_ENCRYPTION_KEY=""); self.assertEqual(r.status_code, 503); self.assertFalse(r.json()["checks"]["kunci_enkripsi"]["ok"])

    def test_backup_states(self):
        k = dict(FIELD_ENCRYPTION_KEY="x" * 10)
        self.write_status(age_h=3); self.assertEqual(self.get(**k).status_code, 200)
        self.write_status(age_h=30); r = self.get(**k); self.assertEqual(r.status_code, 503); self.assertIn("30 jam", r.json()["checks"]["backup"]["detail"])
        self.write_status(ok=False, error="pg_dump gagal"); r = self.get(**k); self.assertEqual(r.status_code, 503); self.assertIn("GAGAL", r.json()["checks"]["backup"]["detail"])
        self.assertNotIn("pg_dump", r.content.decode())  # pesan galat mentah tidak dibocorkan
        self.status.write_text("{bukan json"); self.assertEqual(self.get(**k).status_code, 503)
        self.status.write_text(json.dumps({"ok": True, "finished_at": "kemarin"})); self.assertEqual(self.get(**k).status_code, 503)

    def test_missing_status_file_depends_on_require_flag(self):
        k = dict(FIELD_ENCRYPTION_KEY="x" * 10)
        self.assertEqual(self.get(HEALTH_REQUIRE_BACKUP=False, **k).status_code, 200)
        r = self.get(HEALTH_REQUIRE_BACKUP=True, **k); self.assertEqual(r.status_code, 503); self.assertIn("belum ada", r.json()["checks"]["backup"]["detail"])
        with override_settings(BACKUP_STATUS_FILE="", HEALTH_REQUIRE_BACKUP=False, FIELD_ENCRYPTION_KEY="x" * 10): self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_pending_migrations_fail(self):
        self.write_status()
        with mock.patch("apps.core.health.pending_migrations", return_value=2): r = self.get(FIELD_ENCRYPTION_KEY="x" * 10)
        self.assertEqual(r.status_code, 503); self.assertEqual(r.json()["checks"]["migrasi"]["detail"], "2 migrasi belum dijalankan")

    def test_low_disk_fails(self):
        self.write_status()
        with mock.patch("apps.core.health.shutil.disk_usage", return_value=mock.Mock(free=100 * 1024 * 1024)): r = self.get(FIELD_ENCRYPTION_KEY="x" * 10, HEALTH_MIN_FREE_MB=500)
        self.assertEqual(r.status_code, 503); self.assertIn("100 MB", r.json()["checks"]["disk"]["detail"])

    def test_database_down_fails_without_leaking_error(self):
        self.write_status()
        with mock.patch("apps.core.health.connection") as c:
            c.cursor.side_effect = RuntimeError("password authentication failed for user hris"); r = self.get(FIELD_ENCRYPTION_KEY="x" * 10)
        self.assertEqual(r.status_code, 503); self.assertFalse(r.json()["checks"]["database"]["ok"]); self.assertNotIn("password", r.content.decode())

    def test_cache_failure_fails(self):
        self.write_status()
        with mock.patch("apps.core.health.cache.set", side_effect=ConnectionError("redis down")): r = self.get(FIELD_ENCRYPTION_KEY="x" * 10)
        self.assertEqual(r.status_code, 503); self.assertFalse(r.json()["checks"]["cache"]["ok"])

    def test_healthz_liveness(self):
        r = self.client.get("/healthz"); self.assertEqual((r.status_code, r.content), (200, b"ok"))
        with mock.patch("apps.core.health.connection") as c:
            c.cursor.side_effect = RuntimeError("x"); self.assertEqual(self.client.get("/healthz").status_code, 503)


class AlertTests(TestCase):
    def fake_ok(self, status=200):
        resp = mock.MagicMock(); resp.status = status; resp.__enter__.return_value = resp; return mock.patch("apps.core.alerts.urllib.request.urlopen", return_value=resp)

    @override_settings(ALERT_WEBHOOK_URL="https://hooks.example/x", ALERT_EMAILS=[])
    def test_webhook_payload(self):
        with self.fake_ok() as u: r = alerts.send("Judul", "Isi")
        self.assertEqual(r, {"log": True, "webhook": True, "email": None})
        req = u.call_args[0][0]; self.assertEqual(req.full_url, "https://hooks.example/x"); body = json.loads(req.data)
        self.assertEqual((body["text"], body["content"]), ("Judul\nIsi", "Judul\nIsi")); self.assertEqual(req.get_method(), "POST")

    @override_settings(ALERT_WEBHOOK_URL="https://hooks.example/x", ALERT_EMAILS=["a@x.id", "b@x.id"])
    def test_email_and_failure_never_raises(self):
        with mock.patch("apps.core.alerts.urllib.request.urlopen", side_effect=OSError("timeout")): r = alerts.send("Judul", "Isi")
        self.assertEqual((r["webhook"], r["email"]), (False, True)); self.assertEqual(mail.outbox[0].subject, "[HRIS] Judul"); self.assertEqual(mail.outbox[0].to, ["a@x.id", "b@x.id"])
        with mock.patch("apps.core.alerts.send_mail", side_effect=OSError("smtp")), self.fake_ok(500): r = alerts.send("J", "I")
        self.assertEqual((r["webhook"], r["email"]), (False, False))

    @override_settings(ALERT_WEBHOOK_URL="file:///etc/passwd", ALERT_EMAILS=[])
    def test_non_http_webhook_is_refused(self):
        with mock.patch("apps.core.alerts.urllib.request.urlopen") as u: r = alerts.send("J", "I")
        self.assertFalse(r["webhook"]); u.assert_not_called()

    @override_settings(ALERT_WEBHOOK_URL="", ALERT_EMAILS=[])
    def test_send_alert_command_requires_a_working_channel(self):
        with self.assertRaises(CommandError): call_command("send_alert", "Judul", "Isi", stdout=StringIO())
        with override_settings(ALERT_EMAILS=["a@x.id"]):
            out = StringIO(); call_command("send_alert", "Judul", "Isi", stdout=out); self.assertIn("email", out.getvalue())


class CheckHealthCommandTests(TmpMixin, TestCase):
    def setUp(self):
        super().setUp(); self.state = self.tmp / "state.json"
        self.o = override_settings(BACKUP_STATUS_FILE=str(self.status), HEALTH_STATE_FILE=str(self.state), FIELD_ENCRYPTION_KEY="x" * 10, ALERT_WEBHOOK_URL="https://hooks.example/x", ALERT_REPEAT_HOURS=6)
        self.o.enable(); self.addCleanup(self.o.disable)
        resp = mock.MagicMock(); resp.status = 200; resp.__enter__.return_value = resp
        p = mock.patch("apps.core.alerts.urllib.request.urlopen", return_value=resp); self.url = p.start(); self.addCleanup(p.stop)

    def run_cmd(self, *args):
        out = StringIO()
        try: call_command("check_health", *args, stdout=out, stderr=StringIO()); return True, out.getvalue()
        except CommandError as e: return False, str(e)

    def test_healthy_is_silent_and_exits_zero(self):
        self.write_status(); ok, out = self.run_cmd("--notify"); self.assertTrue(ok); self.assertIn("Semua pemeriksaan baik", out); self.url.assert_not_called()

    def test_failure_raises_and_alerts_once_then_recovers(self):
        self.write_status(ok=False)
        ok, msg = self.run_cmd("--notify"); self.assertFalse(ok); self.assertIn("backup", msg); self.assertEqual(self.url.call_count, 1)
        self.assertIn("GAGAL", json.loads(self.url.call_args[0][0].data)["text"])
        self.run_cmd("--notify"); self.assertEqual(self.url.call_count, 1)             # masalah sama: tidak dikirim ulang (anti-banjir)
        self.write_status(ok=False, age_h=2); st = json.loads(self.state.read_text()); st["alerted_at"] = iso(datetime.now(dt_tz.utc) - timedelta(hours=7)); self.state.write_text(json.dumps(st))
        self.run_cmd("--notify"); self.assertEqual(self.url.call_count, 2)             # lewat 6 jam: diulang
        self.write_status(); ok, _ = self.run_cmd("--notify"); self.assertTrue(ok); self.assertEqual(self.url.call_count, 3)
        self.assertIn("PULIH", json.loads(self.url.call_args[0][0].data)["text"])
        self.run_cmd("--notify"); self.assertEqual(self.url.call_count, 3)

    def test_new_failure_set_alerts_immediately(self):
        self.write_status(ok=False); self.run_cmd("--notify"); self.assertEqual(self.url.call_count, 1)
        with override_settings(FIELD_ENCRYPTION_KEY=""): self.run_cmd("--notify")
        self.assertEqual(self.url.call_count, 2); self.assertIn("kunci_enkripsi", json.loads(self.url.call_args[0][0].data)["text"])

    def test_without_notify_never_alerts(self):
        self.write_status(ok=False); ok, _ = self.run_cmd(); self.assertFalse(ok); self.url.assert_not_called(); self.assertFalse(self.state.exists())


class LoggingConfigTests(SimpleTestCase):
    def test_errors_log_is_separate_and_error_only(self):
        h = settings.LOGGING["handlers"]; self.assertEqual(h["errors"]["level"], "ERROR"); self.assertTrue(str(h["errors"]["filename"]).endswith("errors.log"))
        self.assertEqual(settings.LOGGING["root"]["handlers"], ["file", "errors"])

    def test_mail_admins_only_when_configured(self):
        self.assertEqual("mail_admins" in settings.LOGGING["handlers"], bool(settings.ADMINS))


class BackupScriptTests(TmpMixin, SimpleTestCase):
    """Menjalankan scripts/backup.sh sungguhan dengan pg_dump/pg_restore palsu dan Python palsu (merekam argumen send_alert)."""
    def setUp(self):
        super().setUp()
        self.home, self.bin, self.dir = self.tmp / "home", self.tmp / "bin", self.tmp / "backups"
        for d in (self.home / "media", self.bin): d.mkdir(parents=True)
        (self.home / "media" / "ktp.txt").write_text("dokumen"); (self.home / ".env").write_text("DATABASE_URL=postgres://fake/db\n")
        self.alert_log = self.tmp / "alert.log"
        self.exe("pg_dump", 'if [ -n "${FAKE_DUMP_FAIL:-}" ]; then echo "pg_dump: error: connection \\"refused\\" \\\\ detail" >&2; exit 1; fi\n'
                            'while [ $# -gt 0 ]; do [ "$1" = "-f" ] && { shift; echo DUMP > "$1"; }; shift; done')
        self.exe("pg_restore", '[ -n "${FAKE_RESTORE_FAIL:-}" ] && { echo "pg_restore: error: bukan arsip valid" >&2; exit 1; }; exit 0')
        self.exe("fakepy", f'echo "$@" >> "{self.alert_log}"; [ -n "${{FAKE_ALERT_FAIL:-}}" ] && exit 1; exit 0')

    def exe(self, name, body):
        p = self.bin / name; p.write_text("#!/bin/bash\n" + body + "\n"); p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def run_backup(self, **env):
        e = {**os.environ, "HRIS_HOME": str(self.home), "BACKUP_DIR": str(self.dir), "PY": str(self.bin / "fakepy"), "PATH": f"{self.bin}:{os.environ['PATH']}", **env}
        return subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=60)

    def test_success_writes_ok_status_and_no_alert(self):
        r = self.run_backup(); self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads((self.dir / "status.json").read_text()); self.assertTrue(d["ok"]); self.assertEqual(d["error"], ""); self.assertRegex(d["file"], r"^hris_\d{8}_\d{6}\.dump$")
        self.assertEqual(len(list(self.dir.glob("hris_*.dump"))), 1); self.assertEqual(len(list(self.dir.glob("hris_media_*.tar.gz"))), 1)
        self.assertFalse(self.alert_log.exists()); self.assertEqual(oct((self.dir).stat().st_mode & 0o777), "0o700")
        with override_settings(BACKUP_STATUS_FILE=str(self.dir / "status.json"), BACKUP_MAX_AGE_HOURS=26): self.assertTrue(health._backup()[0])  # health membaca berkas yang ditulis skrip

    def test_pg_dump_failure_writes_failed_status_alerts_and_exits_1(self):
        r = self.run_backup(FAKE_DUMP_FAIL="1"); self.assertEqual(r.returncode, 1)
        d = json.loads((self.dir / "status.json").read_text()); self.assertFalse(d["ok"]); self.assertIn("pg_dump", d["error"])  # JSON tetap valid walau galat memuat tanda kutip/backslash
        self.assertEqual(list(self.dir.glob("hris_*")), [])  # tidak ada sisa berkas setengah jadi
        a = self.alert_log.read_text(); self.assertIn("send_alert", a); self.assertIn("Backup HRIS GAGAL", a)
        with override_settings(BACKUP_STATUS_FILE=str(self.dir / "status.json")): self.assertFalse(health._backup()[0])

    def test_unreadable_dump_is_a_failure(self):
        r = self.run_backup(FAKE_RESTORE_FAIL="1"); self.assertEqual(r.returncode, 1); d = json.loads((self.dir / "status.json").read_text())
        self.assertFalse(d["ok"]); self.assertIn("dump tidak terbaca", d["error"]); self.assertEqual(list(self.dir.glob("hris_*.dump")), [])

    def test_failed_alert_does_not_hide_the_failure(self):
        r = self.run_backup(FAKE_DUMP_FAIL="1", FAKE_ALERT_FAIL="1"); self.assertEqual(r.returncode, 1); self.assertFalse(json.loads((self.dir / "status.json").read_text())["ok"])

    def test_failure_after_success_overwrites_status(self):
        self.run_backup(); self.assertTrue(json.loads((self.dir / "status.json").read_text())["ok"])
        self.run_backup(FAKE_DUMP_FAIL="1"); self.assertFalse(json.loads((self.dir / "status.json").read_text())["ok"])
        self.run_backup(); self.assertTrue(json.loads((self.dir / "status.json").read_text())["ok"])

    def test_old_backups_rotated_but_recent_kept(self):
        self.dir.mkdir(); old, new = self.dir / "hris_20200101_000000.dump", self.dir / "hris_20990101_000000.dump"
        old.write_text("x"); new.write_text("x"); t = (datetime.now() - timedelta(days=40)).timestamp(); os.utime(old, (t, t))
        self.assertEqual(self.run_backup().returncode, 0); self.assertFalse(old.exists()); self.assertTrue(new.exists())
