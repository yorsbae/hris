"""Impor log mesin absensi — Fingerspot Personnel dan sejenisnya (Tahap 6, putaran 48).

Format ekspor Fingerspot bisa berbeda antarversi/cabang, jadi pembacaan dibuat TOLERAN, bukan terkunci pada satu format:
  • berkas  : .xlsx · .xls (lama) · .xls yang sebenarnya HTML (umum pada aplikasi absensi) · .csv · .txt · .dat (ekspor flashdisk mesin)
  • tata letak (dideteksi otomatis; bisa diubah manual di halaman pratinjau):
      log     — satu baris = satu scan: [PIN] [Nama] [Tanggal & jam] (atau kolom Tanggal + kolom Jam terpisah) [Mesin]
      harian  — satu baris = satu karyawan-tanggal dengan beberapa kolom jam (Scan 1…n / Masuk / Pulang); sel berisi "07:58 16:03" juga dibaca
      dat     — tanpa judul kolom (mis. `1001<TAB>2026-10-01 07:58:12<TAB>1<TAB>0 …`): PIN dan waktu dikenali dari isi
Kedua tata letak berujung sama: daftar scan (kunci karyawan, waktu, mesin). Scan disimpan sebagai log mentah yang tidak diubah;
kunci idempotensi (pin, mesin, waktu) → mengimpor ulang berkas yang sama/tumpang tindih tidak menggandakan apa pun.
Alur: unggah → pratinjau (tidak ada yang tersimpan) → Impor. Isi baris tidak pernah masuk audit, hanya ringkasan."""
import csv, hashlib, io, os, re, uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import AttendanceImport, AttendancePin, AttendanceRaw, Employee

MAX_BYTES = int(getattr(settings, "ATTENDANCE_MAX_UPLOAD_MB", 30)) * 1024 * 1024
MAX_ROWS = int(getattr(settings, "ATTENDANCE_MAX_ROWS", 400_000))
DEFAULT_DEVICE = getattr(settings, "ATTENDANCE_DEFAULT_DEVICE", "FINGERSPOT")
STAGE_DIR = "attendance_stage"
CHUNK = 2000   # batas aman untuk IN (...) di SQLite (999 variabel pada versi lama) dan ukuran bulk_create

# ---------------------------------------------------------------- alias judul kolom (huruf kecil, tanpa spasi/tanda baca)
ALIASES = {
    "key": ["pin", "idmesin", "idfinger", "fingerid", "idabsen", "noabsen", "userid", "personid", "nopin", "badge", "idkaryawan", "nik", "nikkaryawan", "nip", "nokaryawan",
            "noinduk", "nomorinduk", "employeeid", "empid", "kodekaryawan", "kodepegawai", "idno", "noid", "id"],
    "name": ["nama", "namakaryawan", "namapegawai", "name", "employeename", "fullname"],
    "datetime": ["tanggalscan", "tglscan", "waktuscan", "scantime", "scandate", "datetime", "tanggalwaktu", "tanggaldanwaktu", "tanggaljam", "timestamp", "datetimescan",
                 "tanggalabsen", "waktuabsen", "waktu", "scan"],
    "date": ["tanggal", "tgl", "date", "tanggalabsensi", "tglabsen"],
    "time": ["jam", "time", "jamscan", "jamabsen", "clock"],
    "device": ["mesin", "device", "deviceid", "sn", "serialnumber", "machine", "terminal", "namamesin", "kodemesin", "devicename"],
}
TIME_HDR = re.compile(r"^(scan|jam|masuk|pulang|keluar|in|out|checkin|checkout|clockin|clockout|scanmasuk|scanpulang|jammasuk|jampulang|jamkeluar|waktumasuk|waktupulang|"
                      r"istirahat\w*|ot\w*|lembur\w*)\d*$")
ALL_ALIASES = {a for v in ALIASES.values() for a in v}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5, "jun": 6, "jul": 7, "agu": 8, "agt": 8, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}
FORMULA = ("=", "@")


def _norm(h): return re.sub(r"[^a-z0-9]", "", str(h if h is not None else "").lower())


# ---------------------------------------------------------------- membaca berkas → grid (list baris; sel = str/datetime/date/time/angka/None)
class _Html(HTMLParser):
    def __init__(self): super().__init__(); self.rows, self.row, self.cell, self.in_cell = [], None, [], False
    def handle_starttag(self, t, a):
        if t == "tr": self.row = []
        elif t in ("td", "th"): self.in_cell, self.cell = True, []
        elif t == "br" and self.in_cell: self.cell.append("\n")
    def handle_endtag(self, t):
        if t in ("td", "th") and self.row is not None: self.row.append("".join(self.cell).strip()); self.in_cell = False
        elif t == "tr" and self.row is not None:
            if any(self.row): self.rows.append(self.row)
            self.row = None
    def handle_data(self, d):
        if self.in_cell: self.cell.append(d)


def _decode(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"): return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try: return raw.decode(enc)
        except UnicodeDecodeError: continue
    return raw.decode("latin-1")


def _text_grid(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines: return []
    sample = lines[:8]
    best = max(("\t", ";", ",", "|"), key=lambda d: sum(ln.count(d) for ln in sample))
    if sum(ln.count(best) for ln in sample) == 0:  # tanpa pemisah: spasi (mis. "1 2026-10-01 07:58:12 1 0")
        return [re.split(r"\s+", ln.strip()) for ln in lines]
    return list(csv.reader(io.StringIO("\n".join(lines)), delimiter=best))


def read_grid(raw: bytes, filename: str):
    """→ list[list]. ValueError berisi pesan untuk pengguna."""
    if len(raw) > MAX_BYTES: raise ValueError(f"Berkas terlalu besar (maks {MAX_BYTES // 1024 // 1024} MB). Bagi per minggu/bulan, atau pakai perintah `import_attendance` di server.")
    if not raw.strip(): raise ValueError("Berkas kosong.")
    head, name = raw[:8], (filename or "").lower()
    if head[:4] == b"PK\x03\x04": grid = _xlsx(raw)
    elif head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or head[:2] in BIFF_BOF: grid = _xls(raw)   # OLE2 biasa, atau aliran BIFF mentah (ekspor Fingerspot: .xls tanpa pembungkus OLE)
    elif raw.lstrip()[:5] == b"<?xml" and b"<ROW" in raw: grid = _xml_grid(raw)       # ekspor "XML" Fingerspot (<GRID><COLUMNS/><ROWS/>)
    elif raw.lstrip()[:1] == b"<" and (b"<table" in raw[:20000].lower() or b"<tr" in raw[:20000].lower()):
        p = _Html(); p.feed(_decode(raw)); grid = p.rows
    else: grid = _text_grid(_decode(raw))
    if not grid: raise ValueError("Berkas tidak berisi data.")
    if len(grid) > MAX_ROWS + 60: raise ValueError(f"Terlalu banyak baris (maks {MAX_ROWS:,}). Bagi berkas per periode.".replace(",", "."))
    return grid


BIFF_BOF = (b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08")   # rekaman BOF BIFF2/3/4/5-8


def _xml_grid(raw):
    """Ekspor XML Fingerspot: <COLUMNS><COLUMN Name Caption/> dan <ROWS><ROW nama_kolom="nilai"/>. DOCTYPE dibuang sebelum diurai (tanpa entitas eksternal/DTD)."""
    import xml.etree.ElementTree as ET
    text = re.sub(r"<\?.*?\?>", "", _decode(raw), flags=re.S)                           # deklarasi & instruksi (mis. <?xml:stylesheet …?> yang bukan XML valid)
    text = re.sub(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", "", text, count=1, flags=re.S)
    try: root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError: raise ValueError("Berkas XML tidak dapat dibaca (rusak atau bukan ekspor Fingerspot).")
    cols = [(c.get("Name") or "", c.get("Caption") or c.get("Name") or "") for c in root.iter("COLUMN")]
    if not cols: raise ValueError("Berkas XML tidak memuat daftar kolom (<COLUMN>).")
    rows = [[r.get(n, "") for n, _ in cols] for r in root.iter("ROW")]
    return [[cap for _, cap in cols]] + rows if rows else []


def _xlsx(raw):
    from openpyxl import load_workbook
    try: wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception: raise ValueError("Berkas .xlsx tidak dapat dibaca (rusak atau bukan XLSX).")
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True, max_row=MAX_ROWS + 62)]
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if len(rows) >= 2: return rows
    return []


def _xls(raw):
    try: import xlrd
    except ImportError: raise ValueError("Berkas .xls lama butuh paket `xlrd` (pip install xlrd). Atau buka di Excel lalu simpan sebagai .xlsx / .csv.")
    try: wb = xlrd.open_workbook(file_contents=raw, logfile=io.StringIO())
    except Exception: raise ValueError("Berkas .xls tidak dapat dibaca. Buka di Excel lalu simpan sebagai .xlsx / .csv.")
    for sh in wb.sheets():
        rows = []
        for r in range(min(sh.nrows, MAX_ROWS + 62)):
            line = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c); v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        dt = xlrd.xldate_as_datetime(v, wb.datemode); v = dt.time() if dt.date() == date(1899, 12, 30) else dt
                    except Exception: pass
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK): v = None
                line.append(v)
            if any(c not in (None, "") for c in line): rows.append(line)
        if len(rows) >= 2: return rows
    return []


# ---------------------------------------------------------------- mengurai nilai
_DT = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})(?:[ T]+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([AaPp][Mm])?)?$")
_DTM = re.compile(r"^(\d{1,2})[ \-/]([A-Za-z]{3,9})[ \-/,]*(\d{4})(?:[ T]+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?)?$")
_TM = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?!\d)")


def _mk(y, mo, d, h=0, mi=0, s=0, pm=None):
    if pm: h = (h % 12) + (12 if pm.lower() == "pm" else 0)
    if y < 100: y += 2000
    try: return datetime(y, mo, d, h, mi, s)
    except ValueError: return None


def parse_dt(v, serial=False):
    """→ (datetime, punya_jam: bool) atau (None, False). Urutan tanggal: YYYY-MM-DD, selain itu DD/MM/YYYY (kebiasaan Indonesia)."""
    if isinstance(v, datetime): return v.replace(microsecond=0), bool(v.hour or v.minute or v.second)  # tengah malam persis = sel tanggal saja
    if isinstance(v, date): return datetime(v.year, v.month, v.day), False
    if isinstance(v, (int, float)) and serial and 30000 < v < 80000:
        dt = datetime(1899, 12, 30) + timedelta(days=float(v)); return dt.replace(microsecond=0), v != int(v)
    if not isinstance(v, str): return None, False
    s = v.strip()
    m = _DT.match(s)
    if m:
        a, b, c, h, mi, sec, pm = m.groups()
        h, mi, sec = int(h or 0), int(mi or 0), int(sec or 0)
        dt = _mk(int(a), int(b), int(c), h, mi, sec, pm) if len(a) == 4 else _mk(int(c), int(b), int(a), h, mi, sec, pm)
        return dt, h is not None and m.group(4) is not None
    m = _DTM.match(s)
    if m and m.group(2)[:3].lower() in MONTHS:
        d, mon, y, h, mi, sec = m.groups()
        return _mk(int(y), MONTHS[mon[:3].lower()], int(d), int(h or 0), int(mi or 0), int(sec or 0)), h is not None
    if serial and re.fullmatch(r"\d{5}(\.\d+)?", s): return parse_dt(float(s), True)
    return None, False


def parse_times(v):
    """Semua jam dalam sel (mis. '07:58', time(7,58), '07:58\\n16:03', 0.3319 pecahan hari Excel) → list[time]."""
    if v is None or v == "": return []
    if isinstance(v, datetime): return [v.time().replace(microsecond=0)]
    if isinstance(v, time): return [v.replace(microsecond=0)]
    if isinstance(v, float) and 0 <= v < 1:
        sec = round(v * 86400); return [time(sec // 3600 % 24, sec % 3600 // 60, sec % 60)]
    if isinstance(v, (int, float)): return []
    return [time(int(a), int(b), int(c or 0)) for a, b, c in _TM.findall(str(v))]


def clean_key(v):
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    s = str(v).strip().lstrip("'")
    return s if re.fullmatch(r"[\w.\-/ ]{1,40}", s) else ""


def _kind(vals, need=0.8):
    """Klasifikasi isi kolom dari contoh: 'datetime' | 'date' | 'time' | 'other'. `need` = porsi sel yang harus terbaca (lebih longgar bila judul kolom sudah dikenal)."""
    vals = [v for v in vals if v not in (None, "")][:200]
    if not vals: return "other", 0
    n = len(vals); dtm = dt = tm = 0
    for v in vals:
        d, has_t = parse_dt(v, serial=False)
        if d and has_t: dtm += 1
        elif d: dt += 1
        elif parse_times(v) and not isinstance(v, (int, float)) or (isinstance(v, float) and 0 <= v < 1): tm += 1
    for k, c in (("datetime", dtm), ("date", dt), ("time", tm)):
        if c / n >= need: return k, c / n
    return "other", 0


# ---------------------------------------------------------------- deteksi pemetaan
def detect(grid):
    """→ mapping: {header_row, key, name, datetime, date, times[], device} (indeks kolom; None = tidak ada) + layout."""
    width = max(len(r) for r in grid[:200])
    hdr_row, best = -1, 1
    for i, r in enumerate(grid[:30]):
        sc = sum(1 for c in r if (n := _norm(c)) and (n in ALL_ALIASES or TIME_HDR.match(n)))
        if sc > best: hdr_row, best = i, sc; break
    body = grid[hdr_row + 1:] if hdr_row >= 0 else grid
    cols = [[(r[c] if c < len(r) else None) for r in body[:300]] for c in range(width)]
    kinds = [_kind(c, 0.2 if hdr_row >= 0 else 0.8) for c in cols]   # judul dikenal = bukti kuat → toleran terhadap sebagian sel sampah
    m = {"header_row": hdr_row, "key": None, "name": None, "datetime": None, "date": None, "times": [], "device": None}
    if hdr_row >= 0:
        heads = [_norm(grid[hdr_row][c]) if c < len(grid[hdr_row]) else "" for c in range(width)]
        def first(role, ok=lambda c: True):
            for a in ALIASES[role]:
                for c, h in enumerate(heads):
                    if h == a and ok(c): return c
        m["key"] = first("key"); m["name"] = first("name"); m["device"] = first("device")
        m["datetime"] = first("datetime", lambda c: kinds[c][0] == "datetime")
        if m["datetime"] is None:
            m["date"] = first("date", lambda c: kinds[c][0] in ("date", "datetime"))
            times = [c for c, h in enumerate(heads) if (h in ALIASES["time"] or TIME_HDR.match(h) or h in ALIASES["datetime"]) and kinds[c][0] in ("time", "datetime") and c != m["date"]]
            if m["date"] is not None and times: m["times"] = times
            elif m["date"] is not None and kinds[m["date"]][0] == "datetime": m["datetime"], m["date"] = m["date"], None
        if m["key"] is None:  # tanpa judul ID yang dikenal: kolom non-tanggal pertama dengan isi mirip ID
            for c in range(width):
                if c not in (m["name"], m["datetime"], m["date"], m["device"], *m["times"]) and kinds[c][0] == "other": m["key"] = c; break
    else:  # tanpa judul (mis. .dat): kenali dari isi
        for c, (k, _) in enumerate(kinds):
            if k == "datetime" and m["datetime"] is None: m["datetime"] = c
            elif k == "date" and m["date"] is None: m["date"] = c
            elif k == "time" and m["date"] is not None and not m["times"]: m["times"] = [c]
        for c in range(width):
            if c not in (m["datetime"], m["date"], *m["times"]) and kinds[c][0] == "other": m["key"] = c; break
    return m


def layout_of(m): return "harian" if len(m.get("times") or []) >= 2 else "log"


# ---------------------------------------------------------------- mengambil scan dari grid
# alasan baris gagal: kode → (label singkat, saran perbaikan). Dipakai di pratinjau, halaman hasil, dan unduhan daftar gagal.
REASONS = {
    "formula": ("Sel diawali '=' atau '@' (ditolak: risiko formula)", "Buka berkas di Excel, hapus tanda '=' / '@' di awal sel PIN, lalu simpan ulang."),
    "key": ("PIN/NIK kosong atau karakternya tidak valid", "Isi PIN karyawan pada baris itu, atau hapus baris kosong/baris catatan di berkas. Pastikan kolom PIN/NIK di pemetaan benar."),
    "time": ("Tanggal/jam tidak terbaca", "Pastikan kolom tanggal/jam berformat tanggal (mis. 2026-10-01 07:58:12 atau 01/10/2026 07:58). Periksa pemetaan kolom tanggal & jam."),
    "absurd": ("Waktu tidak masuk akal (sebelum tahun 2000 atau di masa depan)", "Biasanya jam/tanggal mesin salah atau kolom tanggal tertukar. Perbaiki di Fingerspot atau berkas, lalu unggah ulang."),
}
DETAIL_MAX = 500   # rincian baris gagal yang disimpan di laporan impor


def extract(grid, m, limit_errors=500):
    """→ dict(taps=[(key, name, datetime, device)], errors=[(nomor_baris, pesan)], groups=Counter(kode_alasan), detail=[{row, code, msg, pin, value}], invalid=int, rows=int).
    Nomor baris = baris di berkas (mulai 1). `errors` memuat maks `limit_errors` baris pertama; `groups` menghitung SEMUA baris gagal per alasan."""
    out = {"taps": [], "errors": [], "groups": Counter(), "detail": [], "invalid": 0, "rows": 0}
    if m.get("key") is None: raise ValueError("Kolom PIN/NIK karyawan tidak ditemukan. Pilih kolomnya pada pemetaan di bawah.")
    if m.get("datetime") is None and not (m.get("date") is not None and m.get("times")): raise ValueError("Kolom tanggal & jam tidak ditemukan. Pilih kolom 'Tanggal & jam', atau 'Tanggal' + 'Jam' pada pemetaan.")
    start = m["header_row"] + 1 if m["header_row"] >= 0 else 0
    now = timezone.localtime().replace(tzinfo=None) + timedelta(days=1)
    g = lambda r, c: (r[c] if c is not None and c < len(r) else None)  # noqa: E731
    show = lambda v: re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:40]  # noqa: E731

    def bad(i, r, code, msg=None):
        out["invalid"] += 1; out["groups"][code] += 1; msg = msg or REASONS[code][0]
        if len(out["errors"]) < limit_errors: out["errors"].append((i, msg))
        if len(out["detail"]) < DETAIL_MAX:
            tcol = m.get("datetime") if m.get("datetime") is not None else m.get("date")
            out["detail"].append({"row": i, "code": code, "msg": msg, "pin": show(g(r, m["key"])).lstrip("=@+-"), "value": show(g(r, tcol)).lstrip("=@+-")})  # tanda formula dibuang agar aman dibuka di Excel
    for i, r in enumerate(grid[start:], start=start + 1):
        if not any(c not in (None, "") for c in r): continue
        out["rows"] += 1
        raw_key = g(r, m["key"])
        if isinstance(raw_key, str) and raw_key.strip()[:1] in FORMULA: bad(i, r, "formula"); continue
        key = clean_key(raw_key)
        if not key: bad(i, r, "key"); continue
        name = str(g(r, m["name"]) or "").strip()[:150]; dev = (str(g(r, m["device"]) or "").strip()[:40]) or DEFAULT_DEVICE
        stamps = []
        if m.get("datetime") is not None:
            dt, _ = parse_dt(g(r, m["datetime"]), serial=True)
            if dt: stamps = [dt]
        else:
            d, _ = parse_dt(g(r, m["date"]), serial=True)
            if d:
                prev = None
                for c in m["times"]:
                    for t in parse_times(g(r, c)):
                        dt = datetime.combine(d.date(), t)
                        if prev and dt < prev: dt += timedelta(days=1)   # jam pulang lebih kecil dari jam masuk → lewat tengah malam (shift malam)
                        stamps.append(dt); prev = dt
        if not stamps:
            # baris harian tanpa satu pun jam = hari tanpa scan (sah, bukan galat) — kecuali tanggalnya sendiri tak terbaca
            if m.get("datetime") is None and parse_dt(g(r, m["date"]), serial=True)[0]: out["rows"] -= 1; continue
            bad(i, r, "time"); continue
        for dt in stamps:
            if dt.year < 2000 or dt > now: bad(i, r, "absurd", f"Waktu tidak masuk akal: {dt:%d-%m-%Y %H:%M} (jam mesin salah?)."); break
            out["taps"].append((key, name, dt, dev))
    return out


def build_report(ex, plan=None):
    """Laporan yang disimpan di AttendanceImport.report (JSON): alasan gagal terkelompok + rincian + jumlah duplikat."""
    groups = [{"code": c, "label": REASONS[c][0], "hint": REASONS[c][1], "n": n} for c, n in ex["groups"].most_common()]
    rep = {"groups": groups, "rows": ex["detail"][:DETAIL_MAX], "rows_shown_max": DETAIL_MAX, "invalid": ex["invalid"]}
    if plan is not None: rep.update(dup_db=plan["dup_db"], dup_file=plan["dup_file"])
    return rep


# ---------------------------------------------------------------- pemetaan PIN → karyawan
def _chunks(seq, n=CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n): yield seq[i:i + n]


def resolve_keys(keys):
    """{key: (employee_id, via)}; via = 'pin' (tabel pemetaan) | 'nik' (PIN sama dengan NIK induk). Kunci tak dikenal tidak ada di hasil."""
    keys, out = set(keys), {}
    for ch in _chunks(keys):
        for pin, eid in AttendancePin.objects.filter(pin__in=ch).values_list("pin", "employee_id"): out[pin] = (eid, "pin")
    rest = keys - set(out)
    if rest and getattr(settings, "ATTENDANCE_PIN_EQUALS_NIK", True):
        exact, zero = {}, {}
        for eid, nik in Employee.objects.values_list("pk", "nik"):
            exact[nik] = eid
            if nik.isdigit(): zero.setdefault(nik.lstrip("0") or "0", []).append(eid)
        for k in rest:
            if k in exact: out[k] = (exact[k], "nik")
            elif k.isdigit() and len(zero.get(k.lstrip("0") or "0", [])) == 1: out[k] = (zero[k.lstrip("0") or "0"][0], "nik")
    return out


def suggest_by_name(unmapped_names):
    """{key: Employee} — saran (BUKAN pemetaan otomatis): nama di berkas sama persis dengan SATU karyawan."""
    out = {}
    for key, name in unmapped_names.items():
        if not name: continue
        hit = list(Employee.objects.filter(name__iexact=name)[:2])
        if len(hit) == 1: out[key] = hit[0]
    return out


# ---------------------------------------------------------------- penyimpanan sementara + analisis + impor
def _stage_path(name):
    from pathlib import Path
    root = Path(settings.MEDIA_ROOT) / STAGE_DIR; root.mkdir(parents=True, exist_ok=True)
    return root / name


def stage_upload(f, user):
    """Simpan unggahan sebagai draft (berkas sementara bernama uuid). ValueError bila tak terbaca."""
    raw = f.read(MAX_BYTES + 1); grid = read_grid(raw, f.name)
    m = detect(grid)
    name = uuid.uuid4().hex
    _stage_path(name).write_bytes(raw)
    return AttendanceImport.objects.create(original_name=os.path.basename(f.name)[:200], sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), staged_name=name,
                                           layout=layout_of(m), mapping=m, created_by=user)


def load_grid(imp):
    if imp.state != "draft" or not imp.staged_name: raise ValueError("Berkas sementara sudah tidak ada (impor ini sudah selesai/dibatalkan). Unggah ulang.")
    p = _stage_path(imp.staged_name)
    if not re.fullmatch(r"[0-9a-f]{32}", imp.staged_name) or not p.exists(): raise ValueError("Berkas sementara hilang. Unggah ulang.")
    return read_grid(p.read_bytes(), imp.original_name)


def mapping_from_post(p, width):
    """Pemetaan dari isian form (indeks kolom, '' = tidak ada). Indeks di luar lebar kolom ditolak."""
    def idx(k):
        v = (p.get(k) or "").strip()
        if not v: return None
        if not v.isdigit() or int(v) >= width: raise ValueError("Pemetaan kolom tidak valid.")
        return int(v)
    times = []
    for v in p.getlist("map_times"):
        if not v.isdigit() or int(v) >= width: raise ValueError("Pemetaan kolom jam tidak valid.")
        times.append(int(v))
    hr = (p.get("map_header") or "").strip()
    return {"header_row": int(hr) if hr.lstrip("-").isdigit() else -1, "key": idx("map_key"), "name": idx("map_name"), "datetime": idx("map_datetime"),
            "date": idx("map_date"), "times": times, "device": idx("map_device")}


def _npin(k):
    """PIN dibandingkan tanpa nol di depan ('0012' = '12') agar format berkas yang berbeda tidak menggandakan scan yang sama."""
    return (k.lstrip("0") or "0") if k.isdigit() else k


def _db_index(taps):
    """{(pin_ternormalisasi, waktu): {kode_mesin…}} untuk scan yang sudah ada di basis data dalam rentang berkas."""
    dmin = min(t[2] for t in taps); dmax = max(t[2] for t in taps); npins = {_npin(t[0]) for t in taps}
    qs = AttendanceRaw.objects.filter(punched_at__gte=timezone.make_aware(dmin), punched_at__lte=timezone.make_aware(dmax)).values_list("pin", "device_id", "punched_at")
    idx = defaultdict(set)
    for p, dv, at in qs.iterator(chunk_size=20000):
        n = _npin(p)
        if n in npins: idx[(n, timezone.make_naive(at))].add(dv)
    return idx


def _same(idx, k, dev, dt):
    """Scan yang sama = PIN (tanpa nol depan) + waktu sama dan mesin sama. Mesin 'tak diketahui' (berkas tanpa kolom Mesin → DEFAULT_DEVICE) cocok dengan mesin mana pun
    pada PIN+waktu yang sama, sehingga mengimpor ulang data yang sama dari format berkas lain tidak menggandakan. Dua mesin bernama berbeda tetap dianggap dua scan."""
    devs = idx.get((_npin(k), dt))
    return bool(devs) and (dev in devs or dev == DEFAULT_DEVICE or DEFAULT_DEVICE in devs)


def plan(taps):
    """Satu-satunya tempat aturan duplikat (pratinjau & impor memakai hasil yang sama).
    → dict(kept=[scan berbeda di berkas], new=[yang belum ada di DB], dup_db=n sudah ada di DB, dup_file=n kembar di dalam berkas sendiri)."""
    idx = _db_index(taps) if taps else {}
    seen = defaultdict(set); kept, new = [], []; dup_db = dup_file = 0
    for t in taps:
        k, _, dt, dev = t; nk = (_npin(k), dt)
        if dev in seen[nk] or (seen[nk] and (dev == DEFAULT_DEVICE or DEFAULT_DEVICE in seen[nk])): dup_file += 1; continue
        seen[nk].add(dev); kept.append(t)
        if _same(idx, k, dev, dt): dup_db += 1
        else: new.append(t)
    return {"kept": kept, "new": new, "dup_db": dup_db, "dup_file": dup_file}


def _same_file_note(imp):
    """Peringatan bila berkas yang persis sama (SHA-256) sudah pernah selesai diimpor."""
    prev = AttendanceImport.objects.filter(sha256=imp.sha256, state="selesai").exclude(pk=imp.pk).order_by("-id").first()
    return prev


def analyze(imp, mapping=None):
    """Pratinjau tanpa menyimpan scan. Mengembalikan dict untuk templat; memperbarui ringkasan di baris draft."""
    grid = load_grid(imp); m = mapping or imp.mapping
    ex = extract(grid, m)
    pl = plan(ex["taps"]); uniq = pl["kept"]
    keys = {t[0] for t in uniq}; res = resolve_keys(keys)
    names = {}
    for k, n, _, _ in uniq:
        if n and k not in names: names[k] = n
    unm_count = Counter(k for k, *_ in uniq if k not in res)
    dmin = min((t[2] for t in uniq), default=None); dmax = max((t[2] for t in uniq), default=None)
    sug = suggest_by_name({k: names.get(k, "") for k in list(unm_count)[:200]})
    emp_names = {e.pk: e for e in Employee.objects.filter(pk__in={v[0] for v in res.values()}).only("nik", "name")} if len(res) <= 5000 else {}
    imp.layout = layout_of(m); imp.mapping = m; imp.rows_total, imp.rows_invalid = ex["rows"], ex["invalid"]
    imp.taps_valid, imp.taps_duplicate, imp.taps_new = len(uniq), pl["dup_db"], len(pl["new"])
    imp.pins_total, imp.pins_unmapped, imp.taps_unmapped = len(keys), len(unm_count), sum(unm_count.values())
    imp.date_min, imp.date_max = (dmin.date() if dmin else None), (dmax.date() if dmax else None)
    imp.report = build_report(ex, pl)
    imp.save()
    width = max(len(r) for r in grid[:200]); body = grid[m["header_row"] + 1:] if m["header_row"] >= 0 else grid
    head = grid[m["header_row"]] if m["header_row"] >= 0 else []
    columns = [{"i": c, "title": (str(head[c]).strip() if c < len(head) and head[c] not in (None, "") else f"Kolom {c + 1}"),
                "sample": ", ".join(str(r[c])[:19] for r in body[:3] if c < len(r) and r[c] not in (None, ""))} for c in range(width)]
    return {"imp": imp, "ex": ex, "columns": columns, "m": m, "layout": imp.layout, "report": imp.report, "dup_file": pl["dup_file"], "same_file": _same_file_note(imp),
            "unmapped": [{"pin": k, "name": names.get(k, ""), "taps": n, "suggest": sug.get(k)} for k, n in unm_count.most_common(30)],
            "mapped_count": len(keys) - len(unm_count), "preview": [(k, names.get(k, ""), dt, dev, (emp_names.get(res[k][0]) if k in res else None)) for k, _, dt, dev in uniq[:12]],
            "grid_rows": len(grid)}


def commit(imp, user, ignore_invalid=False, mapping=None):
    """Simpan scan (idempoten, dikunci agar dua klik/dua sesi tidak menggandakan) + buat pemetaan otomatis PIN = NIK.
    Mengembalikan imp: state 'selesai', atau 'dibatalkan' bila tidak ada scan baru (semua sudah ada → tidak ada data/riwayat ganda). ValueError bila tidak boleh dilanjutkan."""
    with transaction.atomic():
        AttendanceImport.objects.select_for_update().filter(pk=imp.pk).first()           # kunci baris: commit paralel untuk draft yang sama menunggu, lalu gagal rapi
        imp.refresh_from_db(fields=["state", "staged_name"])
        grid = load_grid(imp); m = mapping or imp.mapping; ex = extract(grid, m)
        if ex["invalid"] and not ignore_invalid: raise ValueError(f"{ex['invalid']} baris tidak terbaca. Perbaiki berkas/pemetaan, atau centang 'abaikan baris yang tak terbaca'.")
        if not ex["taps"]: raise ValueError("Tidak ada scan yang dapat diimpor dari berkas ini.")
        pl = plan(ex["taps"]); uniq = pl["kept"]
        keys = {t[0] for t in uniq}
        res = resolve_keys(keys)
        # pemetaan otomatis PIN = NIK: tidak membuat baris pemetaan kedua bila karyawan itu sudah punya PIN setara (mis. '0012' vs '12')
        eids = {eid for eid, via in res.values() if via == "nik"}; have = defaultdict(set)
        for ch in _chunks(eids):
            for pin, eid in AttendancePin.objects.filter(employee_id__in=ch).values_list("pin", "employee_id"): have[eid].add(_npin(pin))
        new_pins = []
        for k, (eid, via) in res.items():
            if via == "nik" and _npin(k) not in have[eid]: new_pins.append(AttendancePin(pin=k, employee_id=eid, source="nik", created_by=user)); have[eid].add(_npin(k))
        for ch in _chunks(new_pins): AttendancePin.objects.bulk_create(ch, ignore_conflicts=True)
        rows = [AttendanceRaw(employee_id=res[k][0] if k in res else None, pin=k, device_id=dev, punched_at=timezone.make_aware(dt), batch=imp) for k, _, dt, dev in pl["new"]]
        for ch in _chunks(rows, 5000): AttendanceRaw.objects.bulk_create(ch, ignore_conflicts=True)
        n_new = AttendanceRaw.objects.filter(batch=imp).count() if rows else 0    # hitung yang BENAR-BENAR masuk (impor paralel lain bisa mendahului sebagian)
        unm = Counter(k for k, *_ in uniq if k not in res)
        imp.rows_total, imp.rows_invalid, imp.taps_valid = ex["rows"], ex["invalid"], len(uniq)
        imp.taps_new, imp.taps_duplicate = n_new, len(uniq) - n_new
        imp.pins_total, imp.pins_unmapped, imp.taps_unmapped = len(keys), len(unm), sum(unm.values())
        imp.date_min, imp.date_max = min(t[2] for t in uniq).date(), max(t[2] for t in uniq).date()
        imp.report = build_report(ex, {"dup_db": len(uniq) - n_new, "dup_file": pl["dup_file"]}); imp.mapping, imp.layout = m, layout_of(m)
        if n_new: imp.state, imp.committed_at = "selesai", timezone.now()
        else: imp.state, imp.note = "dibatalkan", f"Tidak ada scan baru: semua {len(uniq)} scan sudah ada di sistem (tidak ada yang disimpan)."
        _drop_stage(imp); imp.save()
    return imp


def _drop_stage(imp):
    if imp.staged_name and re.fullmatch(r"[0-9a-f]{32}", imp.staged_name):
        try: _stage_path(imp.staged_name).unlink(missing_ok=True)
        except OSError: pass
    imp.staged_name = ""


def cancel(imp):
    if imp.state != "draft": raise ValueError("Hanya draft yang dapat dibatalkan.")
    _drop_stage(imp); imp.state = "dibatalkan"; imp.save()


def purge_stale(hours=24):
    """Hapus draft lama (berkas sementara ikut hilang). Dipanggil oleh `process_attendance` dan saat membuka halaman impor."""
    n = 0
    for imp in AttendanceImport.objects.filter(state="draft", created_at__lt=timezone.now() - timedelta(hours=hours)):
        _drop_stage(imp); imp.state = "dibatalkan"; imp.note = "Kedaluwarsa (draft > 24 jam)"; imp.save(); n += 1
    return n


# ---------------------------------------------------------------- pemetaan PIN manual / massal
def link_pin(user, pin, employee, source="manual", note=""):
    """Petakan PIN → karyawan dan hubungkan semua log mentah PIN itu yang masih yatim. Mengembalikan jumlah scan yang terhubung."""
    pin = clean_key(pin)
    if not pin: raise ValueError("PIN tidak valid.")
    cur = AttendancePin.objects.filter(pin=pin).select_related("employee").first()
    if cur:
        if cur.employee_id == employee.pk: return 0
        raise ValueError(f"PIN {pin} sudah dipetakan ke {cur.employee.nik} {cur.employee.name}. Gunakan 'Pindahkan PIN' bila memang salah orang.")
    with transaction.atomic():
        AttendancePin.objects.create(pin=pin, employee=employee, source=source, note=note[:200], created_by=user)
        return AttendanceRaw.objects.filter(pin=pin, employee__isnull=True).update(employee=employee)


def move_pin(user, pin, employee, reason):
    """Pindahkan PIN ke karyawan lain (salah orang). Semua log mentah PIN itu ikut dipindah; alasan wajib; pemanggil menghitung ulang rekap kedua orang."""
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan wajib diisi.")
    cur = AttendancePin.objects.select_for_update().select_related("employee").get(pin=pin)
    old = cur.employee
    if old.pk == employee.pk: raise ValueError("PIN sudah milik karyawan ini.")
    with transaction.atomic():
        cur.employee, cur.source, cur.note = employee, "manual", f"Dipindah dari {old.nik}: {reason}"[:200]; cur.save()
        n = AttendanceRaw.objects.filter(pin=pin).update(employee=employee)
    return old, n


def import_pin_table(user, raw, filename, commit_it):
    """CSV/XLSX kolom `pin`,`nik` (opsional `catatan`). Semua-atau-tidak-sama-sekali. → dict(ok, errors[(baris, pesan)], created, linked, total)."""
    from apps.core import tabular
    res = {"ok": False, "fatal": "", "errors": [], "created": 0, "linked": 0, "total": 0, "skipped": 0, "commit": commit_it}
    try: rows = tabular.read_table(raw, filename, ["pin", "nik"])
    except ValueError as e: res["fatal"] = str(e); return res
    res["total"] = len(rows); niks = {r["nik"] for r in rows}
    emps = {e.nik: e for e in Employee.objects.filter(nik__in=niks)}
    existing = {p.pin: p for p in AttendancePin.objects.filter(pin__in={r["pin"] for r in rows}).select_related("employee")}
    seen, plan = set(), []   # (nama lokal `plan` di sini = rencana pemetaan, bukan fungsi plan() modul)
    for i, r in enumerate(rows, start=2):
        pin, nik = clean_key(r["pin"]), r["nik"].strip()
        if tabular.formula_like(r["pin"]) or tabular.formula_like(nik): res["errors"].append((i, "Sel diawali '=' atau '@' ditolak.")); continue
        if not pin: res["errors"].append((i, "PIN kosong/tidak valid.")); continue
        if pin in seen: res["errors"].append((i, f"PIN {pin} dobel di berkas.")); continue
        seen.add(pin)
        if nik not in emps: res["errors"].append((i, f"NIK {nik} tidak ditemukan.")); continue
        cur = existing.get(pin)
        if cur and cur.employee_id != emps[nik].pk: res["errors"].append((i, f"PIN {pin} sudah milik {cur.employee.nik}; gunakan 'Pindahkan PIN'.")); continue
        if not cur: plan.append((pin, emps[nik], r.get("catatan", "")))
        else: res["skipped"] += 1   # PIN sudah dipetakan ke karyawan yang sama → dilewati (tidak digandakan)
    res["ok"] = not res["errors"]
    if res["errors"] or not commit_it: res["created"] = len(plan); return res
    with transaction.atomic():
        for pin, emp, note in plan: res["linked"] += link_pin(user, pin, emp, "impor", note); res["created"] += 1
    return res
