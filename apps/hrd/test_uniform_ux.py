"""Putaran 30 → 38: halaman Seragam satu pintu (tab Stok · Pembelian · Riwayat · Master) — alur jelas, aksi utama menonjol, aksi sekunder terlipat, filter ringkas."""
from django.test import TestCase
from apps.core.models import Role, User


class UniformUxTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("hrd", password="x", role=Role.HRD))

    def test_page_shows_flow_primary_actions_and_folded_secondary(self):
        r = self.client.get("/hrd/uniforms/?period=2026-10")
        self.assertContains(r, 'class="hint flow"'); self.assertContains(r, "Barang masuk"); self.assertContains(r, "+ Catat pembelian karyawan"); self.assertContains(r, "+ Barang masuk dari vendor")
        self.assertContains(r, '<details class="more">'); self.assertContains(r, "Impor pembelian dari CSV/XLSX"); self.assertContains(r, "Pesanan ke vendor")
        self.assertContains(r, "Belum ada stok seragam")   # keadaan kosong memberi langkah berikutnya
        self.assertNotContains(r, "Reset filter")

    def test_four_tabs_one_page_and_old_addresses_land_on_the_right_tab(self):
        r = self.client.get("/hrd/uniforms/")
        for label in ("Stok", "Pembelian karyawan", "Riwayat gerak", "Master &amp; tarif"): self.assertContains(r, label)
        self.assertEqual(self.client.get("/hrd/uniforms/").context["tab"], "stok")
        self.assertEqual(self.client.get("/hrd/uniforms/stock/").context["tab"], "riwayat")
        self.assertEqual(self.client.get("/hrd/uniforms/master/").context["tab"], "master")
        r = self.client.get("/hrd/uniforms/stock/in/"); self.assertEqual(r.context["tab"], "stok"); self.assertTrue(r.context["open_form"])
        self.assertEqual(self.client.get("/hrd/uniforms/?tab=ngawur").context["tab"], "stok")

    def test_single_stock_table_replaces_the_two_old_ones(self):
        r = self.client.get("/hrd/uniforms/?tab=stok"); self.assertContains(r, 'id="by-size"'); self.assertNotContains(r, "Saldo saat ini"); self.assertNotContains(r, "Rekap stok seragam")

    def test_advanced_filters_open_only_when_used_and_reset_offered(self):
        r = self.client.get("/hrd/uniforms/?tab=pembelian&period=2026-10&status=belum")
        self.assertContains(r, '<details class="flt" open>'); self.assertContains(r, "Reset filter")
        self.assertContains(self.client.get("/hrd/uniforms/?tab=pembelian&period=2026-10"), '<details class="flt">')
