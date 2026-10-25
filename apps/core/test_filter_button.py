"""Penjaga putaran 40 (popover overlay sejak 40c): setiap form GET yang berisi kontrol filter (pilihan, tanggal, bulan, centang) wajib memakai
tombol Filter bersama (`data-fbar`, dipasang oleh JS di base.html). Tidak boleh ada filter terlipat kedua (`details.flt` berisi form GET)."""
import re
from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
FORM = re.compile(r"<form\b[^>]*>.*?</form>", re.S)
CONTROL = re.compile(r"<select\b|type=\"(?:date|month|checkbox)\"|type=\"number\"", re.S)
# form GET yang bukan filter daftar (pemilih/penyusun isian pada halaman form) — beri alasan bila menambah
ALLOW = {"extra_work_form.html", "periods.html", "thr.html", "tax.html"}   # periods.html (Absensi): isian rentang Tutup Periode; thr.html / tax.html (Payroll): isian tanggal hari raya / tahun + tombol hitung/unduh, bukan filter daftar


def templates():
    return [p for p in ROOT.rglob("*.html") if "templates" in p.parts]


class FilterButtonGuard(SimpleTestCase):
    def test_every_get_filter_form_uses_the_shared_filter_button(self):
        bad = []
        for p in templates():
            if p.name in ALLOW: continue
            s = p.read_text(encoding="utf-8")
            for m in FORM.finditer(s):
                tag = m.group(0)[: m.group(0).index(">") + 1]
                if re.search(r"method=\"get\"", tag, re.I) and CONTROL.search(m.group(0)) and "data-fbar" not in tag:
                    bad.append(p.name)
            for m in re.finditer(r"<div class=\"filters\"[^>]*>", s):
                if "data-fbar" not in m.group(0): bad.append(p.name)
        self.assertEqual(bad, [], "form/div filter tanpa data-fbar: %s" % sorted(set(bad)))

    def test_no_second_collapsed_filter_form(self):
        bad = [p.name for p in templates() if re.search(r"<details class=\"flt\"[^>]*>(?:(?!</details>).)*?<form[^>]*method=\"get\"", p.read_text(encoding="utf-8"), re.S | re.I)]
        self.assertEqual(bad, [], "filter terlipat kedua (details.flt berisi form GET): %s" % bad)

    def test_shared_component_is_in_base(self):
        s = (ROOT / "core" / "templates" / "base.html").read_text(encoding="utf-8")
        for k in (".fbtn", ".fpanel", "[data-fbar]", "aria-expanded", "aria-controls"): self.assertIn(k, s)
        self.assertIn("position:absolute;z-index:60", s)  # popover overlay hemat ruang (putaran 40c)
        self.assertIn(".fscrim", s)                       # ponsel: lembar bawah + latar
