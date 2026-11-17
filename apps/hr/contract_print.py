"""Cetak kontrak dari template Word (.docx) yang bisa diedit HRD sendiri.

Template memakai penanda {{nama}}, {{no_kontrak}}, dst. (daftar: PLACEHOLDERS). Satu pemisah halaman (Ctrl+Enter) memisahkan
HALAMAN 1 (depan) dan HALAMAN 2 (belakang). Karena printer kantor umumnya tanpa duplex, hasil cetak dibuat per "gelombang":
  • berkas DEPAN  = halaman 1 semua kontrak berurutan
  • berkas BELAKANG = halaman 2 semua kontrak, urutannya mengikuti lembar depan (sama / terbalik, tergantung printer).
Hasilnya .docx (bukan PDF) supaya tidak bergantung pada LibreOffice/Word di server; dibuka dan dicetak dari Word.
"""
import re
import struct
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import Contract, ContractConfig, MinimumWage

DEFAULT_TEMPLATE = Path(__file__).parent / "contract_default.docx"
PLACEHOLDERS = {
    "no_kontrak": "Nomor kontrak", "tgl_mulai": "Kontrak mulai", "tgl_selesai": "Kontrak sampai", "durasi": "Lama kontrak",
    "tgl_rapat": "Tanggal rapat / keputusan (diisi saat menyusun cetakan)", "hari_rapat": "Hari rapat / keputusan", "tgl_dibuat": "(sama dengan tgl_rapat; nama lama)", "hari_dibuat": "(sama dengan hari_rapat; nama lama)", "kota": "Kota penandatanganan",
    "nama": "Nama karyawan", "nik": "NIK karyawan", "nik_ktp": "No. KTP", "tgl_lahir": "Tanggal lahir", "alamat": "Alamat",
    "departemen": "Departemen", "jabatan": "Jabatan", "shift": "Shift / kelompok",
    "umk": "UMK (angka, mis. 2.500.000)", "umk_terbilang": "UMK terbilang", "tahun_umk": "Tahun UMK", "tgl_gaji": "Tanggal gajian",
    "perusahaan": "Nama perusahaan", "alamat_perusahaan": "Alamat perusahaan", "ttd_nama": "Nama penandatangan", "ttd_jabatan": "Jabatan penandatangan",
    "ttd_scan": "Gambar scan tanda tangan Pihak Pertama",
}
_PH = re.compile(r"\{\{\s*(\w+)\s*\}\}")
BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
SORTS = {"dept": "Departemen, lalu nama", "nama": "Nama (A–Z)", "nik": "NIK", "akhir": "Kontrak paling dulu berakhir", "no": "Nomor kontrak"}
_SAT = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


# ---------- format ----------
def _t(n):   # 1..999
    if n < 12: return _SAT[n]
    if n < 20: return _t(n - 10) + " belas"
    if n < 100: t, s = divmod(n, 10); return _t(t) + " puluh" + (" " + _t(s) if s else "")
    if n < 200: return "seratus" + (" " + _t(n - 100) if n > 100 else "")
    h, r = divmod(n, 100); return _t(h) + " ratus" + (" " + _t(r) if r else "")


def terbilang(n):
    n = int(n)
    if n == 0: return "nol"
    parts = []
    for div, name in ((10**9, "miliar"), (10**6, "juta"), (10**3, "ribu")):
        q, n = divmod(n, div)
        if q: parts.append("seribu" if (div == 1000 and q == 1) else f"{_t(q)} {name}")
    if n: parts.append(_t(n))
    return " ".join(parts)


def fmt_date(d): return f"{d.day} {BULAN[d.month]} {d.year}" if d else "-"
def rupiah(n): return f"{int(n):,}".replace(",", ".")


def duration_text(start, end):
    """'12 (dua belas) bulan' / '6 (enam) bulan 15 (lima belas) hari' dari start s/d end (kedua tanggal inklusif)."""
    if not start or not end or end < start: return "-"
    stop = end + timedelta(days=1)
    m = (stop.year - start.year) * 12 + stop.month - start.month
    anchor = _add_months(start, m)
    if anchor > stop: m -= 1; anchor = _add_months(start, m)
    d = (stop - anchor).days
    out = []
    if m: out.append(f"{m} ({terbilang(m)}) bulan")
    if d or not m: out.append(f"{d} ({terbilang(d)}) hari")
    return " ".join(out)


def _add_months(d, m):
    y, mo = divmod(d.month - 1 + m, 12); y += d.year; mo += 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
    return date(y, mo, day)


# ---------- nomor, UMK, nilai ----------
def format_number(fmt, seq, on):
    return fmt.format(seq=seq, roman=ROMAN[on.month], month=on.month, year=on.year)


def next_number(on, cfg=None):
    """Nomor kontrak berikutnya untuk tanggal `on`: urut per tahun; dilewati bila sudah dipakai."""
    cfg = cfg or ContractConfig.get(); used = set(Contract.objects.values_list("number", flat=True))
    seq = Contract.objects.filter(start__year=on.year).count() + 1
    for _ in range(10000):
        try: n = format_number(cfg.number_format, seq, on)
        except (KeyError, IndexError, ValueError): n = format_number("{seq:03d}/PKWT/{roman}/{year}", seq, on)
        if n not in used: return n
        seq += 1
    return n


def wage_for(year):
    """UMK tahun `year`; bila belum diisi, tahun terakhir sebelumnya. None bila belum ada sama sekali."""
    return MinimumWage.objects.filter(year__lte=year).order_by("-year").first()


def problems(contract, cfg):
    """(blocking, warnings). Blocking = kontrak tidak bisa dicetak benar."""
    e, blocking, warn = contract.employee, [], []
    if not contract.end: blocking.append("tanggal berakhir kontrak kosong")
    if not wage_for(contract.start.year): blocking.append(f"UMK tahun {contract.start.year} belum diisi")
    for lbl, ok in (("alamat", e.address), ("No. KTP", e.nik_ktp), ("tanggal lahir", e.birth_date), ("jabatan", e.position_id)):
        if not ok: warn.append(f"{lbl} kosong (dicetak “-”)")
    return blocking, warn


def values_for(c, cfg, decision=None):
    """decision = tanggal rapat/keputusan; bila kosong dipakai yang terakhir diingat, lalu tanggal mulai kontrak."""
    e, w = c.employee, wage_for(c.start.year); rapat = decision or cfg.decision_date or c.start
    amount = w.amount if w else 0
    return {
        "no_kontrak": c.number, "tgl_mulai": fmt_date(c.start), "tgl_selesai": fmt_date(c.end), "durasi": duration_text(c.start, c.end),
        "tgl_rapat": fmt_date(rapat), "hari_rapat": HARI[rapat.weekday()], "tgl_dibuat": fmt_date(rapat), "hari_dibuat": HARI[rapat.weekday()], "kota": cfg.city,
        "nama": e.name, "nik": e.nik, "nik_ktp": e.nik_ktp or "-", "tgl_lahir": fmt_date(e.birth_date), "alamat": (e.address or "-").replace("\n", ", "),
        "departemen": e.department.name, "jabatan": e.position.name if e.position_id else "-", "shift": e.shift_label,
        "umk": rupiah(amount), "umk_terbilang": terbilang(amount), "tahun_umk": str(w.year if w else c.start.year), "tgl_gaji": str(cfg.pay_day),
        "perusahaan": cfg.company_name, "alamat_perusahaan": cfg.company_address, "ttd_nama": cfg.signer_name or "…………………", "ttd_jabatan": cfg.signer_title,
    }


def sample_values(cfg):
    return {"no_kontrak": "001/PKWT/XI/2026", "tgl_mulai": "1 November 2026", "tgl_selesai": "31 Oktober 2027", "durasi": "12 (dua belas) bulan",
            "tgl_rapat": "30 Oktober 2026", "hari_rapat": "Jumat", "tgl_dibuat": "30 Oktober 2026", "hari_dibuat": "Jumat", "kota": cfg.city, "nama": "Budi Santoso (contoh)", "nik": "DM00001", "nik_ktp": "3325000000000001",
            "tgl_lahir": "17 Agustus 1995", "alamat": "Jl. Contoh No. 1, Batang", "departemen": "Produksi", "jabatan": "Operator", "shift": "A7",
            "umk": "2.500.000", "umk_terbilang": terbilang(2500000), "tahun_umk": "2026", "tgl_gaji": str(cfg.pay_day),
            "perusahaan": cfg.company_name, "alamat_perusahaan": cfg.company_address, "ttd_nama": cfg.signer_name or "…………………", "ttd_jabatan": cfg.signer_title}


def order(contracts, sort):
    k = {"nama": lambda c: (c.employee.name.lower(), c.pk), "nik": lambda c: (c.employee.nik, c.pk), "no": lambda c: (c.number, c.pk),
         "akhir": lambda c: (c.end or date.max, c.employee.name.lower()),
         "dept": lambda c: (c.employee.department.name.lower(), c.employee.name.lower(), c.pk)}.get(sort or "dept")
    return sorted(contracts, key=k or (lambda c: c.pk))


def batches(items, size):
    size = max(int(size or 50), 1)
    return [items[i:i + size] for i in range(0, len(items), size)]


# ---------- template ----------
def template_bytes(cfg=None):
    cfg = cfg or ContractConfig.get()
    return bytes(cfg.template_docx) if cfg.template_docx else DEFAULT_TEMPLATE.read_bytes()


def _is_break(p):
    return p.tag == qn("w:p") and any(br.get(qn("w:type")) == "page" for br in p.iter(qn("w:br")))


def _split(doc):
    body = doc.element.body
    kids = [c for c in body.iterchildren() if c.tag != qn("w:sectPr")]
    idx = next((i for i, c in enumerate(kids) if _is_break(c)), None)
    return (kids[:idx], kids[idx + 1:], body.find(qn("w:sectPr"))) if idx is not None else (kids, [], body.find(qn("w:sectPr")))


def _all_text(doc):
    out = ["".join(t.text or "" for t in doc.element.body.iter(qn("w:t")))]
    return "\n".join(out)


def validate_template(data):
    """(error|None, info dict). Dipakai saat unggah: file harus .docx valid, penanda dikenal, tepat 1 pemisah halaman."""
    try: doc = Document(BytesIO(data))
    except Exception: return "Berkas bukan dokumen Word (.docx) yang valid.", {}
    body = doc.element.body
    paras = [Paragraph(p, doc._body) for p in body.iter(qn("w:p"))]
    found = set()
    for p in paras: found.update(m.group(1) for m in _PH.finditer("".join(r.text for r in p.runs)))
    # penanda terpecah antar paragraf/format tidak terbaca → deteksi sisa "{{" tanpa pasangan
    raw = "".join(t.text or "" for t in body.iter(qn("w:t")))
    leftover = raw.count("{{") != sum(1 for _ in _PH.finditer(raw))
    unknown = sorted(found - set(PLACEHOLDERS))
    if unknown: return "Penanda tidak dikenal: " + ", ".join("{{%s}}" % u for u in unknown) + ". Cek ejaan (huruf kecil, tanpa spasi).", {}
    if leftover: return "Ada “{{” tanpa penutup “}}” yang benar. Ketik tiap penanda utuh dalam satu kali ketik.", {}
    breaks = sum(1 for c in body.iterchildren() if _is_break(c))
    if breaks != 1: return f"Template harus punya TEPAT satu pemisah halaman (Ctrl+Enter) antara halaman 1 dan 2; ditemukan {breaks}.", {}
    front, back, _ = _split(doc)
    return None, {"placeholders": sorted(found), "front": len(front), "back": len(back)}


# ---------- isi template ----------
def _fill_par(par, values, sig):
    runs = par.runs
    full = "".join(r.text for r in runs)
    if "{{" not in full: return
    bounds, pos = [], 0
    for r in runs: bounds.append((pos, pos + len(r.text))); pos += len(r.text)
    new = [r.text for r in runs]; want_pic = False
    for m in reversed(list(_PH.finditer(full))):
        key = m.group(1); rep = values.get(key, "") if key != "ttd_scan" else ""
        want_pic = want_pic or key == "ttd_scan"
        s, e = m.span(); idx = [i for i, (a, b) in enumerate(bounds) if a < e and b > s]
        if not idx: continue
        for i in idx:
            a, b = bounds[i]; ls, le = max(s, a) - a, min(e, b) - a
            new[i] = new[i][:ls] + (rep if i == idx[0] else "") + new[i][le:]
    for r, t in zip(runs, new): r.text = t
    if want_pic and sig: _picture(par, sig)


def _png_size(data):
    return struct.unpack(">II", data[16:24]) if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24 else (3, 1)


def _picture(par, sig, max_w=4.2, max_h=1.9):
    w, h = _png_size(sig); k = min(max_w / w, max_h / h)
    par.add_run().add_picture(BytesIO(sig), width=Cm(w * k), height=Cm(h * k))


def _fill_el(el, doc, values, sig):
    if el.tag == qn("w:p"): _fill_par(Paragraph(el, doc._body), values, sig)
    elif el.tag == qn("w:tbl"):
        for row in Table(el, doc._body).rows:
            for cell in row.cells:
                for p in cell.paragraphs: _fill_par(p, values, sig)


def build(tpl, items, side, sig=None):
    """Dokumen .docx berisi `items` (list nilai penanda) berurutan. side: 'depan' | 'belakang' | 'penuh' (halaman 1 lalu 2 tiap kontrak)."""
    doc = Document(BytesIO(tpl)); front, back, sect = _split(doc); body = doc.element.body
    els = {"depan": front, "belakang": back, "penuh": front + [_page_break()] + back}[side]
    for c in list(body.iterchildren()):
        if c is not sect: body.remove(c)
    for n, values in enumerate(items):
        copies = [deepcopy(e) for e in els]
        for c in copies: sect.addprevious(c); _fill_el(c, doc, values, sig)
        if n and copies:
            if copies[0].tag == qn("w:p"): Paragraph(copies[0], doc._body).paragraph_format.page_break_before = True
            else: copies[0].addprevious(_page_break())
    tail = doc.add_paragraph(); tail.paragraph_format.space_after = Pt(0); tail.add_run().font.size = Pt(1)   # Word butuh paragraf penutup setelah tabel
    out = BytesIO(); doc.save(out); return out.getvalue()


def _page_break():
    p = OxmlElement("w:p"); r = OxmlElement("w:r"); br = OxmlElement("w:br"); br.set(qn("w:type"), "page"); r.append(br); p.append(r); return p


def order_list_doc(rows, title):
    """Daftar urutan (satu tabel): untuk memeriksa tumpukan cetak. rows: [(no, nama, nik, dept, no_kontrak)]."""
    doc = Document(); doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading(title, level=2)
    t = doc.add_table(rows=1, cols=5); t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ("No", "Nama", "NIK", "Departemen", "No. kontrak")): c.text = h
    for r in rows:
        for c, v in zip(t.add_row().cells, r): c.text = str(v)
    out = BytesIO(); doc.save(out); return out.getvalue()


def png_ok(data):
    """Scan tanda tangan: PNG sungguhan (cek tanda tangan berkas, bukan hanya ekstensi), maks 1 MB, ukuran wajar."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) > 1024 * 1024: return False
    w, h = _png_size(data); return 20 <= w <= 6000 and 20 <= h <= 6000


def checklist_xlsx(rows, title, rapat=None):
    """Ceklis kehadiran penandatanganan (.xlsx) untuk kontrak yang dicetak. rows (urut sesuai tumpukan cetak):
    dict(no, nama, nik, dept, jabatan, no_kontrak, lama_berakhir(date|None), mulai, selesai, gelombang, dicetak(datetime|None)).
    Sheet 1 'Ceklis' (kolom Hadir berisi daftar pilihan ✓, baris berwarna otomatis, jumlah hadir dihitung rumus); sheet 2 ringkasan per tanggal kontrak lama berakhir."""
    from collections import Counter
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook(); ws = wb.active; ws.title = "Ceklis"
    heads = ["No", "Gel.", "Nama", "NIK", "Departemen", "Jabatan", "No. kontrak", "Kontrak lama berakhir", "Kontrak baru mulai", "Kontrak baru berakhir", "Dicetak", "Hadir", "Catatan"]
    widths = [6, 6, 28, 12, 18, 18, 20, 20, 18, 20, 12, 9, 28]
    n = len(rows); last = 5 + n
    ws["A1"] = title; ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Tanggal rapat/keputusan: " + (fmt_date(rapat) if rapat else "-"); ws["A3"] = "Hadir:"; ws["B3"] = f'=COUNTIF(L6:L{max(last, 6)},"✓")'
    ws["C3"] = f'=" dari {n} karyawan · belum hadir "&({n}-B3)'; ws["A3"].font = ws["B3"].font = Font(bold=True)
    thin = Side(style="thin", color="BBBBBB"); box = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, (h, w) in enumerate(zip(heads, widths), 1):
        c = ws.cell(5, i, h); c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3A68"); c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = box
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[5].height = 32
    for r, x in enumerate(rows, 6):
        vals = [x["no"], x["gelombang"], x["nama"], x["nik"], x["dept"], x["jabatan"], x["no_kontrak"], x["lama_berakhir"], x["mulai"], x["selesai"], x["dicetak"], None, None]
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v); c.border = box; c.alignment = Alignment(vertical="center", horizontal="center" if i in (1, 2, 4, 8, 9, 10, 11, 12) else "left")
            if i in (8, 9, 10): c.number_format = "dd mmm yyyy"
            if i == 11: c.number_format = "dd/mm/yy"
        ws.row_dimensions[r].height = 22
    if n:
        dv = DataValidation(type="list", formula1='"✓"', allow_blank=True, showErrorMessage=False); ws.add_data_validation(dv); dv.add(f"L6:L{last}")
        ws.conditional_formatting.add(f"A6:M{last}", FormulaRule(formula=['$L6="✓"'], fill=PatternFill("solid", bgColor="DFF3E4", fgColor="DFF3E4")))
        ws.auto_filter.ref = f"A5:M{last}"
    ws.freeze_panes = "D6"; ws.page_setup.orientation = "landscape"; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0; ws.sheet_properties.pageSetUpPr.fitToPage = True; ws.print_title_rows = "5:5"
    s2 = wb.create_sheet("Per tanggal berakhir"); s2.append(["Kontrak lama berakhir", "Jumlah", "Hadir"])
    for c in s2[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3A68")
    cnt = Counter(x["lama_berakhir"] for x in rows)
    for i, d in enumerate(sorted(cnt, key=lambda d: (d is None, d)), 2):
        s2.cell(i, 1, d if d else "(tanpa kontrak lama)").number_format = "dd mmm yyyy"; s2.cell(i, 2, cnt[d])
        s2.cell(i, 3, f'=COUNTIFS(Ceklis!H6:H{max(last, 6)},{get_column_letter(1)}{i},Ceklis!L6:L{max(last, 6)},"✓")' if d else f'=COUNTIFS(Ceklis!H6:H{max(last, 6)},"",Ceklis!L6:L{max(last, 6)},"✓")')
    s2.column_dimensions["A"].width = 26; s2.column_dimensions["B"].width = 10; s2.column_dimensions["C"].width = 10
    bio = BytesIO(); wb.save(bio); return bio.getvalue()
