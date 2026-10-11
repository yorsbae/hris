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
