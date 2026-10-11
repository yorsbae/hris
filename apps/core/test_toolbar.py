"""Penjaga putaran 41: bilah alat seragam — [+ Tambah] [Cari…] [Ekspor/Impor ▾] [Filter ▾].
Ekspor/impor dikumpulkan JS (base.html) ke satu popover overlay; HTML server tetap memuat tautan aslinya (tanpa JS tetap berfungsi)."""
import re
from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "core" / "templates" / "base.html").read_text(encoding="utf-8")


class ToolbarComponent(SimpleTestCase):
    def test_component_is_in_base(self):
        for k in ("function toolbar(", ".epanel", ".fprim", "data-pbar", "Ekspor / Impor", "Impor data", "Ekspor (sesuai filter)"):
            self.assertIn(k, BASE, k)
        self.assertIn('toolbar(root,wrap)', BASE)  # dipasang pada setiap [data-fbar]
        self.assertIn('querySelectorAll("[data-pbar]")', BASE)  # halaman tanpa filter

    def test_popover_closes_like_filter(self):
        i = BASE.index("function toolbar(")
        body = BASE[i:i + 6000]
        for k in ("Escape", "aria-expanded", "aria-controls", "fscrim"):
            self.assertIn(k, body, k)

    def test_pages_without_filter_use_pbar(self):
        for rel in ("hr/templates/master_list.html", "poli/templates/poli/diagnosis_list.html"):
            self.assertIn("data-pbar", (ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_server_html_keeps_plain_export_links_as_fallback(self):
        s = (ROOT / "core" / "templates" / "_export.html").read_text(encoding="utf-8")
        self.assertIn("Ekspor CSV", s)
        self.assertIn("Ekspor XLSX", s)

    def test_audit_script_present(self):
        self.assertTrue((ROOT.parent / "scripts" / "ui_toolbar_audit.js").exists())


class ToolbarAlignment(SimpleTestCase):
    """Putaran 46: aksi tambah data rata KIRI; Cari · Ekspor/Impor · Filter rata KANAN (elemen kanan pertama diberi `.fr` = margin-left:auto)."""
    def test_right_group_is_pushed_right(self):
        self.assertIn("function align(", BASE)
        self.assertIn(".fbar .fr{margin-left:auto}", BASE)
        self.assertIn("align(root)", BASE)  # halaman [data-fbar]
        self.assertIn("toolbar(h,null);align(h)", BASE)  # halaman [data-pbar]

    def test_search_does_not_stretch_and_phone_is_reset(self):
        self.assertIn(".fbar .fsearch{flex:0 1 300px;max-width:300px}", BASE)
        self.assertIn(".fbar .fr{margin-left:0}", BASE)  # ponsel: kembali memenuhi baris

    def test_add_buttons_stay_first(self):
        self.assertIn("host.insertBefore(fr,host.firstChild)", BASE)  # [+ Tambah] selalu di awal bilah


class MobileCardTables(SimpleTestCase):
    """Putaran 42: tabel daftar jadi kartu per baris di <=600px (CSS + JS `cardify` di base.html)."""
    def test_component_is_in_base(self):
        for k in ("function cardify(", "table.cards", "attr(data-label)", "nocard", "MutationObserver", "max-width:600px"):
            self.assertIn(k, BASE, k)

    def test_wide_tables_are_not_cardified(self):
        self.assertIn("ths.length>8", BASE)  # grid lebar (jadwal mingguan) tetap bergulir


class MobileCardActions(SimpleTestCase):
    """Putaran 43: sel aksi di kartu = baris tombol selebar kartu; sel kosong disembunyikan; label tanpa teks <small>."""
    def test_action_and_empty_cell_rules_in_base(self):
        for k in ("td.kact", "td.knul", "function hl(", 'classList.toggle("kact"', 'classList.toggle("knul"', "min-height:38px"):
            self.assertIn(k, BASE, k)


class MobileKpiStrip(SimpleTestCase):
    def test_kpi_strip_two_columns_on_phone(self):
        self.assertIn(".sepkpi{display:grid;grid-template-columns:1fr 1fr}", BASE)
