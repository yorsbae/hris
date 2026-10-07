"""Kerangka UI (putaran 12): navigasi aktif, label peran, aksesibilitas dasar, halaman login."""
from django.test import TestCase
from apps.core.models import User


class UiShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hrd = User.objects.create_user("hrd1", password="x-Pass-12345", role="hrd")
        cls.poli = User.objects.create_user("poli1", password="x-Pass-12345", role="poli")

    def test_login_page_has_labels_and_toggle(self):
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        self.assertIn('for="u"', h); self.assertIn('autocomplete="current-password"', h)
        self.assertNotIn("Keluar", h)  # menu pengguna hanya untuk yang sudah login

    def test_nav_marks_current_section_and_role_label(self):
        self.client.force_login(self.hrd)
        h = self.client.get("/employees/").content.decode()
        self.assertIn('href="/employees/" aria-current="page"', h)
        self.assertNotIn('href="/" aria-current="page"', h)
        self.assertIn("HRD", h)
        self.assertIn('href="/hrd/"', h)

    def test_nav_hides_links_user_cannot_open(self):
        self.client.force_login(self.poli)
        h = self.client.get("/").content.decode()
        self.assertIn('href="/poli/"', h)
        for hidden in ('href="/hrd/"', 'href="/users/"', 'href="/audit/"', 'href="/leave/"'):
            self.assertNotIn(hidden, h)

    def test_shell_accessibility_basics(self):
        self.client.force_login(self.hrd)
        h = self.client.get("/").content.decode()
        for needle in ('class="skip"', 'id="main"', 'aria-label="Navigasi utama"', 'action="/logout/"'):
            self.assertIn(needle, h)

    def test_dashboard_and_employee_list_render_for_each_role(self):
        for u in (self.hrd, self.poli):
            self.client.force_login(u)
            for path in ("/", "/employees/", "/notifications/", "/announcements/"):
                self.assertEqual(self.client.get(path).status_code, 200, (u.role, path))
