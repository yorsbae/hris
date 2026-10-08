"""Putaran 21: P2 format rupiah (A43) & P3 tema ikon."""
import re
from decimal import Decimal
from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.money import RupiahField, XLSX_RUPIAH_FORMAT, format_rupiah, parse_rupiah
from apps.hrd.models import Aid, Project, ProjectWork
from apps.hrd.test_base import HrdBase


class RupiahFormatTests(SimpleTestCase):
    def test_format_no_trailing_zero_decimals(self):
        for v, out in ((1500000, "Rp 1.500.000"), (0, "Rp 0"), (None, "Rp 0"), ("", "Rp 0"), (999, "Rp 999"), (1000, "Rp 1.000"),
                       (Decimal("1500000.00"), "Rp 1.500.000"), (Decimal("19000"), "Rp 19.000"), (17000.0, "Rp 17.000"), (-2500000, "-Rp 2.500.000")):
            self.assertEqual(format_rupiah(v), out, v)
            self.assertNotIn(",00", format_rupiah(v))

    def test_decimal_only_when_present(self):
        self.assertEqual(format_rupiah(Decimal("1500000.5")), "Rp 1.500.000,5")
        self.assertEqual(format_rupiah(Decimal("1500000.25")), "Rp 1.500.000,25")
        self.assertEqual(format_rupiah(Decimal("0.004")), "Rp 0")  # dibulatkan ke sen

    def test_plain_without_symbol(self): self.assertEqual(format_rupiah(1500000, symbol=False), "1.500.000")

    def test_parse_accepts_common_inputs(self):
        for t in ("1500000", "1.500.000", "Rp 1.500.000", "rp. 1500000", " Rp1.500.000 ", "1.500.000,00", "1.500.000,0", "0"):
            self.assertEqual(parse_rupiah(t), 0 if t == "0" else 1500000, t)

    def test_parse_rejects_ambiguous_or_bad(self):
        for t in ("", "abc", "1.5", "1,500,000", "1.500.00", "1..500", "1.500.000,50", "1.500.000,123", "-5", "12e3", "Rp", "1 500 x"):
            with self.assertRaises(ValueError, msg=t): parse_rupiah(t)
        self.assertEqual(parse_rupiah("-5", allow_negative=True), -5)
        self.assertEqual(parse_rupiah("1.500.000,50", allow_decimal=True), Decimal("1500000.50"))

    def test_xlsx_format_constant(self): self.assertIn("#,##0", XLSX_RUPIAH_FORMAT); self.assertNotIn("0.00", XLSX_RUPIAH_FORMAT)

    def test_roundtrip(self):
        for n in (0, 7, 999, 1000, 19000, 123456789, 2_000_000_000): self.assertEqual(parse_rupiah(format_rupiah(n)), n)


class RupiahFieldTests(SimpleTestCase):
    class F(forms.Form): amt = RupiahField(label="x", min_value=1, max_value=2_000_000_000)

    def test_valid_and_redisplay(self):
        f = self.F({"amt": "Rp 1.500.000"}); self.assertTrue(f.is_valid()); self.assertEqual(f.cleaned_data["amt"], 1500000)
        self.assertEqual(self.F(initial={"amt": 1500000})["amt"].value(), "1.500.000")  # tanpa ,00 dan tanpa Rp
        self.assertIn('value="1.500.000"', str(self.F(initial={"amt": 1500000})["amt"]))

    def test_invalid_and_limits(self):
        for bad in ("abc", "0", "-5", "1.5", "1.500.000,50", "2.000.000.001", ""):
            self.assertFalse(self.F({"amt": bad}).is_valid(), bad)
        self.assertFalse(self.F({"amt": "1.5"}).is_valid())

    def test_bound_invalid_value_kept_as_typed(self): self.assertEqual(self.F({"amt": "1.5"})["amt"].value(), "1.5")


class RupiahFilterTests(SimpleTestCase):
    def render(self, tpl, **ctx): return Template("{% load money %}" + tpl).render(Context(ctx))

    def test_filters(self):
        self.assertEqual(self.render("{{ x|rupiah }}", x=1500000), "Rp 1.500.000")
        self.assertEqual(self.render("{{ x|rupiah }}", x=None), "Rp 0")
        self.assertEqual(self.render("{{ x|rupiah_plain }}", x=19000), "19.000")
        self.assertEqual(self.render("{{ x|rupiah }}", x="bukan angka"), "bukan angka")  # tidak 500


class RupiahPagesTests(HrdBase):
    def setUp(self): self.login()

    def test_aid_list_shows_dotted_rupiah_no_comma_zero(self):
        self.client.post(reverse("hrd_aid_new"), {"nik": "001", "kind": "kematian", "event_date": self.today().isoformat(), "amount": "Rp 1.500.000", "description": ""})
        self.assertEqual(Aid.objects.get().amount, 1500000)
        html = self.client.get(reverse("hrd_aids")).content.decode()
        self.assertIn("Rp 1.500.000", html); self.assertNotRegex(html, r"Rp 1\.500\.000,00"); self.assertNotRegex(html, r"Rp \d{4,}")  # tidak ada angka polos tanpa titik

    def test_aid_edit_form_prefilled_dotted(self):
        self.client.post(reverse("hrd_aid_new"), {"nik": "001", "kind": "kematian", "event_date": self.today().isoformat(), "amount": 2500000, "description": ""})
        a = Aid.objects.get(); html = self.client.get(reverse("hrd_aid_edit", args=[a.pk])).content.decode()
        self.assertIn('value="2.500.000"', html); self.assertIn("data-rupiah", html)

    def test_aid_rejects_cents_and_garbage(self):
        for bad in ("1.500.000,50", "1.5", "satu juta"):
            r = self.client.post(reverse("hrd_aid_new"), {"nik": "001", "kind": "kematian", "event_date": self.today().isoformat(), "amount": bad, "description": ""})
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(Aid.objects.count(), 0)


class ThemeToggleTests(TestCase):
    def test_login_has_floating_toggle_and_early_script(self):
        html = self.client.get("/login/").content.decode()
        self.assertIn('id="tt"', html); self.assertIn("tt float", html)
        self.assertIn('aria-pressed="false"', html); self.assertIn('aria-label="Mode gelap"', html)
        self.assertLess(html.index('localStorage.getItem("theme")'), html.index("<style>"))  # diterapkan sebelum render → tanpa kedip

    def test_css_supports_forced_light_forced_dark_and_print(self):
        html = self.client.get("/login/").content.decode()
        self.assertIn(":root:not([data-theme=light])", html); self.assertIn("html[data-theme=dark]{", html)
        self.assertRegex(html, r"@media print\{:root,html\[data-theme=dark\]\{--bg:#fff")  # cetak selalu terang
        dark = re.search(r"@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-theme=light\]\)\{(.*?)\}\}", html).group(1)
        self.assertIn(dark, html.split("html[data-theme=dark]{", 1)[1])  # variabel gelap identik di kedua jalur

    def test_icons_defined(self):
        html = self.client.get("/login/").content.decode(); self.assertIn('id="i-moon"', html); self.assertIn('id="i-sun"', html)

    def test_logged_in_header_has_button(self):
        from apps.core.models import User, Role
        u = User.objects.create_user("t1", password="x-Sandi-Kuat-123", role=Role.SUPERADMIN); self.client.force_login(u)
        html = self.client.get("/").content.decode(); self.assertIn('class="tt"', html); self.assertNotIn("tt float", html)
