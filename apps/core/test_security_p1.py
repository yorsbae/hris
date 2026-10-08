"""Putaran 21, P1: kunci akun per username, throttle zona, timeout idle, header keamanan, kebijakan sandi, check --deploy."""
import time
from datetime import timedelta
from django.contrib.auth.password_validation import validate_password
from django.core import checks
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core import lockout
from apps.core.models import AuditLog, Role, User

PW = "Sandi-Benar-123"


def mk(name="budi", role=Role.HRD, **kw): return User.objects.create_user(name, password=PW, role=role, **kw)


class LockoutTests(TestCase):
    def setUp(self): cache.clear(); self.u = mk(); self.c = Client(REMOTE_ADDR="10.0.0.1")

    def attempt(self, name="budi", pw="salah", c=None, ip=None):
        return (c or self.c).post("/login/", {"username": name, "password": pw}, REMOTE_ADDR=ip or "10.0.0.1")

    def fail(self, n):
        for i in range(n): self.attempt(ip=f"10.1.0.{i + 1}")  # IP berbeda → hanya kunci per username yang diuji (bukan rate limit IP)

    @override_settings(LOGIN_LOCK_THRESHOLD=3)
    def test_locks_after_threshold_even_with_correct_password(self):
        self.fail(3); self.u.refresh_from_db(); self.assertTrue(lockout.is_locked(self.u))
        r = self.attempt(pw=PW, ip="10.2.0.1"); self.assertEqual(r.status_code, 200); self.assertNotIn("_auth_user_id", self.c.session)  # sandi benar pun ditolak
        self.assertEqual(AuditLog.objects.filter(action="account_locked", user=self.u).count(), 1)

    @override_settings(LOGIN_LOCK_THRESHOLD=3)
    def test_locked_and_wrong_look_identical_and_no_enumeration(self):
        self.fail(3)
        locked = self.attempt(pw=PW, ip="10.2.0.1").content.decode(); wrong = self.attempt(pw="x", ip="10.2.0.2").content.decode(); ghost = self.attempt("tidakada", "x", ip="10.2.0.3").content.decode()
        for h in (locked, wrong, ghost): self.assertIn("Username atau password salah", h)
        self.assertNotIn("terkunci", locked.split("Username atau password salah")[0][-200:])  # tidak ada pesan khusus "akun X terkunci"

    @override_settings(LOGIN_LOCK_THRESHOLD=3)
    def test_attempts_during_lock_do_not_escalate(self):
        self.fail(3); self.u.refresh_from_db(); n = self.u.failed_logins
        for i in range(4): self.attempt(ip=f"10.3.0.{i + 1}")
        self.u.refresh_from_db(); self.assertEqual(self.u.failed_logins, n)

    @override_settings(LOGIN_LOCK_THRESHOLD=3, LOGIN_LOCK_STEPS_MIN=(1, 5, 15))
    def test_progressive_steps(self):
        self.fail(3); self.u.refresh_from_db(); self.assertAlmostEqual(lockout.remaining_seconds(self.u), 60, delta=3)
        User.objects.filter(pk=self.u.pk).update(locked_until=timezone.now() - timedelta(seconds=1))  # kunci habis
        self.fail(1); self.u.refresh_from_db(); self.assertAlmostEqual(lockout.remaining_seconds(self.u), 300, delta=3)  # salah lagi → jenjang 2
        User.objects.filter(pk=self.u.pk).update(locked_until=timezone.now() - timedelta(seconds=1)); self.fail(1)
        self.u.refresh_from_db(); self.assertAlmostEqual(lockout.remaining_seconds(self.u), 900, delta=3)
        User.objects.filter(pk=self.u.pk).update(locked_until=timezone.now() - timedelta(seconds=1)); self.fail(1)
        self.u.refresh_from_db(); self.assertAlmostEqual(lockout.remaining_seconds(self.u), 900, delta=3)  # jenjang terakhir berulang (tidak melonjak/overflow)

    @override_settings(LOGIN_LOCK_THRESHOLD=3)
    def test_success_resets_counter_and_unlocks_after_expiry(self):
        self.fail(2); self.assertEqual(self.attempt(pw=PW, ip="10.2.0.9").status_code, 302)
        self.u.refresh_from_db(); self.assertEqual((self.u.failed_logins, self.u.locked_until), (0, None))
        self.fail(3); User.objects.filter(pk=self.u.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.attempt(pw=PW, ip="10.2.0.10").status_code, 302)  # kunci sudah habis → bisa masuk

    @override_settings(LOGIN_LOCK_THRESHOLD=3, LOGIN_LOCK_RESET_HOURS=24)
    def test_old_failures_decay(self):
        self.fail(2); User.objects.filter(pk=self.u.pk).update(last_failed_at=timezone.now() - timedelta(hours=25))
        self.fail(1); self.u.refresh_from_db(); self.assertEqual(self.u.failed_logins, 1); self.assertFalse(lockout.is_locked(self.u))

    @override_settings(LOGIN_LOCK_THRESHOLD=2)
    def test_admin_login_also_locked(self):
        for i in range(2): self.client.post("/admin/login/", {"username": "budi", "password": "x"}, REMOTE_ADDR=f"10.4.0.{i + 1}")
        self.u.refresh_from_db(); self.assertTrue(lockout.is_locked(self.u))

    @override_settings(LOGIN_LOCK_THRESHOLD=2)
    def test_inactive_user_wrong_password_not_counted_and_unknown_user_safe(self):
        User.objects.filter(pk=self.u.pk).update(is_active=False); self.fail(3); self.u.refresh_from_db(); self.assertEqual(self.u.failed_logins, 0)
        self.assertEqual(self.attempt("tidakada", "x", ip="10.5.0.1").status_code, 200)


class UnlockViewTests(TestCase):
    def setUp(self):
        cache.clear(); self.sa = mk("root", Role.SUPERADMIN); self.u = mk(); self.client.force_login(self.sa)
        User.objects.filter(pk=self.u.pk).update(failed_logins=9, locked_until=timezone.now() + timedelta(minutes=30), last_failed_at=timezone.now())

    def test_detail_shows_lock_and_button(self):
        h = self.client.get(reverse("user_detail", args=[self.u.pk])).content.decode(); self.assertIn("terkunci", h); self.assertIn("Buka kunci login", h)

    def test_unlock_clears_and_audits(self):
        r = self.client.post(reverse("user_unlock", args=[self.u.pk])); self.assertRedirects(r, reverse("user_detail", args=[self.u.pk]))
        self.u.refresh_from_db(); self.assertEqual((self.u.failed_logins, self.u.locked_until), (0, None))
        a = AuditLog.objects.filter(action="user_unlock").latest("id"); self.assertEqual((a.before, a.after), ({"locked": True}, {"locked": False}))

    def test_unlock_post_only_and_superadmin_only(self):
        self.assertEqual(self.client.get(reverse("user_unlock", args=[self.u.pk])).status_code, 405)
        for role in (Role.HRD, Role.POLI):
            c = Client(); c.force_login(mk(f"x{role}", role)); self.assertEqual(c.post(reverse("user_unlock", args=[self.u.pk])).status_code, 403)
        self.assertEqual(Client().post(reverse("user_unlock", args=[self.u.pk])).status_code, 302)  # anonim → login
        self.u.refresh_from_db(); self.assertTrue(lockout.is_locked(self.u))

    def test_reset_password_unlocks(self):
        self.client.post(reverse("user_reset_password", args=[self.u.pk]), {"password1": "Sandi-Baru-4567", "password2": "Sandi-Baru-4567"})
        self.u.refresh_from_db(); self.assertFalse(lockout.is_locked(self.u)); self.assertEqual(self.u.failed_logins, 0)

    def test_unlock_missing_user_404(self): self.assertEqual(self.client.post(reverse("user_unlock", args=[99999])).status_code, 404)


@override_settings(THROTTLE_ZONES={"api": (3, 60), "export": (2, 60), "upload": (2, 60)})
class ThrottleTests(TestCase):
    def setUp(self): cache.clear(); self.u = mk(); self.c = Client(); self.c.force_login(self.u)

    def test_api_429_with_retry_after_json(self):
        for _ in range(3): self.assertEqual(self.c.get("/api/dashboard/").status_code, 200)
        r = self.c.get("/api/dashboard/"); self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["detail"], "too_many_requests"); self.assertTrue(1 <= int(r["Retry-After"]) <= 60)

    def test_zones_independent_and_per_user(self):
        for _ in range(3): self.c.get("/api/dashboard/")
        self.assertEqual(self.c.get("/api/dashboard/").status_code, 429)
        self.assertEqual(self.c.get("/employees/").status_code, 200)  # halaman biasa tidak ikut terkena zona api
        other = Client(); other.force_login(mk("lain")); self.assertEqual(other.get("/api/dashboard/").status_code, 200)  # user lain, kuota sendiri

    def test_export_zone(self):
        for _ in range(2): self.assertEqual(self.c.get("/employees/?export=1&format=csv").status_code, 200)
        r = self.c.get("/employees/?export=1&format=csv"); self.assertEqual(r.status_code, 429); self.assertIn("Retry-After", r)

    def test_upload_zone_only_multipart_post(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        for _ in range(2): self.c.post("/employees/import/", {"file": SimpleUploadedFile("a.csv", b"x")})
        self.assertEqual(self.c.post("/employees/import/", {"file": SimpleUploadedFile("a.csv", b"x")}).status_code, 429)
        self.assertNotEqual(self.c.post("/employees/import/", {"a": "b"}, content_type="application/x-www-form-urlencoded").status_code, 429)  # POST biasa bukan unggahan

    @override_settings(THROTTLE_ZONES={})
    def test_disabled_when_empty(self):
        for _ in range(10): self.assertEqual(self.c.get("/api/dashboard/").status_code, 200)

    def test_login_rate_limit_has_retry_after(self):
        for i in range(5): Client().post("/login/", {"username": "a", "password": "b"}, REMOTE_ADDR="10.9.9.9")
        r = Client().post("/login/", {"username": "a", "password": "b"}, REMOTE_ADDR="10.9.9.9"); self.assertEqual(r.status_code, 429); self.assertIn("Retry-After", r)


@override_settings(SESSION_IDLE_TIMEOUT=600)
class IdleTimeoutTests(TestCase):
    def setUp(self): cache.clear(); self.u = mk(); self.c = Client()

    def login(self): self.assertEqual(self.c.post("/login/", {"username": "budi", "password": PW}).status_code, 302)

    def test_login_stamps_and_active_session_survives(self):
        self.login(); self.assertIn("_last", self.c.session); self.assertEqual(self.c.get("/employees/").status_code, 200)

    def test_idle_session_expires_page_and_api(self):
        self.login(); s = self.c.session; s["_last"] = int(time.time()) - 601; s.save()
        r = self.c.get("/employees/"); self.assertEqual(r.status_code, 302); self.assertIn("/login/?timeout=1", r["Location"])
        self.assertTrue(AuditLog.objects.filter(action="session_timeout", user=self.u).exists())
        self.assertEqual(self.c.get("/employees/").status_code, 302)  # benar-benar keluar
        self.assertContains(self.c.get("/login/?timeout=1"), "Sesi berakhir")
        self.login(); s = self.c.session; s["_last"] = int(time.time()) - 601; s.save()
        r = self.c.get("/api/dashboard/"); self.assertEqual((r.status_code, r.json()["detail"]), (401, "session_expired"))

    def test_activity_refreshes_stamp_only_after_interval(self):
        self.login(); s = self.c.session; old = int(time.time()) - 200; s["_last"] = old; s.save()
        self.c.get("/employees/"); self.assertGreater(self.c.session["_last"], old)  # ≥ min(60, 150) detik → disegarkan
        s = self.c.session; recent = int(time.time()) - 5; s["_last"] = recent; s.save(); self.c.get("/employees/"); self.assertEqual(self.c.session["_last"], recent)  # belum waktunya → tidak menulis

    @override_settings(SESSION_IDLE_TIMEOUT=0)
    def test_disabled(self):
        self.login(); s = self.c.session; s["_last"] = 1; s.save(); self.assertEqual(self.c.get("/employees/").status_code, 200)

    def test_anonymous_unaffected(self): self.assertEqual(self.c.get("/login/").status_code, 200)


class HeadersTests(TestCase):
    def test_csp_and_friends_on_every_page(self):
        r = self.client.get("/login/"); csp = r["Content-Security-Policy"]
        for d in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"): self.assertIn(d, csp)
        self.assertNotIn("*", csp.replace("'self'", "")); self.assertNotIn("http:", csp)
        self.assertIn("camera=()", r["Permissions-Policy"]); self.assertEqual(r["X-Frame-Options"], "DENY"); self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Referrer-Policy"], "same-origin")

    def test_no_store_only_when_logged_in(self):
        self.assertNotIn("no-store", self.client.get("/").get("Cache-Control", ""))  # anonim: middleware tidak menambah (login view Django memang never_cache sendiri)
        self.client.force_login(mk()); self.assertIn("no-store", self.client.get("/employees/")["Cache-Control"])

    def test_pages_have_no_external_resources(self):  # CSP default-src 'self' → halaman tidak boleh memuat sumber luar
        import re
        self.client.force_login(mk("sa", Role.SUPERADMIN))
        for url in ("/", "/employees/", "/users/", "/audit/", "/login/"):
            h = self.client.get(url).content.decode(); self.assertFalse(re.search(r"(src|href)=\"https?://", h), url)

    @override_settings(CSP_POLICY="")
    def test_csp_can_be_disabled(self): self.assertNotIn("Content-Security-Policy", self.client.get("/login/"))


class PasswordPolicyTests(TestCase):
    def test_validators(self):
        for bad in ("pendek1", "abcdefghijkl", "1234567890123", "password123", "kartikasari1"):
            with self.assertRaises(ValidationError, msg=bad): validate_password(bad, user=User(username="kartikasari"))
        validate_password("Kuda-Biru-2026")

    def test_change_password_form_enforces(self):
        u = mk(); self.client.force_login(u)
        r = self.client.post("/password/change/", {"old_password": PW, "new_password1": "hanyahuruf-panjang", "new_password2": "hanyahuruf-panjang"})
        self.assertEqual(r.status_code, 200); self.assertContains(r, "huruf dan angka")


class DeployCheckTests(TestCase):
    @override_settings(DEBUG=False, SECRET_KEY="k" * 60 + "!@#$%^&*()_+-=[]{}|;:,.<>?abcXYZ", ALLOWED_HOSTS=["hris.lan"], SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
                       SECURE_HSTS_SECONDS=31536000, SECURE_HSTS_INCLUDE_SUBDOMAINS=True, SECURE_HSTS_PRELOAD=False)
    def test_check_deploy_clean_when_https_settings_applied(self):
        issues = [i for i in checks.run_checks(include_deployment_checks=True) if i.id not in ("security.W021", "security.W008")]  # W021: HSTS preload sengaja tidak diaktifkan; W008: pengalihan HTTPS oleh Nginx (SILENCED_SYSTEM_CHECKS)
        self.assertEqual(issues, [], [i.id for i in issues])

    def test_cache_defaults_to_locmem_without_redis_url(self):
        from django.conf import settings
        if not settings.REDIS_URL: self.assertIn("locmem", settings.CACHES["default"]["BACKEND"])
