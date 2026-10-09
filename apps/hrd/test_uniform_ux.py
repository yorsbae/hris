"""Putaran 30: halaman Seragam — alur jelas, aksi utama menonjol, aksi sekunder terlipat, filter ringkas."""
from django.test import TestCase
from apps.core.models import Role, User


class UniformUxTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("hrd", password="x", role=Role.HRD))

    def test_page_shows_flow_primary_actions_and_folded_secondary(self):
        r = self.client.get("/hrd/uniforms/?period=2026-10")
        self.assertContains(r, "Alur:"); self.assertContains(r, "+ Catat pembelian karyawan"); self.assertContains(r, "+ Barang masuk dari vendor")
        self.assertContains(r, '<details class="more">'); self.assertContains(r, "Kartu stok"); self.assertContains(r, "Impor dari CSV/XLSX")
        self.assertContains(r, "Pastikan stok sudah dicatat")  # keadaan kosong memberi langkah berikutnya
        self.assertNotContains(r, "Reset filter")

    def test_advanced_filters_open_only_when_used_and_reset_offered(self):
        r = self.client.get("/hrd/uniforms/?period=2026-10&status=belum")
        self.assertContains(r, '<details class="flt" open>'); self.assertContains(r, "Reset filter")
        self.assertContains(self.client.get("/hrd/uniforms/?period=2026-10"), '<details class="flt">')
