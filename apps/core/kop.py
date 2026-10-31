"""Kop surat bergambar (logo + nama + baris usaha + alamat + garis ganda biru) untuk semua surat cetak HRD & Poliklinik.

Tata letak mengikuti kop asli PT. Sukorejo Indah Textile: logo bintang-matahari di kiri, nama perusahaan serif tebal biru,
baris usaha sans tebal biru, alamat serif tebal hitam, lalu garis biru tebal + tipis. Logo digambar vektor (tanpa berkas gambar).
Poliklinik memakai tata letak yang sama dengan logo sendiri (lingkaran + palang medis) dan nama poli.
"""
import math
from pathlib import Path
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import Color, white, black
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

LOGO_FILE = Path(__file__).parent / "static" / "core" / "kop-logo.png"   # logo SUKORINTEX asli (bintang-matahari + pita nama)
LOGO_W, LOGO_H = 24 * mm, 24 * mm * 107 / 151                          # rasio gambar 151×107
NAVY = Color(0.09, 0.24, 0.56)     # teks & garis kop
LOGO_DARK = Color(0.06, 0.18, 0.46)
LOGO_BLUE = Color(0.13, 0.44, 0.88)


def _fit(text, font, size, maxw, min_size=5):
    while size > min_size and stringWidth(text, font, size) > maxw: size -= 0.25
    return size


def _banner(c, cx, cy, w, h, label):
    c.setFillColor(white); c.setStrokeColor(LOGO_DARK); c.setLineWidth(0.8); c.rect(cx - w / 2, cy - h / 2, w, h, fill=1, stroke=1)
    size = _fit(label, "Helvetica-Bold", 6.2, w - 1.6 * mm, 3); c.setFillColor(LOGO_DARK); c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, cy - size * 0.35, label)


def logo_company(c, cx, cy, r, label="SUKORINTEX"):
    """Bintang-matahari 24 sinar + cakram biru + pita nama (logo perusahaan)."""
    n = 24; pts = []
    for i in range(n * 2):
        a = math.pi * i / n - math.pi / 2; rad = r if i % 2 == 0 else r * 0.74
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    p = c.beginPath(); p.moveTo(*pts[0])
    for pt in pts[1:]: p.lineTo(*pt)
    p.close(); c.setFillColor(LOGO_DARK); c.setStrokeColor(LOGO_DARK); c.drawPath(p, fill=1, stroke=0)
    c.setFillColor(white); c.circle(cx, cy, r * 0.70, fill=1, stroke=0)
    c.setFillColor(LOGO_BLUE); c.circle(cx, cy, r * 0.64, fill=1, stroke=0)
    _banner(c, cx, cy, r * 1.78, r * 0.34, label)


def logo_poli(c, cx, cy, r, label="POLIKLINIK"):
    """Logo Poliklinik: cincin ganda biru, palang medis putih di cakram biru, pita nama."""
    c.setFillColor(LOGO_DARK); c.circle(cx, cy, r, fill=1, stroke=0)
    c.setFillColor(white); c.circle(cx, cy, r * 0.88, fill=1, stroke=0)
    c.setFillColor(LOGO_BLUE); c.circle(cx, cy, r * 0.80, fill=1, stroke=0)
    arm, th = r * 0.52, r * 0.20; c.setFillColor(white)
    c.rect(cx - th, cy - arm + r * 0.08, 2 * th, 2 * arm, fill=1, stroke=0); c.rect(cx - arm, cy - th + r * 0.08, 2 * arm, 2 * th, fill=1, stroke=0)
    _banner(c, cx, cy - r * 0.02, r * 1.78, r * 0.34, label)


def logo_image(c, cx, cy):
    """Logo asli dari berkas; False bila berkas tidak ada (→ pakai logo vektor pengganti)."""
    try: img = ImageReader(str(LOGO_FILE))
    except Exception: return False
    c.drawImage(img, cx - LOGO_W / 2, cy - LOGO_H / 2, LOGO_W, LOGO_H, mask="auto"); return True


def draw(c, W, top, L, lh, kind="company"):
    """Gambar kop; `top` = batas atas konten (titik tertinggi logo). Mengembalikan y garis bawah kop (semua isi surat diukur dari sana).
    kind: "company" (surat resmi), "hrd" (cetakan internal HRD), "poli" (Poliklinik) — judul menurut letterhead.heading; logo asli untuk company/hrd, logo Poliklinik untuk poli."""
    from .letterhead import heading
    r = 11 * mm; cy = top - r; name, tag, addr = heading(kind, lh); lw = 2 * r
    if kind == "poli": logo_poli(c, L + r, cy, r)
    elif logo_image(c, L + LOGO_W / 2, cy): lw = LOGO_W
    else: logo_company(c, L + r, cy, r)
    x0 = L + lw + 3 * mm; x1 = W - L; mid = (x0 + x1) / 2; maxw = x1 - x0
    c.setFillColor(NAVY); s = _fit(name, "Times-Bold", 25, maxw, 10); c.setFont("Times-Bold", s)
    y = top - 8.5 * mm; c.drawCentredString(mid, y, name)
    if tag:
        s = _fit(tag, "Helvetica-Bold", 11.5, maxw, 6); c.setFont("Helvetica-Bold", s); y -= 6.3 * mm; c.drawCentredString(mid, y, tag)
    c.setFillColor(black)
    if addr:
        s = _fit(addr, "Times-Bold", 8.5, maxw, 5); c.setFont("Times-Bold", s); y -= 5 * mm; c.drawCentredString(mid, y, addr)
    y = min(y, cy - r + 1.5 * mm) - 2.2 * mm
    c.setStrokeColor(NAVY); c.setLineWidth(1.6); c.line(L, y, W - L, y); c.setLineWidth(0.6); c.line(L, y - 1.3 * mm, W - L, y - 1.3 * mm)
    c.setStrokeColor(black); c.setFillColor(black); c.setLineWidth(1)
    return y - 1.3 * mm
