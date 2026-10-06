"""PDF A4 portrait, isi hanya separuh atas halaman (±148,5 mm); separuh bawah kosong/garis potong."""
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

def sick_leave_pdf(letter, company="PT ........"):
    rec, emp = letter.record, letter.record.employee
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; top = H - 15 * mm; L = 20 * mm
    c.setFont("Helvetica-Bold", 14); c.drawCentredString(W / 2, top, company)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 6 * mm, "POLIKLINIK PERUSAHAAN")
    c.line(L, top - 9 * mm, W - L, top - 9 * mm)
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(W / 2, top - 17 * mm, "SURAT IZIN PULANG")
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 22 * mm, f"No. {letter.number}")
    rows = [("Nama", emp.name), ("NIK", emp.nik), ("Departemen", emp.department.name),
            ("Tanggal", rec.visit_at.astimezone().strftime("%d-%m-%Y %H:%M"))]
    y = top - 33 * mm
    for k, v in rows:
        c.drawString(L, y, k); c.drawString(L + 35 * mm, y, f": {v}"); y -= 6 * mm
    c.drawString(L, y - 2 * mm, "Berdasarkan pemeriksaan, karyawan tersebut disarankan untuk pulang / beristirahat.")
    y -= 40 * mm  # ruang tanda tangan
    c.drawString(W - L - 55 * mm, y + 20 * mm, "Petugas Poliklinik,")
    c.drawString(W - L - 55 * mm, y, f"( {rec.created_by.get_full_name() or rec.created_by.username} )")
    # garis potong tepat di tengah halaman
    c.setDash(3, 3); c.line(10 * mm, H / 2, W - 10 * mm, H / 2); c.setDash()
    c.showPage(); c.save()
    return buf.getvalue()
