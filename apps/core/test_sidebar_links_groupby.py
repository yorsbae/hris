"""Putaran 62: sidebar bisa diciutkan (ikon + flyout submenu), tautan aksi tabel = tombol-chip (.lk), group-by bertingkat + saring kolom di semua halaman tabel."""
from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "core" / "templates" / "base.html").read_text(encoding="utf-8")
ICONS = (ROOT / "core" / "templates" / "_icons.html").read_text(encoding="utf-8")


class SidebarCollapse(SimpleTestCase):
    def test_toggle_button_icon_and_state(self):
        for k in ('id="sbt"', 'aria-pressed="false"', 'href="#i-sidebar"', "html.sbc aside.side{width:72px}", "html.sbc .col{margin-left:72px}"):
            self.assertIn(k, BASE, k)
        self.assertIn('id="i-sidebar"', ICONS)

    def test_state_is_restored_before_paint_and_saved(self):
        self.assertIn('localStorage.getItem("sb")==="1"', BASE)  # tanpa kedip saat pindah halaman
        self.assertIn('localStorage.setItem("sb"', BASE)

    def test_collapsed_keeps_icons_and_submenu_flyout(self):
        for k in ("html.sbc nav.main a>span:not(.badge){display:none}", "html.sbc .nsec.fly>.subs", "position:fixed", 'subs.setAttribute("data-t"', "a.title=sp.textContent.trim()"):
            self.assertIn(k, BASE, k)

    def test_only_on_desktop(self):
        self.assertIn("@media(max-width:1000px){.sbt{display:none}}", BASE)
        self.assertIn("@media not all and (max-width:1000px){", BASE)


class ActionLinks(SimpleTestCase):
    def test_link_chip_style_and_auto_class(self):
        for k in ("a.lk{display:inline-flex", "a.lk:hover", "ACT=/^(ubah|edit|kelola", 'a.classList.add("lk")'):
            self.assertIn(k, BASE, k)

    def test_pages_use_chip(self):
        for rel, needle in (("hrd/templates/hrd/bpjs_list.html", 'class="lk"'), ("poli/templates/poli/diagnosis_list.html", 'class="lk"')):
            self.assertIn(needle, (ROOT / rel).read_text(encoding="utf-8"), rel)


class GroupByFilter(SimpleTestCase):
    def test_multi_level_group_and_column_filter(self):
        for k in ('mk("groupby2"', 'mk("groupby3"', "fieldset.cfset", 'i.name="cf"', "function sgVis(", "sgnone"):
            self.assertIn(k, BASE, k)

    def test_group_rows_collapsible_and_keyboard_accessible(self):
        for k in ('td.setAttribute("aria-expanded"', 'e.key!=="Enter"', "tr.grow.cl td::before"):
            self.assertIn(k, BASE, k)

    def test_auto_toolbar_on_pages_without_filter_bar(self):
        self.assertIn('main .wrap>table,main .table-wrap>table', BASE)
        self.assertIn(".nosg", BASE)

    def test_diagnosis_page_gets_filter_button(self):
        s = (ROOT / "poli" / "templates" / "poli" / "diagnosis_list.html").read_text(encoding="utf-8")
        self.assertIn("data-fbar data-pbar", s)
