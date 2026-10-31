"""Kop surat: judul mengikut JENIS cetakan (company / hrd / poli), logo asli untuk perusahaan & HRD."""
import io
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, override_settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from apps.core import kop, letterhead

CFG = dict(COMPANY_NAME="PT Maju Jaya", COMPANY_TAGLINE="EXPORT - IMPORT - MANUFACTURE", POLI_NAME="", HRD_KOP_UNIT="DEPARTEMEN HRD", POLI_TAGLINE="PELAYANAN KESEHATAN KARYAWAN")


@override_settings(**CFG)
class KopHeadingTests(SimpleTestCase):
    def test_each_kind_has_its_own_title(self):
        lh = letterhead.info()
        self.assertEqual(letterhead.heading("company", lh)[:2], ("PT Maju Jaya", "EXPORT - IMPORT - MANUFACTURE"))
        self.assertEqual(letterhead.heading("hrd", lh)[:2], ("PT Maju Jaya", "DEPARTEMEN HRD"))
        self.assertEqual(letterhead.heading("poli", lh)[:2], ("POLIKLINIK PT Maju Jaya", "PELAYANAN KESEHATAN KARYAWAN"))
        with self.assertRaises(ValueError): letterhead.heading("lain", lh)

    def test_poli_and_hrd_never_carry_the_trade_line(self):
        lh = letterhead.info()
        for k in ("hrd", "poli"): self.assertNotIn("EXPORT", " ".join(letterhead.heading(k, lh)))

    @override_settings(POLI_NAME="Klinik Maju Jaya")
    def test_poli_name_without_company_shows_company_line(self):
        self.assertEqual(letterhead.heading("poli", letterhead.info())[:2], ("Klinik Maju Jaya", "PT Maju Jaya"))

    def test_pdf_company_kop_embeds_the_real_logo_image(self):
        def size(kind):
            b = io.BytesIO(); c = canvas.Canvas(b, pagesize=A4); kop.draw(c, A4[0], A4[1] - 15 * mm, 20 * mm, letterhead.info(), kind); c.save(); return b.getvalue()
        self.assertIn(b"/Subtype /Image", size("company")); self.assertIn(b"/Subtype /Image", size("hrd")); self.assertNotIn(b"/Subtype /Image", size("poli"))

    def test_html_block_follows_kind(self):
        req = RequestFactory().get("/")
        def html(kind, extra=""): return Template('{% load kop %}{% kop_block "' + kind + '"' + extra + ' %}').render(Context({"request": req}))
        self.assertIn("kop-logo.png", html("company")); self.assertIn("EXPORT - IMPORT", html("company"))
        p = html("poli"); self.assertIn("POLIKLINIK PT Maju Jaya", p); self.assertNotIn("EXPORT", p); self.assertNotIn("kop-logo.png", p)
        self.assertIn("DEPARTEMEN HRD", html("hrd", " print_only=True")); self.assertIn("@media print", html("hrd", " print_only=True"))
