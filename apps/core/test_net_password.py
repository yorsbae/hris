from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from .models import AuditLog, Role, User
from .net import client_ip

PW = "kata-sandi-panjang-123"
NEW = "sandi-baru-yang-panjang-456"


def req(remote, xff=None):
    meta = {"REMOTE_ADDR": remote}
    if xff is not None: meta["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **meta)


class ClientIpTests(TestCase):
    """Di belakang Nginx REMOTE_ADDR selalu 127.0.0.1; X-Forwarded-For hanya dipercaya dari proxy terdaftar dan tidak bisa dipalsukan klien."""
    @override_settings(TRUSTED_PROXY_IPS=[])
    def test_no_trusted_proxy_ignores_header(self):
        self.assertEqual(client_ip(req("10.0.0.5", "1.2.3.4")), "10.0.0.5")

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_trusted_proxy_uses_forwarded_client(self):
        self.assertEqual(client_ip(req("127.0.0.1", "203.0.113.9")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_spoofed_left_entries_ignored(self):
        # klien mengirim XFF palsu; Nginx menambahkan IP asli di KANAN → yang dipakai entri kanan
        self.assertEqual(client_ip(req("127.0.0.1", "6.6.6.6, 203.0.113.9")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"])
    def test_proxy_chain_cidr(self):
        self.assertEqual(client_ip(req("10.0.0.2", "203.0.113.9, 10.0.0.7")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_untrusted_remote_header_ignored(self):
        self.assertEqual(client_ip(req("198.51.100.4", "6.6.6.6")), "198.51.100.4")  # akses langsung memalsukan header

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_garbage_or_missing_header_falls_back_to_proxy(self):
        self.assertEqual(client_ip(req("127.0.0.1", "203.0.113.9, bukan-ip")), "127.0.0.1")
        self.assertEqual(client_ip(req("127.0.0.1")), "127.0.0.1")
        self.assertEqual(client_ip(req("127.0.0.1", " , ")), "127.0.0.1")

    @override_settings(TRUSTED_PROXY_IPS=["bukan-ip", "", "127.0.0.1"])
    def test_bad_config_entries_do_not_widen_trust(self):
        self.assertEqual(client_ip(req("198.51.100.4", "6.6.6.6")), "198.51.100.4")
        self.assertEqual(client_ip(req("127.0.0.1", "203.0.113.9")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_IPS=["::1"])
    def test_ipv6(self):
        self.assertEqual(client_ip(req("::1", "2001:db8::7")), "2001:db8::7")

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_audit_and_login_signals_record_real_client_ip(self):
        u = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        kw = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.9"}
        self.client.post("/login/", {"username": "hrd", "password": "salah"}, **kw)
        self.client.post("/login/", {"username": "hrd", "password": PW}, **kw)
        self.assertEqual(AuditLog.objects.get(action="login_failed").ip, "203.0.113.9")
        self.assertEqual(AuditLog.objects.get(action="login", user=u).ip, "203.0.113.9")


class ViewAuditIpTests(TestCase):
    """Jalur `audit.log()` (aksi di view, bukan sinyal login) juga harus mencatat IP klien asli, bukan alamat proxy."""
    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_view_action_records_forwarded_client_ip(self):
        su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN); e = AuditLog.objects.create(module="x", action="y")
        self.client.force_login(su)
        self.client.get(f"/audit/{e.pk}/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.50")
        self.assertEqual(AuditLog.objects.get(action="view_entry").ip, "203.0.113.50")

    @override_settings(TRUSTED_PROXY_IPS=[])
    def test_without_proxy_config_forged_header_is_not_recorded(self):
        su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN); e = AuditLog.objects.create(module="x", action="y")
        self.client.force_login(su)
        self.client.get(f"/audit/{e.pk}/", REMOTE_ADDR="198.51.100.4", HTTP_X_FORWARDED_FOR="6.6.6.6")
        self.assertEqual(AuditLog.objects.get(action="view_entry").ip, "198.51.100.4")


class LoginAuditSignalTests(TestCase):
    """Regresi: receiver sinyal lama (closure, weak ref) dibuang garbage collector → login/logout/login_failed tidak pernah tercatat."""
    def test_login_failed_login_logout_are_recorded(self):
        import gc
        u = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        gc.collect()  # bug lama muncul setelah GC; paksa agar tes tidak bergantung waktu
        self.client.post("/login/", {"username": "hrd", "password": "salah"})
        self.client.post("/login/", {"username": "hrd", "password": PW})
        self.client.post("/logout/")
        acts = list(AuditLog.objects.filter(module="auth").order_by("id").values_list("action", "user_id"))
        self.assertEqual(acts, [("login_failed", None), ("login", u.pk), ("logout", u.pk)])

    def test_failed_login_does_not_store_attempted_username_or_password(self):
        self.client.post("/login/", {"username": "orang-asing", "password": "rahasia-salah-ketik"})
        self.assertNotIn("rahasia-salah-ketik", str(list(AuditLog.objects.values()))); self.assertNotIn("orang-asing", str(list(AuditLog.objects.values())))


class LoginRateLimitTests(TestCase):
    """Sebelumnya kunci rate limit = REMOTE_ADDR → di belakang Nginx satu orang bisa mengunci login SEMUA pengguna."""
    def setUp(self): cache.clear(); User.objects.create_user("hrd", password=PW, role=Role.HRD)

    def attempts(self, n, **kw): return [self.client.post("/login/", {"username": "hrd", "password": "salah"}, **kw).status_code for _ in range(n)]

    @override_settings(TRUSTED_PROXY_IPS=["127.0.0.1"])
    def test_limit_is_per_real_client_behind_proxy(self):
        a = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.1"}
        b = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.2"}
        self.assertEqual(self.attempts(6, **a), [200] * 5 + [429])      # penyerang A terkunci
        self.assertEqual(self.client.post("/login/", {"username": "hrd", "password": PW}, **b).status_code, 302)  # B tidak terdampak
        self.assertEqual(self.client.post("/login/", {"username": "hrd", "password": "x"}, **a).status_code, 429)

    @override_settings(TRUSTED_PROXY_IPS=[])
    def test_forged_header_cannot_evade_limit_without_proxy(self):
        kw = lambda i: {"REMOTE_ADDR": "198.51.100.4", "HTTP_X_FORWARDED_FOR": f"1.1.1.{i}"}
        codes = [self.client.post("/login/", {"username": "hrd", "password": "salah"}, **kw(i)).status_code for i in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])  # ganti-ganti header tidak mereset hitungan


class ForcePasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("baru", password=PW, role=Role.HRD, must_change_password=True)

    def setUp(self): cache.clear(); self.client.force_login(self.u)

    def test_pages_redirect_to_change_page_and_api_is_403(self):
        for url in ("/", "/employees/", "/requests/", "/announcements/", "/hrd/", "/leave/"):
            r = self.client.get(url); self.assertEqual((r.status_code, r["Location"]), (302, "/password/change/"), url)
        r = self.client.get("/api/employees/"); self.assertEqual(r.status_code, 403); self.assertEqual(r.json()["detail"], "password_change_required")
        self.assertEqual(self.client.post("/hrd/aids/new/", {}).status_code, 302)  # aksi tulis pun diblokir

    def test_change_page_logout_and_static_stay_reachable(self):
        self.assertEqual(self.client.get("/password/change/").status_code, 200)
        self.assertEqual(self.client.post("/logout/").status_code, 302)
        self.client.force_login(self.u)
        self.assertEqual(self.client.get("/static/tidak-ada.css").status_code, 404)  # tidak dialihkan

    def test_user_without_flag_unaffected(self):
        User.objects.filter(pk=self.u.pk).update(must_change_password=False)
        self.assertEqual(self.client.get("/employees/").status_code, 200)

    def post(self, old, new): return self.client.post("/password/change/", {"old_password": old, "new_password1": new, "new_password2": new})

    def errs(self, old, new): return set(self.post(old, new).context["form"].errors)

    def test_validation_rules(self):
        self.assertEqual(self.errs("salah-lama-123", NEW), {"old_password"})                 # sandi lama salah
        self.assertContains(self.post(PW, PW), "harus berbeda")                              # sama dengan yang lama
        self.assertEqual(self.errs(PW, PW), {"new_password1"})
        self.assertEqual(self.errs(PW, "pendek1"), {"new_password2"})                        # validator Django (min 10)
        self.assertEqual(self.errs(PW, "password123"), {"new_password2"})                    # sandi umum
        self.u.refresh_from_db(); self.assertTrue(self.u.must_change_password); self.assertTrue(self.u.check_password(PW))

    def test_successful_change_clears_flag_keeps_session_and_audits_without_secret(self):
        r = self.post(PW, NEW)
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.u.refresh_from_db()
        self.assertFalse(self.u.must_change_password); self.assertTrue(self.u.check_password(NEW)); self.assertTrue(self.u.password.startswith("argon2"))
        self.assertEqual(self.client.get("/employees/").status_code, 200)   # sesi tetap hidup, tidak dialihkan lagi
        a = AuditLog.objects.get(module="users", action="password_change")
        self.assertEqual((a.user_id, a.before, a.after), (self.u.pk, None, None))
        self.assertNotIn(NEW, str(list(AuditLog.objects.values())))

    def test_anonymous_cannot_use_change_page(self):
        self.client.logout(); self.assertEqual(self.client.get("/password/change/").status_code, 302)


class CreateSuperuserTests(TestCase):
    def test_createsuperuser_gets_superadmin_role(self):
        u = User.objects.create_superuser("root", password=PW)
        self.assertEqual(u.role, Role.SUPERADMIN); self.assertTrue(u.is_superuser)
        self.assertEqual(self.client.login(username="root", password=PW), True)
        self.client.force_login(u); self.assertEqual(self.client.get("/users/").status_code, 200)  # tak lagi terlempar ke /admin/

    def test_explicit_role_is_respected(self):
        self.assertEqual(User.objects.create_superuser("r2", password=PW, role=Role.HRD).role, Role.HRD)
