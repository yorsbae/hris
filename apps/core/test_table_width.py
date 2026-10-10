"""Penjaga UI putaran 39: tabel daftar tidak boleh melebar (≤ 8 kolom) agar tidak perlu scroll samping."""
import glob
import re

from django.test import SimpleTestCase

MAX_COLS = 8
EXEMPT = set()
BRANCH = re.compile(r"\{%\s*(?:if|elif|else|endif)\b[^%]*%\}")


def columns(thead):
    """Jumlah <th>; untuk cabang {% if/elif/else %} dihitung cabang terlebar (bukan jumlah semua cabang)."""
    seg = [len(re.findall(r"<th[ >]", x)) for x in BRANCH.split(thead)]
    return seg[0] + max(seg[1:-1]) + seg[-1] if len(seg) > 2 else sum(seg)


class TableWidthTests(SimpleTestCase):
    def test_no_wide_tables(self):
        wide = []
        for f in glob.glob("apps/*/templates/**/*.html", recursive=True):
            if f.rsplit("/", 1)[-1] in EXEMPT: continue
            for m in re.finditer(r"<thead>(.*?)</thead>", open(f, encoding="utf-8").read(), re.S):
                n = columns(m.group(1))
                if n > MAX_COLS: wide.append((f, n))
        self.assertEqual(wide, [], "Tabel terlalu lebar; tumpuk data sekunder sebagai <small> atau pindahkan ke detail")
