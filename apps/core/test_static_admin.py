"""Putaran 13: berkas statis Django admin harus tersaji walau DEBUG=False (sebelumnya /admin/ tampil tanpa CSS & ikon jadi kotak hitam)."""
import re
from django.test import TestCase, override_settings
from apps.core.models import User


class AdminStaticTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_user("root1", password="x-Pass-12345", role="superadmin", is_staff=True, is_superuser=True)
        cls.hrd = User.objects.create_user("hrd1", password="x-Pass-12345", role="hrd")

    @override_settings(DEBUG=False)
    def test_admin_static_served_without_debug(self):
        for url, ctype in (("/static/admin/css/base.css", "text/css"), ("/static/admin/js/theme.js", "javascript")):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(ctype, r["Content-Type"])

    def test_every_asset_linked_from_admin_index_resolves(self):
        self.client.force_login(self.root)
        h = self.client.get("/admin/").content.decode()
        urls = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', h))
        self.assertTrue(urls, "halaman admin harus memuat berkas statis")
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 200, u)

    def test_static_does_not_expose_media_or_source(self):
        for u in ("/static/../.env", "/static/../config/settings.py", "/media/", "/static/hr/models.py"):
            self.assertNotEqual(self.client.get(u).status_code, 200, u)

    def test_admin_branding_and_app_names(self):
        self.client.force_login(self.root)
        h = self.client.get("/admin/").content.decode()
        for needle in ("HRIS &amp; Poliklinik", "HR Core", "Operasional HRD", "Poliklinik"):
            self.assertIn(needle, h)

    def test_non_superadmin_still_cannot_open_admin(self):
        self.client.force_login(self.hrd)
        self.assertEqual(self.client.get("/admin/").status_code, 302)  # diarahkan ke login admin, bukan masuk
