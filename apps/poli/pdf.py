"""PDF A4 portrait, isi hanya separuh atas halaman (±148,5 mm); separuh bawah kosong/garis potong."""
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

def _fmt(d): return d.strftime("%d-%m-%Y") if d else "-"


def _body(letter):
    """(judul, [baris isi]) menurut jenis surat. Tidak pernah memuat diagnosa; surat izin hamil hanya memuat keterangan kehamilan (HPL/usia) yang memang perlu untuk izin."""
    rec = letter.record
    if letter.kind == "izin_libur":
        return "SURAT IZIN LIBUR / ISTIRAHAT", [f"Berdasarkan pemeriksaan, karyawan tersebut memerlukan istirahat dan tidak dapat bekerja selama {letter.days} hari,",
                                                f"yaitu tanggal {_fmt(letter.start_date)} s/d {_fmt(letter.end_date)}."]
    if letter.kind == "izin_hamil":
        preg = (rec.exam or {}).get("kehamilan", {}) if isinstance(rec.exam, dict) else {}
        info = []
        if preg.get("usia_minggu"): info.append(f"usia kehamilan {preg['usia_minggu']} minggu")
        if preg.get("hpl"): info.append(f"HPL {preg['hpl'][8:10]}-{preg['hpl'][5:7]}-{preg['hpl'][0:4]}")
        purpose = dict(letter.PURPOSES).get(letter.purpose, "")
        lines = ["Karyawan tersebut dalam keadaan hamil" + (f" ({', '.join(info)})" if info else "") + ".",
                 f"Keperluan: {purpose}.", f"Berlaku {letter.days} hari, tanggal {_fmt(letter.start_date)} s/d {_fmt(letter.end_date)}."]
        return "SURAT IZIN HAMIL", lines
    return "SURAT IZIN PULANG", ["Berdasarkan pemeriksaan, karyawan tersebut disarankan untuk pulang / beristirahat."]


def sick_leave_pdf(letter, company="PT ........"):
    """Surat Poli menurut jenis (izin pulang / izin libur / izin hamil): A4 portrait, isi separuh atas, garis potong tengah."""
    rec, emp = letter.record, letter.record.employee
    title, lines = _body(letter)
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; top = H - 15 * mm; L = 20 * mm
    c.setFont("Helvetica-Bold", 14); c.drawCentredString(W / 2, top, company)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 6 * mm, "POLIKLINIK PERUSAHAAN")
    c.line(L, top - 9 * mm, W - L, top - 9 * mm)
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(W / 2, top - 17 * mm, title)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 22 * mm, f"No. {letter.number}")
    rows = [("Nama", emp.name), ("NIK", emp.nik), ("Departemen", emp.department.name),
            ("Tanggal", rec.visit_at.astimezone().strftime("%d-%m-%Y %H:%M"))]
    y = top - 33 * mm
    for k, v in rows:
        c.drawString(L, y, k); c.drawString(L + 35 * mm, y, f": {v}"); y -= 6 * mm
    y -= 2 * mm
    for ln in lines:
        for part in simpleSplit(ln, "Helvetica", 10, W - 2 * L):
            c.drawString(L, y, part); y -= 5 * mm
    y = min(y, top - 70 * mm) - 12 * mm  # ruang tanda tangan
    c.drawString(W - L - 55 * mm, y + 20 * mm, "Petugas Poliklinik,")
    c.drawString(W - L - 55 * mm, y, f"( {rec.created_by.get_full_name() or rec.created_by.username} )")
    # garis potong tepat di tengah halaman
    c.setDash(3, 3); c.line(10 * mm, H / 2, W - 10 * mm, H / 2); c.setDash()
    c.showPage(); c.save()
    return buf.getvalue()


def referral_pdf(ref, company="PT ........"):
    """Surat rujukan: A4 portrait, isi separuh atas, garis potong tengah (VISION → Pencetakan). Tanpa diagnosa/isi medis."""
    rec, emp = ref.record, ref.record.employee
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; top = H - 15 * mm; L = 20 * mm
    c.setFont("Helvetica-Bold", 14); c.drawCentredString(W / 2, top, company)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 6 * mm, "POLIKLINIK PERUSAHAAN")
    c.line(L, top - 9 * mm, W - L, top - 9 * mm)
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(W / 2, top - 17 * mm, "SURAT RUJUKAN")
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, top - 22 * mm, f"No. {ref.number}")
    rows = [("Nama", emp.name), ("NIK", emp.nik), ("Departemen", emp.department.name), ("Tujuan rujukan", ref.facility),
            ("Tanggal", (ref.created_at or rec.visit_at).astimezone().strftime("%d-%m-%Y"))]
    y = top - 33 * mm
    for k, v in rows:
        c.drawString(L, y, k); c.drawString(L + 35 * mm, y, f": {v}"); y -= 6 * mm
    c.drawString(L, y - 2 * mm, "Mohon pemeriksaan dan penanganan lebih lanjut bagi karyawan tersebut.")
    y -= 34 * mm
    c.drawString(W - L - 55 * mm, y + 20 * mm, "Petugas Poliklinik,")
    c.drawString(W - L - 55 * mm, y, f"( {(ref.created_by or rec.created_by).get_full_name() or (ref.created_by or rec.created_by).username} )")
    c.setDash(3, 3); c.line(10 * mm, H / 2, W - 10 * mm, H / 2); c.setDash()
    c.showPage(); c.save()
    return buf.getvalue()
