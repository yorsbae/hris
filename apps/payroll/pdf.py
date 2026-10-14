"""Slip gaji PDF format struk (lebar 80 mm, tinggi mengikuti isi) — meniru slip kertas yang sudah dipakai karyawan. Satu halaman per slip; `slips_pdf` menggabungkan banyak slip."""
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from apps.core import letterhead
from apps.core.money import format_rupiah
from . import calc

W = 80 * mm; LM = 5 * mm; RM = W - 5 * mm; LH = 4.3 * mm
SECTION_TITLES = {"pokok": "I. Gaji Pokok", "tunjangan": "II. Tunjangan", "premi": "III. Premi Produksi", "lembur": "IV. Lembur", "makan": "V. Uang Makan", "lain": "VI. Lain-lain", "potongan": "VII. Potongan"}


def _rows(slip):
    """Daftar baris tercetak: ("title"|"head"|"line"|"sum"|"total"|"gap", ...)."""
    items = list(slip.items.all()); by = {}
    for i in items: by.setdefault(i.section, []).append(i)
    rows = [("head", "Nama", slip.emp_name), ("head", "NIK", slip.nik), ("head", "Jabatan", slip.position_name or "-"), ("head", "Departemen", slip.department_name),
            ("head", "Grade", slip.grade_label or "-"), ("head", "Bln masuk", slip.join_date.strftime("%b-%y")), ("head", "Masa kerja", f"{slip.service_years} th"),
            ("head", "Pddkan", slip.education or "-"), ("head", "Periode", calc.period_label(slip.run.period)), ("gap",)]
    for sec in ("pokok", "tunjangan", "premi", "lembur", "makan", "lain"):
        if not by.get(sec): continue
        rows.append(("title", SECTION_TITLES[sec]))
        for i in by[sec]:
            extra = ""
            if i.qty is not None and i.rate is not None: extra = f"{calc.fmt_qty(i.qty, 1 if sec in ('pokok',) else 2 if sec == 'lembur' else 0)} x {format_rupiah(i.rate, symbol=False)}"
            elif i.qty is not None: extra = calc.fmt_qty(i.qty, 0)
            rows.append(("line", i.label, extra, format_rupiah(i.amount, symbol=False)))
        rows.append(("sum", "Jumlah", format_rupiah(sum(x.amount for x in by[sec]), symbol=False)))
    rows.append(("total", "Total pendapatan", format_rupiah(slip.total_earnings, symbol=False)))
    if by.get("potongan"):
        rows.append(("gap",)); rows.append(("title", SECTION_TITLES["potongan"]))
        for i in by["potongan"]: rows.append(("line", i.label, calc.fmt_qty(i.qty, 0) if i.qty is not None else "", format_rupiah(i.amount, symbol=False)))
        rows.append(("sum", "Jumlah potongan", format_rupiah(slip.total_deductions, symbol=False)))
    rows.append(("gap",)); rows.append(("net", "Gaji yang diterima", format_rupiah(slip.net_pay, symbol=False)))
    return rows


def _height(rows): return (28 * mm + sum(LH * (1.0 if r[0] in ("head", "line", "sum", "total") else 1.35 if r[0] in ("title", "net") else 0.6) for r in rows) + 16 * mm)


def draw_slip(c, slip):
    rows = _rows(slip); H = _height(rows); c.setPageSize((W, H)); y = H - 8 * mm
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(W / 2, y, "SLIP GAJI HARIAN"); y -= 4.4 * mm
    c.setFont("Helvetica", 7); c.drawCentredString(W / 2, y, letterhead.info()["company"][:48]); y -= 3.2 * mm
    c.setLineWidth(0.5); c.line(LM, y, RM, y); y -= 4 * mm
    for r in rows:
        k = r[0]
        if k == "gap": y -= LH * 0.6; continue
        if k == "head": c.setFont("Helvetica", 7.5); c.drawString(LM, y, r[1]); c.drawString(LM + 21 * mm, y, f": {r[2]}"[:36])
        elif k == "title": c.setFont("Helvetica-Bold", 8); c.drawString(LM, y, r[1]); y -= LH * 0.35
        elif k == "line":
            c.setFont("Helvetica", 7.5); c.drawString(LM + 2 * mm, y, r[1][:24])
            if r[2]: c.setFont("Helvetica", 6.5); c.drawRightString(RM - 22 * mm, y, r[2][:22]); c.setFont("Helvetica", 7.5)
            c.drawRightString(RM, y, r[3])
        elif k == "sum": c.setLineWidth(0.3); c.line(RM - 24 * mm, y + LH * 0.75, RM, y + LH * 0.75); c.setFont("Helvetica-Oblique", 7.5); c.drawString(LM + 2 * mm, y, r[1]); c.drawRightString(RM, y, r[2])
        elif k == "total": c.setFont("Helvetica-Bold", 8); c.drawString(LM, y, r[1]); c.drawRightString(RM, y, r[2])
        elif k == "net":
            c.setLineWidth(0.8); c.line(LM, y + LH * 0.9, RM, y + LH * 0.9); c.setFont("Helvetica-Bold", 9); c.drawString(LM, y - 1 * mm, r[1]); c.drawRightString(RM, y - 1 * mm, "Rp " + r[2]); y -= 1 * mm
        y -= LH * (1.0 if k in ("head", "line", "sum", "total") else 1.0)
    c.setFont("Helvetica-Oblique", 6); c.drawCentredString(W / 2, 5 * mm, "Dokumen rahasia — hanya untuk yang bersangkutan"); c.showPage()


def slips_pdf(slips):
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=(W, 200 * mm))
    for s in slips: draw_slip(c, s)
    c.save(); return buf.getvalue()
