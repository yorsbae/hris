from django.test import TestCase
from .models import Role, User


class RejectNulTests(TestCase):
    """Regresi: byte NUL di parameter membuat PostgreSQL error → 500 (SQLite tidak). Harus 400 di semua endpoint."""
    @classmethod
    def setUpTestData(cls): cls.hrd = User.objects.create_user("hrd", password="kata-sandi-panjang-123", role=Role.HRD)

    def setUp(self): self.client.force_login(self.hrd); self.client.raise_request_exception = False

    def test_get_with_nul_is_400_on_old_and_new_endpoints(self):
        for url in ("/api/employees/?q=%00", "/requests/?q=ab%00cd", "/hrd/bpjs/?q=%00", "/hrd/aids/?q=%00", "/hrd/catering/?meal=%00", "/employees/?%00=1"):
            self.assertEqual(self.client.get(url).status_code, 400, url)

    def test_post_with_nul_is_400(self):
        self.assertEqual(self.client.post("/hrd/aids/new/", {"nik": "001\x00", "amount": 1}).status_code, 400)

    def test_normal_requests_unaffected(self):
        self.assertEqual(self.client.get("/hrd/bpjs/?q=budi").status_code, 200)
        self.assertEqual(self.client.get("/api/employees/?q=%C3%A9").status_code, 200)   # unicode biasa tetap boleh
