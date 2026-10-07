import threading
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from apps.hr.models import Department
from .models import AuditLog, Role, User
from .user_services import UserRuleError, guard

PW = "kata-sandi-panjang-123"
TEMP = "sandi-sementara-789x"
NEW = "sandi-baru-yang-panjang-456"


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN, first_name="Super")
        cls.su2 = User.objects.create_user("su2", password=PW, role=Role.SUPERADMIN, first_name="Super Dua")
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD, first_name="Hera")
        cls.adm = User.objects.create_user("adm", password=PW, role=Role.DEPT_ADMIN, department=cls.d1, first_name="Adi")
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI, first_name="Poli")

    def login(self, who="su"): self.client.force_login(User.objects.get(username=who))

    def audit(self, action): return AuditLog.objects.filter(module="users", action=action).order_by("-id")

    def msgs(self, r): return [str(m) for m in r.context["messages"]] if r.context else []

    def create_payload(self, **kw):
        d = {"username": "budi", "first_name": "Budi", "last_name": "S", "email": "budi@example.com", "role": Role.HRD, "department": "",
             "password1": TEMP, "password2": TEMP}
        d.update(kw); return d


class RbacTests(Base):
    """Seluruh halaman manajemen user & audit: hanya Superadmin."""
    def urls(self):
        t = self.hrd.pk
        get = ["/users/", "/users/new/", f"/users/{t}/", f"/users/{t}/edit/", f"/users/{t}/reset-password/", "/audit/", f"/audit/{AuditLog.objects.first().pk}/"]
        post = [f"/users/{t}/deactivate/", f"/users/{t}/activate/"]
        return get, post

    def setUp(self): AuditLog.objects.create(module="x", action="y")

    def test_non_superadmin_gets_403_on_every_url_and_method(self):
        get, post = self.urls()
        for who in ("hrd", "adm", "poli"):
            self.login(who)
            for u in get: self.assertEqual(self.client.get(u).status_code, 403, (who, "GET", u)); self.assertEqual(self.client.post(u, {}).status_code, 403, (who, "POST", u))
            for u in post: self.assertEqual(self.client.post(u, {"reason": "x"}).status_code, 403, (who, "POST", u))
        self.assertEqual(User.objects.filter(is_active=False).count(), 0)

    def test_anonymous_redirected_to_login(self):
        get, post = self.urls()
        for u in get + post:
            r = self.client.post(u, {}) if u in post else self.client.get(u)
            self.assertEqual(r.status_code, 302, u); self.assertTrue(r["Location"].startswith("/login/"), u)

    def test_post_only_actions_reject_get(self):
        self.login()
        for a in ("deactivate", "activate"): self.assertEqual(self.client.get(f"/users/{self.hrd.pk}/{a}/").status_code, 405)

    def test_superadmin_can_open_everything_and_unknown_ids_404(self):
        self.login(); get, _ = self.urls()
        for u in get: self.assertEqual(self.client.get(u).status_code, 200, u)
        for u in ("/users/999999/", "/users/999999/edit/", "/users/999999/reset-password/", "/audit/999999/"): self.assertEqual(self.client.get(u).status_code, 404, u)
        for a in ("deactivate", "activate"): self.assertEqual(self.client.post(f"/users/999999/{a}/", {"reason": "x"}).status_code, 404)

    def test_no_physical_delete_url(self):
        self.login()
        for u in (f"/users/{self.hrd.pk}/delete/", f"/users/{self.hrd.pk}/remove/"): self.assertEqual(self.client.post(u).status_code, 404)

    def test_nav_links_only_for_superadmin(self):
        self.login("su"); self.assertContains(self.client.get("/"), 'href="/users/"')
        self.login("hrd"); r = self.client.get("/"); self.assertNotContains(r, 'href="/users/"'); self.assertNotContains(r, 'href="/audit/"')


class ListTests(Base):
    def test_filters_and_pagination(self):
        for i in range(60): User.objects.create_user(f"mass{i:02d}", password=PW, role=Role.POLI, first_name=f"Massal {i}")
        self.login()
        r = self.client.get("/users/"); self.assertEqual(len(r.context["page"]), 50); self.assertEqual(r.context["page"].paginator.count, 65)
        self.assertEqual(self.client.get("/users/?page=2").context["page"].paginator.count, 65)
        self.assertEqual(self.client.get("/users/?q=mass05").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get("/users/?q=hera").context["page"].paginator.count, 1)            # cari nama depan
        self.assertEqual(self.client.get(f"/users/?role={Role.HRD}").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(f"/users/?department={self.d1.pk}").context["page"].paginator.count, 1)
        User.objects.filter(username="hrd").update(is_active=False)
        self.assertEqual(self.client.get("/users/?active=nonaktif").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get("/users/?active=aktif").context["page"].paginator.count, 64)
        for junk in ("?role=bukan", "?department=abc", "?page=zzz", "?active=?"): self.assertEqual(self.client.get("/users/" + junk).status_code, 200, junk)

    def test_list_never_exposes_password_hash(self):
        self.login(); body = self.client.get("/users/").content.decode()
        self.assertNotIn("argon2", body); self.assertNotIn("pbkdf2", body)


class CreateTests(Base):
    def post(self, **kw): self.login(); return self.client.post("/users/new/", self.create_payload(**kw))

    def test_create_sets_hashed_password_and_forces_change_and_audits_without_secret(self):
        r = self.post(); u = User.objects.get(username="budi")
        self.assertRedirects(r, f"/users/{u.pk}/", fetch_redirect_response=False)
        self.assertEqual((u.role, u.is_active, u.is_staff, u.is_superuser, u.must_change_password), (Role.HRD, True, False, False, True))
        self.assertTrue(u.password.startswith("argon2")); self.assertTrue(u.check_password(TEMP))
        a = self.audit("user_create").get()
        self.assertEqual((a.user_id, a.object_type, a.object_id), (self.su.pk, "User", str(u.pk)))
        self.assertEqual(a.after["role"], Role.HRD); self.assertNotIn(TEMP, str(list(AuditLog.objects.values()))); self.assertNotIn("password", str(a.after))

    def test_dept_admin_requires_department_and_others_must_not_have_one(self):
        self.assertContains(self.post(username="x1", role=Role.DEPT_ADMIN), "wajib punya departemen")
        self.assertContains(self.client.post("/users/new/", self.create_payload(username="x2", role=Role.POLI, department=self.d1.pk)), "hanya untuk Admin Departemen")
        self.assertEqual(User.objects.filter(username__in=("x1", "x2")).count(), 0)
        self.client.post("/users/new/", self.create_payload(username="x3", role=Role.DEPT_ADMIN, department=self.d2.pk))
        self.assertEqual(User.objects.get(username="x3").department, self.d2)

    def test_username_rules(self):
        self.login()
        for bad in ("SU", "Su", "su ", "a b", "x/y", "<b>", "é!"):
            self.assertContains(self.client.post("/users/new/", self.create_payload(username=bad)), "errorlist", msg_prefix=bad)
        self.assertEqual(User.objects.count(), 5)
        self.assertEqual(self.client.post("/users/new/", self.create_payload(username="Dewi.K-1_x")).status_code, 302)

    def test_password_rules(self):
        self.login()
        self.assertContains(self.client.post("/users/new/", self.create_payload(password1=TEMP, password2=TEMP + "x")), "tidak sama")
        for weak in ("pendek1", "password123", "1234567890"):
            self.assertContains(self.client.post("/users/new/", self.create_payload(password1=weak, password2=weak)), "errorlist", msg_prefix=weak)
        self.assertFalse(User.objects.filter(username="budi").exists())

    def test_invalid_role_and_email_rejected(self):
        self.login()
        self.assertEqual(self.client.post("/users/new/", self.create_payload(role="bos")).status_code, 200)
        self.assertFalse(User.objects.filter(username="budi").exists())
        self.assertContains(self.client.post("/users/new/", self.create_payload(email="bukan-email")), "errorlist")
        self.assertEqual(self.client.post("/users/new/", self.create_payload(role="")).status_code, 200)


class EditTests(Base):
    def edit(self, who="adm", **kw):
        t = User.objects.get(username=who)
        d = {"first_name": t.first_name, "last_name": t.last_name, "email": t.email, "role": t.role, "department": t.department_id or ""}
        d.update(kw); self.login(); return self.client.post(f"/users/{t.pk}/edit/", d)

    def test_change_role_to_hrd_requires_clearing_department_then_audits_exact_before_after(self):
        r = self.edit(role=Role.HRD); self.assertContains(r, "hanya untuk Admin Departemen")  # departemen lama masih terkirim
        self.assertEqual(User.objects.get(username="adm").role, Role.DEPT_ADMIN)
        self.edit(role=Role.HRD, department="", first_name="Adi Baru")
        u = User.objects.get(username="adm"); self.assertEqual((u.role, u.department_id, u.first_name), (Role.HRD, None, "Adi Baru"))
        a = self.audit("user_update").get()
        self.assertEqual(a.before["role"], Role.DEPT_ADMIN); self.assertEqual(a.after["role"], Role.HRD)             # BEFORE benar-benar nilai lama (bug snapshot putaran 10)
        self.assertEqual((a.before["department"], a.after["department"]), ("A", "")); self.assertEqual((a.before["first_name"], a.after["first_name"]), ("Adi", "Adi Baru"))

    def test_move_department_and_promote_to_dept_admin(self):
        self.edit(department=self.d2.pk); self.assertEqual(User.objects.get(username="adm").department, self.d2)
        self.edit("poli", role=Role.DEPT_ADMIN, department=self.d1.pk); self.assertEqual(User.objects.get(username="poli").department, self.d1)
        self.assertEqual(self.edit("hrd", role=Role.DEPT_ADMIN).status_code, 200)   # tanpa departemen ditolak
        self.assertEqual(User.objects.get(username="hrd").role, Role.HRD)

    def test_no_change_writes_no_audit(self):
        self.edit(); self.assertEqual(self.audit("user_update").count(), 0)

    def test_username_password_flags_not_editable_through_form(self):
        before = User.objects.get(username="adm")
        self.edit(username="hacker", password="x", is_superuser="on", is_staff="on", is_active="", must_change_password="on")
        u = User.objects.get(pk=before.pk)
        self.assertEqual((u.username, u.password, u.is_superuser, u.is_staff, u.is_active, u.must_change_password), ("adm", before.password, False, False, True, False))

    def test_own_role_cannot_be_changed_but_name_can(self):
        self.edit("su", role=Role.POLI, first_name="Nama Baru")
        u = User.objects.get(username="su"); self.assertEqual((u.role, u.first_name), (Role.SUPERADMIN, "Nama Baru"))   # kolom role dikunci di server

    def test_demoting_another_superadmin_is_allowed_while_one_remains(self):
        self.edit("su2", role=Role.HRD); self.assertEqual(User.objects.get(username="su2").role, Role.HRD)
        # kini hanya `su`: tak ada Superadmin lain yang bisa diturunkan, dan `su` tak bisa menurunkan dirinya
        self.edit("su", role=Role.HRD); self.assertEqual(User.objects.get(username="su").role, Role.SUPERADMIN)


class DeactivateTests(Base):
    def test_requires_reason_and_blocks_login_and_kills_live_session(self):
        self.login(); live = Client(); live.force_login(self.hrd)
        self.assertEqual(live.get("/employees/").status_code, 200)
        r = self.client.post(f"/users/{self.hrd.pk}/deactivate/", {"reason": "  "}, follow=True)
        self.assertIn("Alasan wajib diisi.", self.msgs(r)); self.assertTrue(User.objects.get(username="hrd").is_active)
        self.client.post(f"/users/{self.hrd.pk}/deactivate/", {"reason": "resign"})
        self.assertFalse(User.objects.get(username="hrd").is_active)
        self.assertEqual(live.get("/employees/").status_code, 302)                       # sesi yang sedang berjalan langsung tak berlaku
        self.assertFalse(Client().login(username="hrd", password=PW))                    # tak bisa login lagi
        a = self.audit("user_deactivate").get(); self.assertEqual((a.before, a.after), ({"is_active": True}, {"is_active": False, "reason": "resign"}))

    def test_activate_restores_access_and_audits(self):
        self.login(); self.client.post(f"/users/{self.hrd.pk}/deactivate/", {"reason": "cuti panjang"})
        self.client.post(f"/users/{self.hrd.pk}/activate/")
        self.assertTrue(User.objects.get(username="hrd").is_active); self.assertTrue(Client().login(username="hrd", password=PW))
        self.assertEqual(self.audit("user_activate").count(), 1)

    def test_repeat_actions_are_idempotent_without_duplicate_audit(self):
        self.login()
        for _ in range(2): self.client.post(f"/users/{self.hrd.pk}/deactivate/", {"reason": "x"})
        self.assertEqual(self.audit("user_deactivate").count(), 1)
        for _ in range(2): self.client.post(f"/users/{self.hrd.pk}/activate/")
        self.assertEqual(self.audit("user_activate").count(), 1)

    def test_cannot_deactivate_self(self):
        self.login(); r = self.client.post(f"/users/{self.su.pk}/deactivate/", {"reason": "x"}, follow=True)
        self.assertTrue(User.objects.get(username="su").is_active); self.assertTrue(any("Anda sendiri" in m for m in self.msgs(r)))
        self.assertEqual(self.audit("user_deactivate").count(), 0)

    def test_can_deactivate_other_superadmin_while_one_remains(self):
        self.login(); self.client.post(f"/users/{self.su2.pk}/deactivate/", {"reason": "dobel akun"})
        self.assertFalse(User.objects.get(username="su2").is_active)
        self.assertEqual(User.objects.filter(role=Role.SUPERADMIN, is_active=True).count(), 1)

    def test_deactivated_users_audit_trail_remains_attributed(self):
        self.client.force_login(self.hrd); self.client.get("/employees/")   # force_login sudah mencatat 'login'
        n = AuditLog.objects.filter(user=self.hrd).count(); self.assertGreater(n, 0)
        self.login(); self.client.post(f"/users/{self.hrd.pk}/deactivate/", {"reason": "x"})
        self.assertEqual(AuditLog.objects.filter(user=self.hrd).count(), n)


class ResetPasswordTests(Base):
    def test_reset_sets_flag_cuts_sessions_forces_change_and_hides_secret(self):
        live = Client(); live.force_login(self.hrd); self.assertEqual(live.get("/employees/").status_code, 200)
        self.login(); r = self.client.post(f"/users/{self.hrd.pk}/reset-password/", {"password1": TEMP, "password2": TEMP})
        self.assertRedirects(r, f"/users/{self.hrd.pk}/", fetch_redirect_response=False)
        u = User.objects.get(username="hrd"); self.assertTrue(u.must_change_password); self.assertTrue(u.check_password(TEMP)); self.assertFalse(u.check_password(PW))
        self.assertEqual(live.get("/employees/").status_code, 302); self.assertTrue(live.get("/employees/")["Location"].startswith("/login/"))  # sesi lama putus
        fresh = Client(); self.assertTrue(fresh.login(username="hrd", password=TEMP)); self.assertEqual(fresh.get("/employees/")["Location"], "/password/change/")
        a = self.audit("password_reset").get(); self.assertEqual(a.after, {"must_change_password": True})
        self.assertNotIn(TEMP, str(list(AuditLog.objects.values())))

    def test_validation(self):
        self.login(); url = f"/users/{self.hrd.pk}/reset-password/"
        self.assertContains(self.client.post(url, {"password1": TEMP, "password2": "lain-lagi-123"}), "tidak sama")
        self.assertContains(self.client.post(url, {"password1": "pendek", "password2": "pendek"}), "errorlist")
        self.assertContains(self.client.post(url, {"password1": PW, "password2": PW}), "berbeda dari sandi lama")
        self.assertTrue(User.objects.get(username="hrd").check_password(PW)); self.assertEqual(self.audit("password_reset").count(), 0)

    def test_cannot_reset_own_password_here(self):
        self.login(); r = self.client.post(f"/users/{self.su.pk}/reset-password/", {"password1": TEMP, "password2": TEMP})
        self.assertRedirects(r, "/password/change/", fetch_redirect_response=False); self.assertTrue(User.objects.get(username="su").check_password(PW))

    def test_full_onboarding_flow(self):
        self.login(); self.client.post("/users/new/", self.create_payload(username="karyawan1", role=Role.POLI))
        c = Client(); self.assertTrue(c.login(username="karyawan1", password=TEMP))
        self.assertEqual(c.get("/poli/")["Location"], "/password/change/")                              # belum boleh ke mana pun
        c.post("/password/change/", {"old_password": TEMP, "new_password1": NEW, "new_password2": NEW})
        self.assertEqual(c.get("/poli/").status_code, 200)
        self.assertFalse(Client().login(username="karyawan1", password=TEMP)); self.assertTrue(Client().login(username="karyawan1", password=NEW))


class DetailTests(Base):
    def test_detail_shows_recent_activity_and_no_secrets_and_self_has_no_danger_actions(self):
        self.login()
        r = self.client.get(f"/users/{self.hrd.pk}/"); self.assertContains(r, "Nonaktifkan"); self.assertContains(r, "Reset sandi"); self.assertNotContains(r, "argon2")
        r = self.client.get(f"/users/{self.su.pk}/"); self.assertNotContains(r, "Nonaktifkan akun"); self.assertNotContains(r, "Reset sandi"); self.assertContains(r, "akun Anda")
        User.objects.filter(pk=self.hrd.pk).update(is_active=False)
        self.assertContains(self.client.get(f"/users/{self.hrd.pk}/"), "Aktifkan kembali")

    def test_xss_in_names_is_escaped(self):
        User.objects.filter(pk=self.hrd.pk).update(first_name="<script>alert(1)</script>"); self.login()
        for url in ("/users/", f"/users/{self.hrd.pk}/", f"/users/{self.hrd.pk}/edit/"):
            self.assertNotContains(self.client.get(url), "<script>alert(1)</script>")


class GuardTests(Base):
    """`guard` = invarian 'selalu ada Superadmin aktif' + pelaku diverifikasi ulang di dalam transaksi."""
    def run_guard(self, actor, target, role, active):
        with transaction.atomic(): return guard(actor.pk, target.pk, role, active)

    def test_self_change_rejected_other_allowed(self):
        with self.assertRaisesMessage(UserRuleError, "Anda sendiri"): self.run_guard(self.su, self.su, Role.HRD, True)
        with self.assertRaisesMessage(UserRuleError, "Anda sendiri"): self.run_guard(self.su, self.su, Role.SUPERADMIN, False)
        self.assertEqual(self.run_guard(self.su, self.su2, Role.HRD, True).pk, self.su2.pk)

    def test_last_active_superadmin_cannot_be_removed_by_anyone(self):
        User.objects.filter(pk=self.su2.pk).update(is_active=False)           # tinggal `su` sebagai satu-satunya Superadmin aktif
        for role, active in ((Role.HRD, True), (Role.SUPERADMIN, False)):
            with self.assertRaisesMessage(UserRuleError, "yang terakhir"): self.run_guard(self.su, self.su, role, active)
        self.assertEqual(self.run_guard(self.su, self.su, Role.SUPERADMIN, True).pk, self.su.pk)   # tanpa perubahan = boleh

    def test_stale_actor_who_was_demoted_or_deactivated_is_refused(self):
        User.objects.filter(pk=self.su2.pk).update(role=Role.HRD)             # su2 diturunkan oleh proses lain setelah lolos pemeriksaan role di awal request
        with self.assertRaises(PermissionDenied): self.run_guard(self.su2, self.hrd, Role.POLI, True)
        User.objects.filter(pk=self.su2.pk).update(role=Role.SUPERADMIN, is_active=False)
        with self.assertRaises(PermissionDenied): self.run_guard(self.su2, self.hrd, Role.POLI, True)

    def test_non_superadmin_actor_refused(self):
        with self.assertRaises(PermissionDenied): self.run_guard(self.hrd, self.poli, Role.HRD, True)


@skipUnlessDBFeature("has_select_for_update")
class SuperadminFloorConcurrencyTests(TransactionTestCase):
    """Dua Superadmin saling menonaktifkan/menurunkan BERSAMAAN: tanpa kunci baris keduanya sukses → nol Superadmin. Hanya bermakna di PostgreSQL."""
    def _race(self, action_a, action_b):
        a = User.objects.create_user("sa", password=PW, role=Role.SUPERADMIN); b = User.objects.create_user("sb", password=PW, role=Role.SUPERADMIN)
        barrier, codes = threading.Barrier(2), []

        def worker(actor, target, action):
            try:
                c = Client(); c.force_login(actor); barrier.wait(timeout=5)
                codes.append(action(c, target).status_code)
            except Exception as ex: codes.append(f"ERROR {type(ex).__name__}: {ex}")
            finally: connection.close()

        ts = [threading.Thread(target=worker, args=(a, b, action_a)), threading.Thread(target=worker, args=(b, a, action_b))]
        [t.start() for t in ts]; [t.join(15) for t in ts]
        self.assertFalse([c for c in codes if isinstance(c, str)], codes)
        return codes

    def test_mutual_deactivate(self):
        deact = lambda c, t: c.post(f"/users/{t.pk}/deactivate/", {"reason": "balas"})
        codes = self._race(deact, deact)
        self.assertEqual(User.objects.filter(role=Role.SUPERADMIN, is_active=True).count(), 1, codes)

    def test_mutual_demote(self):
        def demote(c, t): return c.post(f"/users/{t.pk}/edit/", {"first_name": "x", "last_name": "", "email": "", "role": Role.HRD, "department": ""})
        codes = self._race(demote, demote)
        self.assertEqual(User.objects.filter(role=Role.SUPERADMIN, is_active=True).count(), 1, codes)

    def test_deactivate_vs_demote_cross(self):
        deact = lambda c, t: c.post(f"/users/{t.pk}/deactivate/", {"reason": "x"})
        def demote(c, t): return c.post(f"/users/{t.pk}/edit/", {"first_name": "x", "last_name": "", "email": "", "role": Role.HRD, "department": ""})
        codes = self._race(deact, demote)
        self.assertEqual(User.objects.filter(role=Role.SUPERADMIN, is_active=True).count(), 1, codes)
