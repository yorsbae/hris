"""PDF surat Poliklinik (A4 portrait). Kop (perusahaan, alamat, kota, nama poli) dan penanda tangan Personalia dibaca dari apps.core.letterhead (.env) — tidak ditanam di kode.

Putaran 47 — tata letak mengikuti formulir cetak Poli:
  • Surat Keterangan Istirahat / Cuti (pulang hari itu juga ATAU istirahat beberapa hari — satu surat) : isi separuh atas halaman + garis potong tengah
  • Surat Keterangan Cuti Hamil (usulan dokter + persetujuan Kabag Personalia) : satu halaman penuh
  • Surat Rujukan (data karyawan, keluhan, no. BPJS K/TK, tanggal kejadian) : satu halaman penuh
Penanda tangan dokter/pemeriksa = pemeriksa pada rekam medis saat surat dibuat (disalin ke surat; lihat services.record_signer). Surat izin tidak memuat diagnosa."""
from io import BytesIO
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from apps.core import kop, letterhead

MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
GENDER = {"L": "Laki-laki", "P": "Perempuan"}
_SATUAN = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def _fmt(d): return d.strftime("%d-%m-%Y") if d else "-"
def _id(d): return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def terbilang(n):
    """Bilangan bulat 0–999 dalam kata Indonesia (untuk '3 ( tiga ) hari')."""
    if not 0 <= n < 1000: raise ValueError("terbilang hanya untuk 0–999")
    if n < 12: return _SATUAN[n]
    if n < 20: return terbilang(n - 10) + " belas"
    if n < 100:
        t, s = divmod(n, 10); return terbilang(t) + " puluh" + (" " + terbilang(s) if s else "")
    if n < 200: return "seratus" + (" " + terbilang(n - 100) if n > 100 else "")
    h, r = divmod(n, 100); return terbilang(h) + " ratus" + (" " + terbilang(r) if r else "")


def _kop(c, W, top, L, lh):
    """Kop surat bergambar poli (logo poli, nama poli, alamat) + garis ganda. Mengembalikan y garis kop; semua bagian lain surat diukur dari sana."""
    return kop.draw(c, W, top, L, lh, kind="poli")


def _dateline(c, W, L, y, lh, d):
    """Baris 'Kota, 11 Oktober 2026' rata kanan (kota kosong → hanya tanggal)."""
    c.setFont("Helvetica", 10); c.drawRightString(W - L, y, f"{lh['city']}, {_id(d)}" if lh.get("city") else _id(d))


def _title(c, W, y, title, number):
    c.setFont("Helvetica-Bold", 12); c.drawCentredString(W / 2, y, title)
    tw = c.stringWidth(title, "Helvetica-Bold", 12); c.line(W / 2 - tw / 2, y - 1 * mm, W / 2 + tw / 2, y - 1 * mm)
    c.setFont("Helvetica", 10); c.drawCentredString(W / 2, y - 5.5 * mm, f"No. {number}")
    return y - 5.5 * mm


def _lines(c, x, y, text, width, size=10, font="Helvetica", step=5 * mm, limit=None):
    """Teks dibungkus per `width`; mengembalikan y berikutnya. `limit` = jumlah baris maksimum (sisanya diberi '…')."""
    c.setFont(font, size)
    parts = simpleSplit(text, font, size, width) or [""]
    if limit and len(parts) > limit: parts = parts[:limit]; parts[-1] = parts[-1].rstrip() + " …"
    for part in parts:
        c.drawString(x, y, part); y -= step
    return y


def _fields(c, W, L, y, rows, label_w=42 * mm, step=5.5 * mm, max_lines=3):
    """Baris 'label : nilai' (nilai panjang dibungkus, lebar mengikuti margin; lebih dari `max_lines` baris dipotong dengan '…'). Mengembalikan y berikutnya."""
    vw = W - 2 * L - label_w - 3 * mm
    for k, v in rows:
        c.setFont("Helvetica", 10); c.drawString(L, y, k); c.drawString(L + label_w, y, ":")
        parts = []
        for para in (str(v) if v not in (None, "") else "-").splitlines() or ["-"]: parts += simpleSplit(para, "Helvetica", 10, vw) or [""]
        if len(parts) > max_lines: parts = parts[:max_lines]; parts[-1] = parts[-1].rstrip() + " …"
        for i, part in enumerate(parts):
            c.drawString(L + label_w + 3 * mm, y, part); y -= step if i == len(parts) - 1 else 4.6 * mm
    return y


def _signature(c, x, y, role, name):
    """Blok tanda tangan mulai di baris `y` (di bawah akhir isi surat → tidak pernah menimpa isi); nama di bawah label dengan ruang 18 mm untuk tanda tangan. Mengembalikan y baris nama."""
    c.setFont("Helvetica", 10); c.drawString(x, y, role)
    c.drawString(x, y - 18 * mm, f"( {name or '........................'} )")
    return y - 18 * mm


def _signer(obj):
    """(label, nama) pemeriksa penanda tangan: salinan di surat/rujukan; surat lama (tanpa salinan) dihitung dari rekam medisnya."""
    if obj.signer_role or obj.signer_name: return obj.signer_role, obj.signer_name
    from .services import record_signer
    return record_signer(obj.record)


def _cut_line(c, W, H, y_last):
    """Garis potong tengah halaman — hanya bila seluruh isi masih di atasnya (isi panjang tidak boleh dipotong garis)."""
    if y_last > H / 2 + 4 * mm: c.setDash(3, 3); c.line(10 * mm, H / 2, W - 10 * mm, H / 2); c.setDash()


def _emp_rows(emp, on=None):
    age = emp.age(on)
    return [("Nama", emp.name), ("Umur", f"{age} tahun" if age is not None else "-"), ("Alamat", emp.address or "-"),
            ("Jabatan", emp.position.name if emp.position_id else "-"), ("NIK", emp.nik)]


def _issued(letter): return timezone.localtime(letter.issued_at).date() if letter.issued_at else timezone.localdate()


def _document(title_h=None):
    buf = BytesIO(); c = canvas.Canvas(buf, pagesize=A4); W, H = A4
    return buf, c, W, H, H - 15 * mm, 20 * mm


# ================================================================ Surat Keterangan Istirahat / Cuti
def _rest_period(letter):
    """(start, days, pulang) — surat lama: izin_pulang = hari terbit (1 hari), izin_libur = start_date/days."""
    if letter.kind == "izin_pulang" or letter.go_home: return (letter.start_date or _issued(letter)), 1, True
    return letter.start_date, letter.days, False


def _rest_pdf(letter):
    rec, emp = letter.record, letter.record.employee
    lh = letterhead.info(rec.created_by); role, name = _signer(letter)
    buf, c, W, H, top, L = _document()
    y0 = _kop(c, W, top, L, lh)
    _dateline(c, W, L, y0 - 6 * mm, lh, _issued(letter))
    yn = _title(c, W, y0 - 13 * mm, "SURAT KETERANGAN ISTIRAHAT / CUTI", letter.number)
    y = _lines(c, L, yn - 8 * mm, f"Yang bertanda tangan di bawah ini Dokter Perusahaan - {lh['company']} menerangkan bahwa:", W - 2 * L)
    y = _fields(c, W, L, y - 0.5 * mm, _emp_rows(emp, _issued(letter)))
    start, days, pulang = _rest_period(letter)
    if pulang:
        text = f"Sehubungan dengan sakitnya, yang bersangkutan diizinkan pulang pada hari itu juga, yaitu tanggal {_fmt(start)}."
    else:
        end = letter.end_date or start
        text = (f"Sehubungan dengan sakitnya, yang bersangkutan diberikan Cuti Istirahat selama : {days} ( {terbilang(days)} ) hari. "
                f"Terhitung mulai tanggal : {_fmt(start)} s.d. {_fmt(end)} tahun {end.year}.")
    y = _lines(c, L, y - 2 * mm, text, W - 2 * L)
    y_end = _signature(c, W - L - 62 * mm, y - 4 * mm, role, name)
    _cut_line(c, W, H, y_end)
    c.showPage(); c.save()
    return buf.getvalue()


# ================================================================ Surat Keterangan Cuti Hamil
def _hamil_pdf(letter):
    rec, emp = letter.record, letter.record.employee
    lh = letterhead.info(rec.created_by); role, name = _signer(letter)
    preg = (rec.exam or {}).get("kehamilan", {}) if isinstance(rec.exam, dict) else {}
    fmt_iso = lambda v: f"{v[8:10]}-{v[5:7]}-{v[0:4]}" if v else "-"
    buf, c, W, H, top, L = _document()
    y0 = _kop(c, W, top, L, lh)
    _dateline(c, W, L, y0 - 6 * mm, lh, _issued(letter))
    yn = _title(c, W, y0 - 13 * mm, "SURAT KETERANGAN CUTI HAMIL", letter.number)
    y = _lines(c, L, yn - 9 * mm, f"Yang bertanda tangan di bawah ini Dokter Perusahaan - {lh['company']} menerangkan bahwa:", W - 2 * L)
    y = _fields(c, W, L, y - 0.5 * mm, _emp_rows(emp, _issued(letter)) + [("HPHT", fmt_iso(preg.get("hpht"))), ("HPL", fmt_iso(preg.get("hpl")))])
    start = letter.start_date or _issued(letter)
    y = _lines(c, L, y - 3 * mm, f"Sehubungan dengan pemeriksaan usia kehamilan pasien yang bersangkutan, maka diusulkan untuk diberikan Cuti Hamil mulai tanggal {_id(start)}.", W - 2 * L)
    y = _signature(c, L, y - 4 * mm, role, name) - 9 * mm
    lama = (f"{letter.months} ( {terbilang(letter.months)} ) bulan" if letter.months else f"{letter.days} ( {terbilang(letter.days)} ) hari") if (letter.months or letter.days) else "- bulan"
    end = letter.end_date or start
    y = _lines(c, L, y, f"Menyetujui untuk diberikan cuti hamil selama : {lama} terhitung mulai : Tanggal {_fmt(start)} sampai dengan {_fmt(end)}", W - 2 * L, font="Helvetica-Bold")
    y = _lines(c, L, y - 3 * mm, "Demikian Surat Keterangan ini diberikan untuk digunakan sebagaimana mestinya.", W - 2 * L)
    pers = lh["pers_title"] or "Kabag Personalia"
    _signature(c, L, y - 4 * mm, f"{pers},", lh["pers_name"])
    c.showPage(); c.save()
    return buf.getvalue()


def sick_leave_pdf(letter):
    """Surat Poli menurut jenis: Surat Keterangan Istirahat / Cuti (juga surat izin pulang/libur lama), Surat Keterangan Cuti Hamil."""
    return _hamil_pdf(letter) if letter.kind == "izin_hamil" else _rest_pdf(letter)


# ================================================================ Surat Rujukan
def _checkbox(c, x, y, checked, label):
    c.setLineWidth(0.6); c.rect(x, y - 0.6 * mm, 3.2 * mm, 3.2 * mm)
    if checked: c.line(x + 0.5 * mm, y, x + 2.7 * mm, y + 2 * mm); c.line(x + 0.5 * mm, y + 2 * mm, x + 2.7 * mm, y)
    c.setLineWidth(1); c.setFont("Helvetica", 10); c.drawString(x + 5 * mm, y, label)
    return x + 5 * mm + c.stringWidth(label, "Helvetica", 10) + 6 * mm


def referral_pdf(ref):
    """Surat rujukan, satu halaman: jenis (kontrol/berobat/tes lab/rujukan spesialis), data karyawan (NIK karyawan + NIK KTP, putaran 52), no. BPJS K/TK, tanggal kejadian/keluhan, keluhan + catatan klinis,
    ditandatangani pemeriksa saat rujukan dibuat. TANPA diagnosa."""
    rec, emp = ref.record, ref.record.employee
    lh = letterhead.info(ref.created_by or rec.created_by); role, name = _signer(ref)
    created = timezone.localtime(ref.created_at).date() if ref.created_at else timezone.localdate()
    buf, c, W, H, top, L = _document()
    y0 = _kop(c, W, top, L, lh)
    _dateline(c, W, L, y0 - 6 * mm, lh, created)
    yn = _title(c, W, y0 - 13 * mm, "SURAT RUJUKAN", ref.number)
    x = L
    for code, label in ref.PURPOSES: x = _checkbox(c, x, yn - 8 * mm, ref.purpose == code, label)
    y = _lines(c, L, yn - 15 * mm, "Mohon agar karyawan yang tercantum di bawah ini dapat diperiksa kesehatannya / ditangani lebih lanjut.", W - 2 * L)
    inc = ref.incident_date or timezone.localtime(rec.visit_at).date()
    rows = [("Tujuan rujukan", ref.facility), ("Nama", emp.name), ("Jenis kelamin", GENDER.get(emp.gender, emp.gender)), ("Umur", f"{emp.age(created)} tahun" if emp.age(created) is not None else "-"),
            ("Alamat", emp.address or "-"), ("Jabatan", emp.position.name if emp.position_id else "-"), ("Departemen", emp.department.name), ("NIK Karyawan", emp.nik), ("NIK KTP", emp.nik_ktp or "-"),
            ("No. BPJS Kesehatan (K)", emp.bpjs_kes or "-"), ("No. BPJS Ketenagakerjaan (TK)", emp.bpjs_tk or "-"), ("Tanggal kejadian / keluhan", _fmt(inc))]
    y = _fields(c, W, L, y - 0.5 * mm, rows, label_w=58 * mm)
    inc_data = ((rec.exam or {}).get("kecelakaan") or {}) if isinstance(rec.exam, dict) else {}
    med = []
    if rec.kind == "kecelakaan_kerja":
        for k, lab in (("lokasi", "Lokasi kejadian"), ("kronologi", "Kronologi"), ("bagian_tubuh", "Bagian tubuh cedera")):
            if inc_data.get(k): med.append((lab, inc_data[k]))
    if (rec.complaint or "").strip(): med.append(("Keluhan", rec.complaint.strip()))
    if (ref.note or "").strip(): med.append(("Catatan klinis", ref.note.strip()))
    y = _fields(c, W, L, y, med, label_w=58 * mm, max_lines=7) if med else y
    y = _lines(c, L, y - 3 * mm, "Demikian agar mendapat tindak lanjut, terima kasih.", W - 2 * L)
    _signature(c, W - L - 62 * mm, y - 4 * mm, role, name)
    c.showPage(); c.save()
    return buf.getvalue()


# ================================================================ Daftar Karyawan Berobat + REKAP (putaran 58)
def _rp(n): return "Rp " + f"{int(n):,}".replace(",", ".") if n else "Rp -"


def bills_pdf(bills, period, partner, user):
    """Daftar karyawan berobat per mitra/bulan (A4 lanskap) + lembar REKAP pembayaran, mengikuti formulir Personalia/Keuangan.
    Kolom: Tgl periksa, NIK, Nama, Jabatan, Dept, Pelayanan, Tagihan RS, Potongan, Tagihan Aktif, Ditanggung Karyawan, Kebijakan Perusahaan, Keterangan (aturan BPJS).
    Beban: sesuai penanggung (BPJS / Non BPJS) & Master Tanggungan pada tanggal pelayanan (services.split_bill), disalin ke tiap tagihan. Tanpa keluhan/diagnosa (bukan data medis)."""
    from collections import OrderedDict
    from reportlab.lib.pagesizes import landscape
    from . import services
    buf = BytesIO(); W, H = landscape(A4); c = canvas.Canvas(buf, pagesize=(W, H)); L = 12 * mm
    lh = letterhead.info(user)
    mitra = partner.name if partner else "Semua Mitra"
    try: label = f"{MONTHS[int(period[5:]) - 1].upper()} {period[:4]}" if period and len(period) == 7 else "SEMUA PERIODE"
    except (ValueError, IndexError): label = "SEMUA PERIODE"
    cols = [("Tgl Periksa", 16), ("NIK", 19), ("Nama Karyawan", 34), ("Jabatan", 26), ("Dept", 20), ("Pelayanan", 18), ("Tagihan RS", 21), ("Potongan", 18), ("Tagihan Aktif", 21),
            ("Ditanggung Karyawan", 21), ("Kebijakan Perusahaan", 21), ("Keterangan", 0)]
    fixed = sum(w for _, w in cols) * mm; cols[-1] = ("Keterangan", (W - 2 * L - fixed) / mm)
    xs = [L]
    for _, w in cols: xs.append(xs[-1] + w * mm)
    num = {6, 7, 8, 9, 10}

    def head(first):
        c.setFont("Helvetica-Bold", 11); y = H - 14 * mm
        c.drawCentredString(W / 2, y, f"DAFTAR KARYAWAN BEROBAT {mitra.upper()}"); c.drawCentredString(W / 2, y - 5 * mm, f"BULAN {label}")
        y -= 12 * mm; h = 9 * mm; c.setFillColorRGB(0.93, 0.55, 0.25); c.rect(L, y - h, xs[-1] - L, h, fill=1, stroke=1); c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica-Bold", 6.5)
        for i, (t, _) in enumerate(cols):
            for j, part in enumerate(simpleSplit(t, "Helvetica-Bold", 6.5, xs[i + 1] - xs[i] - 2 * mm)[:2]): c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 3.6 * mm - j * 3 * mm, part)
        return y - h

    y = head(True); rowh = 6.2 * mm
    tot = dict(rs=0, disc=0, net=0, emp=0, co=0)
    c.setFont("Helvetica", 6.8)
    if not bills:
        c.drawString(L + 2 * mm, y - 5 * mm, "Tidak ada tagihan pada periode/filter ini."); y -= rowh
    for b in bills:
        if y - rowh < 40 * mm:
            c.showPage(); y = head(False); c.setFont("Helvetica", 6.8)
        e = b.employee
        pct = services.pct_clean(b.company_pct); ket = f"{b.get_payer_display()}: prs {pct}% / kry {services.pct_clean(100 - b.company_pct)}%"
        cells = [_fmt(b.service_date), e.nik, e.name, e.position.name if e.position_id else "-", e.department.name, b.get_service_type_display(), _rp(b.total_amount), _rp(b.discount_amount),
                 _rp(b.net_amount), _rp(b.employee_amount), _rp(b.company_amount), ket]
        for i, t in enumerate(cells):
            t = simpleSplit(str(t), "Helvetica", 6.8, xs[i + 1] - xs[i] - 2 * mm)[0] if t else ""
            (c.drawRightString(xs[i + 1] - 1.5 * mm, y - 4.3 * mm, t) if i in num else c.drawString(xs[i] + 1.2 * mm, y - 4.3 * mm, t))
        c.setLineWidth(0.3); c.line(L, y - rowh, xs[-1], y - rowh)
        for x in xs: c.line(x, y, x, y - rowh)
        y -= rowh
        tot["rs"] += b.total_amount; tot["disc"] += b.discount_amount; tot["net"] += b.net_amount; tot["emp"] += b.employee_amount; tot["co"] += b.company_amount
    c.setFont("Helvetica-Bold", 7.2); c.rect(L, y - rowh, xs[-1] - L, rowh)
    c.drawString(xs[0] + 2 * mm, y - 4.4 * mm, "Total")
    for i, k in ((6, "rs"), (7, "disc"), (8, "net"), (9, "emp"), (10, "co")): c.drawRightString(xs[i + 1] - 1.5 * mm, y - 4.4 * mm, _rp(tot[k]))
    y -= rowh + 8 * mm
    if y < 55 * mm: c.showPage(); y = H - 25 * mm
    today = timezone.localdate(); c.setFont("Helvetica", 9)
    c.drawString(L + 10 * mm, y, "Mengetahui,"); c.drawString(L + 10 * mm, y - 4.5 * mm, lh["pers_title"])
    c.drawString(L + 10 * mm, y - 24 * mm, "( ........................ )")   # kolom tanda tangan dikosongkan: nama ditulis tangan
    c.drawRightString(W - L - 10 * mm, y, f"{lh['city']}, {_id(today)}" if lh["city"] else _id(today)); c.drawRightString(W - L - 10 * mm, y - 4.5 * mm, "Yang Membuat")
    c.drawRightString(W - L - 10 * mm, y - 24 * mm, "( ........................ )")
    c.showPage()
    _rekap_page(c, W, H, bills, mitra, label, tot, lh, today)
    c.save(); return buf.getvalue()


def _rekap_page(c, W, H, bills, mitra, label, tot, lh, today):
    """Lembar REKAP (formulir pembayaran): baris 1 = Hutang Pengobatan ditanggung Karyawan (piutang yang dipotong dari karyawan), lalu satu baris per departemen = beban pengobatan
    perusahaan. Jumlah = seluruh tagihan aktif yang dibayarkan ke mitra (perusahaan membayar penuh; bagian karyawan menjadi piutang)."""
    from collections import OrderedDict
    L = 20 * mm; R = W - 20 * mm; y = H - 22 * mm
    c.setFont("Helvetica-Bold", 16); c.drawCentredString(W / 2, y, "REKAP"); y -= 12 * mm
    c.setFont("Helvetica", 10)
    for k, v in (("Kepada Yth", ""), ("Dari / Dept", "Keuangan"), ("Untuk dibayarkan ke", f"{mitra} Sebagai Tagihan Bulan {label.title()}")):
        c.drawString(L, y, k); c.drawString(L + 42 * mm, y, ": " + v); y -= 6 * mm
    y -= 3 * mm
    xs = [L, L + 12 * mm, L + 105 * mm, L + 130 * mm, R]
    def row(vals, bold=False, h=7 * mm):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9.5)
        for i, t in enumerate(vals):
            if i == 3: c.drawRightString(xs[4] - 2 * mm, y - 5 * mm, t)
            elif i == 2: c.drawCentredString((xs[2] + xs[3]) / 2, y - 5 * mm, t)
            else: c.drawString(xs[i] + 2 * mm, y - 5 * mm, t)
        c.rect(L, y - h, R - L, h)
        for x in xs[1:-1]: c.line(x, y, x, y - h)
        y -= h
    row(["No.", "Uraian", "Perk.", "Jumlah (Rp)"], bold=True)
    by_dept = OrderedDict()
    for b in bills: by_dept[b.employee.department.name] = by_dept.get(b.employee.department.name, 0) + b.company_amount
    lines = [("Hutang Pengobatan ditanggung Karyawan", tot["emp"])] + [(f"Pengobatan Kary {d}", v) for d, v in sorted(by_dept.items())]
    for i, (t, v) in enumerate(lines, 1): row([str(i), t[:52], "", _rp(v)])
    row(["", "Jumlah", "", _rp(tot["net"])], bold=True)
    y -= 12 * mm; c.setFont("Helvetica", 9)
    for i, role in enumerate(("Pembukuan", "Disetujui", "Diketahui", "Pemohon", "Penerima")):
        x = L + i * ((R - L) / 5); c.drawString(x, y, role); c.drawString(x, y - 20 * mm, "(................)")
    c.setFont("Helvetica", 7.5); c.drawString(L, y - 30 * mm, f"Dicetak {_id(today)}.")
    c.showPage()
