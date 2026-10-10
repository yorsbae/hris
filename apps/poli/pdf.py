"""PDF surat Poliklinik: A4 portrait, isi hanya separuh atas halaman (±148,5 mm); separuh bawah kosong/garis potong.
Kop (perusahaan, alamat, nama poli) dan penanda tangan dibaca dari apps.core.letterhead (.env) — tidak ditanam di kode (putaran 34)."""
from io import BytesIO
from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from apps.core import letterhead

def _fmt(d): return d.strftime("%d-%m-%Y") if d else "-"


def _kop(c, W, top, L, lh):
    """Kop surat (perusahaan, alamat bila diisi, nama poli) + garis bawah. Mengembalikan y garis kop; semua bagian lain surat diukur dari sana."""
    c.setFont("Helvetica-Bold", 14); c.drawCentredString(W / 2, top, lh["company"]); y = top - 6 * mm
    if lh["address"]:  # alamat panjang dibungkus (maks. 2 baris) agar tidak melewati margin
        c.setFont("Helvetica", 8.5)
        for part in simpleSplit(lh["address"], "Helvetica", 8.5, W - 2 * L)[:2]:
            c.drawCentredString(W / 2, y, part); y -= 4.5 * mm
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, y, lh["poli"])
    y -= 3 * mm; c.line(L, y, W - L, y)
    return y


def _title(c, W, y0, title, number):
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(W / 2, y0 - 8 * mm, title)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, y0 - 13 * mm, f"No. {number}")


def _signature(c, W, L, y, lh):
    """Blok tanda tangan kanan, dimulai di baris `y` (di bawah akhir isi surat, jadi tidak pernah menimpa isi): 'Dokter Perusahaan' bila nama dokter
    diatur di .env, selain itu 'Petugas Poliklinik' (pengguna Poli pembuat surat). Nama di bawah label dengan ruang 20 mm untuk tanda tangan."""
    role = "Dokter Perusahaan," if settings.POLI_DOCTOR_NAME else "Petugas Poliklinik,"
    c.setFont("Helvetica", 10); c.drawString(W - L - 55 * mm, y, role)
    c.drawString(W - L - 55 * mm, y - 20 * mm, f"( {lh['doctor'] or '........................'} )")


def _cut_line(c, W, H):
    c.setDash(3, 3); c.line(10 * mm, H / 2, W - 10 * mm, H / 2); c.setDash()


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


def sick_leave_pdf(letter):
    """Surat Poli menurut jenis (izin pulang / izin libur / izin hamil): A4 portrait, isi separuh atas, garis potong tengah."""
    rec, emp = letter.record, letter.record.employee
    lh = letterhead.info(rec.created_by)
    title, lines = _body(letter)
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; top = H - 15 * mm; L = 20 * mm
    y0 = _kop(c, W, top, L, lh); _title(c, W, y0, title, letter.number)
    c.setFont("Helvetica", 10)
    rows = [("Nama", emp.name), ("NIK", emp.nik), ("Departemen", emp.department.name),
            ("Tanggal", rec.visit_at.astimezone().strftime("%d-%m-%Y %H:%M"))]
    y = y0 - 24 * mm
    for k, v in rows:
        c.drawString(L, y, k); c.drawString(L + 35 * mm, y, f": {v}"); y -= 6 * mm
    y -= 2 * mm
    for ln in lines:
        for part in simpleSplit(ln, "Helvetica", 10, W - 2 * L):
            c.drawString(L, y, part); y -= 5 * mm
    _signature(c, W, L, min(y, y0 - 52 * mm) - 6 * mm, lh); _cut_line(c, W, H)  # mengikuti akhir isi → tidak bertabrakan dengan baris panjang
    c.showPage(); c.save()
    return buf.getvalue()


def referral_pdf(ref):
    """Surat rujukan: A4 portrait, isi separuh atas, garis potong tengah (VISION → Pencetakan). Tanpa diagnosa/isi medis."""
    rec, emp = ref.record, ref.record.employee
    lh = letterhead.info(ref.created_by or rec.created_by)
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4; top = H - 15 * mm; L = 20 * mm
    y0 = _kop(c, W, top, L, lh); _title(c, W, y0, "SURAT RUJUKAN", ref.number)
    c.setFont("Helvetica", 10)
    rows = [("Nama", emp.name), ("NIK", emp.nik), ("Departemen", emp.department.name), ("Tujuan rujukan", ref.facility),
            ("Tanggal", (ref.created_at or rec.visit_at).astimezone().strftime("%d-%m-%Y"))]
    y = y0 - 24 * mm
    for k, v in rows:
        c.drawString(L, y, k); c.drawString(L + 35 * mm, y, f": {v}"); y -= 6 * mm
    c.drawString(L, y - 2 * mm, "Mohon pemeriksaan dan penanganan lebih lanjut bagi karyawan tersebut.")
    _signature(c, W, L, y - 12 * mm, lh); _cut_line(c, W, H)
    c.showPage(); c.save()
    return buf.getvalue()
