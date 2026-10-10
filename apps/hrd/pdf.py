"""PDF Surat Peringatan (A4 portrait). Kop & penanda tangan dari apps.core.letterhead."""
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from apps.core import letterhead

MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def _id(d): return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def warning_pdf(w):
    lh = letterhead.info(); emp = w.employee
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4); W, H = A4; L = 22 * mm; R = W - 22 * mm; y = H - 20 * mm
    c.setFont("Helvetica-Bold", 15); c.drawCentredString(W / 2, y, lh["company"]); y -= 6 * mm
    if lh["address"]:
        c.setFont("Helvetica", 9); c.drawCentredString(W / 2, y, lh["address"]); y -= 5 * mm
    c.setLineWidth(1.2); c.line(L, y, R, y); c.setLineWidth(0.4); c.line(L, y - 1.2 * mm, R, y - 1.2 * mm); y -= 12 * mm
    c.setFont("Helvetica-Bold", 13); title = f"SURAT PERINGATAN {w.level}"; c.drawCentredString(W / 2, y, title)
    tw = c.stringWidth(title, "Helvetica-Bold", 13); c.line(W / 2 - tw / 2, y - 1 * mm, W / 2 + tw / 2, y - 1 * mm); y -= 6 * mm
    c.setFont("Helvetica", 10.5); c.drawCentredString(W / 2, y, f"Nomor: {w.number}"); y -= 12 * mm
    c.drawString(L, y, "Diberikan kepada:"); y -= 7 * mm
    for k, v in (("Nama", emp.name), ("NIK", emp.nik), ("Jabatan", emp.position.name), ("Departemen", emp.department.name)):
        c.drawString(L + 6 * mm, y, k); c.drawString(L + 40 * mm, y, f": {v}"); y -= 6 * mm
    y -= 4 * mm
    text = [f"Sehubungan dengan pelanggaran berupa: {w.violation}.", *( [w.description] if w.description else [] ),
            f"Maka perusahaan memberikan Surat Peringatan {w.level} (SP {w.level}) yang berlaku sejak {_id(w.issue_date)} sampai dengan {_id(w.valid_until)}.",
            "Apabila dalam masa berlaku surat ini yang bersangkutan mengulangi pelanggaran atau melakukan pelanggaran lain, perusahaan akan mengambil tindakan sesuai peraturan perusahaan dan ketentuan perundang-undangan yang berlaku.",
            "Demikian surat peringatan ini dibuat untuk diperhatikan dan dilaksanakan sebagaimana mestinya."]
    for para in text:
        for line in simpleSplit(para, "Helvetica", 10.5, R - L):
            c.drawString(L, y, line); y -= 5.4 * mm
        y -= 3 * mm
    y -= 6 * mm; c.drawString(R - 60 * mm, y, f"{_id(w.issue_date)}"); y -= 5.5 * mm
    c.drawString(R - 60 * mm, y, "Hormat kami,"); y -= 24 * mm
    c.setFont("Helvetica-Bold", 10.5); name = lh["hrd_name"] or "( ........................ )"
    c.drawString(R - 60 * mm, y, name); c.setFont("Helvetica", 10.5); c.drawString(R - 60 * mm, y - 5 * mm, lh["hrd_title"])
    y -= 22 * mm; c.drawString(L, y, "Penerima,"); c.drawString(L, y - 24 * mm, f"( {emp.name} )")
    c.showPage(); c.save(); return buf.getvalue()


def paklaring_pdf(s):
    """Surat Keterangan Kerja (paklaring), A4 portrait. Isi dari SALINAN identitas saat dicatat (bukan data karyawan sekarang). Tidak memuat tali asih atau alasan keluar rinci."""
    from .services import SEPARATION_PHRASE, service_period
    lh = letterhead.info()
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4); W, H = A4; L = 22 * mm; R = W - 22 * mm; y = H - 20 * mm
    c.setFont("Helvetica-Bold", 15); c.drawCentredString(W / 2, y, lh["company"]); y -= 6 * mm
    if lh["address"]:
        c.setFont("Helvetica", 9); c.drawCentredString(W / 2, y, lh["address"]); y -= 5 * mm
    c.setLineWidth(1.2); c.line(L, y, R, y); c.setLineWidth(0.4); c.line(L, y - 1.2 * mm, R, y - 1.2 * mm); y -= 12 * mm
    c.setFont("Helvetica-Bold", 13); title = "SURAT KETERANGAN KERJA"; c.drawCentredString(W / 2, y, title)
    tw = c.stringWidth(title, "Helvetica-Bold", 13); c.line(W / 2 - tw / 2, y - 1 * mm, W / 2 + tw / 2, y - 1 * mm); y -= 6 * mm
    c.setFont("Helvetica", 10.5); c.drawCentredString(W / 2, y, f"Nomor: {s.paklaring_number}"); y -= 12 * mm
    c.drawString(L, y, "Yang bertanda tangan di bawah ini menerangkan bahwa:"); y -= 8 * mm
    for k, v in (("Nama", s.emp_name), ("NIK", s.employee.nik), ("Jabatan terakhir", s.position_name or "-"), ("Departemen", s.department_name)):
        c.drawString(L + 6 * mm, y, k); c.drawString(L + 46 * mm, y, f": {v}"); y -= 6 * mm
    y -= 4 * mm
    text = [f"Benar telah bekerja di {lh['company']} sejak {_id(s.join_date)} sampai dengan {_id(s.last_date)} (masa kerja {service_period(s.join_date, s.last_date)}), {SEPARATION_PHRASE.get(s.kind, SEPARATION_PHRASE['lainnya'])}.",
            "Demikian surat keterangan kerja ini dibuat untuk dipergunakan sebagaimana mestinya."]
    for para in text:
        for line in simpleSplit(para, "Helvetica", 10.5, R - L):
            c.drawString(L, y, line); y -= 5.4 * mm
        y -= 3 * mm
    y -= 6 * mm; c.drawString(R - 60 * mm, y, f"{_id(s.paklaring_date)}"); y -= 5.5 * mm
    c.drawString(R - 60 * mm, y, "Hormat kami,"); y -= 24 * mm
    c.setFont("Helvetica-Bold", 10.5); name = lh["hrd_name"] or "( ........................ )"
    c.drawString(R - 60 * mm, y, name); c.setFont("Helvetica", 10.5); c.drawString(R - 60 * mm, y - 5 * mm, lh["hrd_title"])
    c.showPage(); c.save(); return buf.getvalue()
