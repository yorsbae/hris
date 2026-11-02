"""Pembaca scanlog mesin Fingerspot (protokol FK, port 5005) lewat FKAttend.dll bawaan Fingerspot Personnel.
Dijalankan sebagai PROSES TERPISAH oleh HRIS karena DLL-nya 32-bit & hanya untuk Windows:
    python32 fk_dump.py --dll "C:\\Program Files (x86)\\Fingerspot Personnel\\FKAttend.dll" --ip 192.168.11.40 --port 5005 --machine 12 [--key 0] [--new]
    python32 fk_dump.py --dll "...\\FKAttend.dll" --exports        # diagnostik: daftar fungsi yang diekspor DLL
    python32 fk_dump.py --dll ... --ip ... --machine 12 --from 2026-10-30 --to 2026-10-31 --bydate i4   # muat log HANYA rentang itu (lihat BYDATE)
    python32 fk_dump.py --dll ... --argc                       # jumlah argumen fungsi (kalibrasi + FK_LoadGeneralLogDataByDate)
    python32 fk_dump.py --dll ... --ip ... --machine 12 --from 2026-10-30 --to 2026-10-31 --bydate-test   # uji mana tanda tangan ByDate yang benar
    python32 fk_dump.py --dll ... --ip ... --machine 12 --users-down            # baca user + sidik jari (+ password/kartu)
    python32 fk_dump.py --dll ... --ip ... --machine 12 --users-up berkas.json  # kirim user dari berkas JSON (dihapus HRIS setelah selesai)
Keluaran (satu JSON per baris):
    {"p": <persen 0-100>, "stage": "connect|disable|load|read", "n": <jumlah scan>, "t": <detik>}   ← kemajuan + detak (tiap 5 dtk, juga saat DLL sedang menunggu mesin)
    {"rows": [["PIN","YYYY-MM-DD HH:MM:SS"],...]}   ← bertahap (5000 scan per baris) agar log besar tidak jadi satu JSON raksasa
    {"users": [{"pin","name","priv","password","card","fps":[[jari,"<base64>"],...]},...]}   ← bertahap (--users-down)
    {"ok": true, "count": N, "truncated": false} atau {"ok": false, "error": "..."}   ← baris terakhir
    (--users-up) baris terakhir {"ok": true, "sent": N, "fps": M, "bad": ["PIN x: ..."]}
Tanda tangan fungsi diturunkan dari disassembly FKAttend.dll (jumlah argumen stdcall cocok); verifikasi akhir tetap pada mesin sungguhan."""
import argparse, base64, ctypes, json, math, os, re, struct, sys, threading, time

_T0 = time.time(); _ST = {"p": 0, "stage": "start", "n": 0}; _LOCK = threading.Lock()


def _line(d):
    with _LOCK: sys.stdout.write(json.dumps(d) + "\n"); sys.stdout.flush()


def prog(p=None, stage=None, n=None):
    if p is not None: _ST["p"] = p
    if stage is not None:
        if stage != _ST.get("stage"): _ST["ts"] = time.time()
        _ST["stage"] = stage
    if n is not None: _ST["n"] = n
    _line({k: v for k, v in _ST.items() if k != "ts"} | {"t": round(time.time() - _T0, 1)})


LOAD_EST = 90.0                                                                    # detik; HRIS mengisi dari lama tahap load tarikan sebelumnya untuk mesin yang sama (--load-est)


def hb_line(st, now, t0, est=None):
    """Satu detak. Saat tahap 'load' (satu panggilan DLL tanpa laporan kemajuan) persen naik 15→29 mengikuti PERKIRAAN lama load (est detik; ±89% di titik est) supaya tidak tampak macet — bukan kemajuan mesin."""
    d = {k: v for k, v in st.items() if k != "ts"}; d.update(t=round(now - t0, 1), hb=1)
    if st.get("stage") == "load" and st.get("ts"): d["p"] = round(min(29, max(st["p"], 15 + 14 * (1 - math.exp(-2.2 * (now - st["ts"]) / max(est or LOAD_EST, 5))))), 1); d["est"] = 1
    return d


def _heartbeat():
    while True:
        time.sleep(5); _line(hb_line(_ST, time.time(), _T0))     # ctypes melepas GIL saat DLL menunggu → detak tetap jalan


BATCH = 5000; CAP = 2_000_000                                                     # CAP: pengaman memori; bila tercapai → truncated=true (HRIS menolak hasil terpotong)


class DllTimeout(Exception): pass


def timed_call(fn, timeout, *args):
    """Jalankan satu panggilan DLL di thread daemon dengan batas waktu (detik). DLL FK bisa menggantung tanpa batas bila mesin tidak menjawab
    (mis. FK_LoadGeneralLogData pada log besar / sesi macet); ctypes tidak bisa dibatalkan, jadi thread dibiarkan & pemanggil diberi DllTimeout."""
    box = {}
    def run():
        try: box["r"] = fn(*args)
        except BaseException as e: box["e"] = e
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(timeout)
    if t.is_alive(): raise DllTimeout(f"{getattr(fn, '__name__', 'fungsi DLL')} tidak menjawab dalam {timeout:g} dtk")
    if "e" in box: raise box["e"]
    return box["r"]


def hard_exit(**kw):
    """Keluar TANPA menjalankan finally (FK_EnableDevice/FK_DisConnect akan ikut menggantung bila DLL sedang macet)."""
    _line(kw); sys.stdout.flush(); os._exit(1)


def out(**kw): _line(kw); sys.exit(0 if kw.get("ok") else 1)


def exports(path):
    """Daftar nama fungsi yang diekspor DLL (baca tabel ekspor PE langsung, tanpa pustaka tambahan)."""
    b = open(path, "rb").read(); pe = struct.unpack_from("<I", b, 0x3C)[0]
    nsec = struct.unpack_from("<H", b, pe + 6)[0]; opt = pe + 24; magic = struct.unpack_from("<H", b, opt)[0]
    rva = struct.unpack_from("<I", b, opt + (96 if magic == 0x10B else 112))[0]
    secs = [struct.unpack_from("<IIII", b, opt + struct.unpack_from("<H", b, pe + 20)[0] + 40 * i + 8) for i in range(nsec)]
    def off(r):
        for vs, va, rs, ro in secs:
            if va <= r < va + max(vs, rs): return r - va + ro
        raise ValueError("RVA di luar seksi")
    e = off(rva); cnt, names = struct.unpack_from("<I", b, e + 24)[0], off(struct.unpack_from("<I", b, e + 32)[0]); res = []
    for i in range(cnt):
        o = off(struct.unpack_from("<I", b, names + 4 * i)[0]); res.append(b[o:b.index(b"\0", o)].decode("ascii", "replace"))
    return sorted(res)


# ---- user & sidik jari (nomor cadangan FK: 0-9 = jari, 10 = password, 11 = kartu; >= 12 = wajah/lainnya → dilewati)
BK_PSW, BK_CARD, FP_SIZE, BUF = 10, 11, 1680, 65536


def _bind_users(dll):
    I, P, V = ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_void_p
    dll.FK_ReadAllUserID.argtypes = [I]
    dll.FK_GetAllUserID.argtypes = [I, P, P, P, P]                       # handle, &pin, &backup, &privilege, &enable
    dll.FK_GetEnrollData.argtypes = [I, I, I, P, V, P]                   # handle, pin, backup, &privilege, buffer, &password/kartu
    dll.FK_PutEnrollData.argtypes = [I, I, I, I, V, I]                   # handle, pin, backup, privilege, buffer, password/kartu
    dll.FK_SaveEnrollData.argtypes = [I]
    dll.FK_GetUserName.argtypes = [I, I, ctypes.c_char_p]; dll.FK_SetUserName.argtypes = [I, I, ctypes.c_char_p]


def _txt(raw):
    raw = raw.split(b"\0", 1)[0]
    for enc in ("mbcs", "utf-8", "latin-1"):
        try: return raw.decode(enc).strip()
        except (LookupError, UnicodeDecodeError): continue
    return ""


def _list_users(dll, h):
    """Daftar user. → (users{kunci: {pin, priv, bks}}, mode 'num'|'str', info). Mesin ber-ID teks mengembalikan daftar numerik KOSONG → bila FK_GetAllUserID_StringID tersedia (hasil bongkar DLL: 5 argumen =
    handle, char** id, int* backup, int* priv, int* enable; argumen ke-2 pointer-ke-pointer buffer) dipakai itu. Kunci = int (num) atau str (str)."""
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int); pin, bk, pv, en = I(), I(), I(), I(); users = {}; info = {"str_flag": stringid_flag(dll, h)}
    if dll.FK_ReadAllUserID(h) != 1: out(ok=False, error=f"FK_ReadAllUserID gagal (error {dll.FK_GetLastError(h)}).")
    if info["str_flag"] != 1:                                             # flag 1 = mesin ber-ID teks → lewati jalur numerik
        while dll.FK_GetAllUserID(h, ctypes.byref(pin), ctypes.byref(bk), ctypes.byref(pv), ctypes.byref(en)) == 1:
            u = users.setdefault(pin.value, {"pin": str(pin.value), "priv": pv.value, "bks": []}); u["bks"].append(bk.value)
    if users: return users, "num", info
    if hasattr(dll, "FK_GetAllUserID_StringID") and argc(dll, "FK_GetAllUserID_StringID") == 5:
        if info["str_flag"] != 1: dll.FK_ReadAllUserID(h)                 # daftar numerik sudah habis terbaca → muat ulang untuk jalur StringID
        fn = dll.FK_GetAllUserID_StringID; fn.argtypes = [I, ctypes.POINTER(ctypes.c_void_p), P, P, P]; fn.restype = I
        buf = ctypes.create_string_buffer(256); bp = ctypes.c_void_p(ctypes.addressof(buf))
        for _ in range(500_000):                                          # pengaman: tidak pernah berputar selamanya
            ctypes.memset(buf, 0, 256)
            if fn(h, ctypes.byref(bp), ctypes.byref(bk), ctypes.byref(pv), ctypes.byref(en)) != 1: break
            key = _txt(buf.raw)
            if not key: continue
            u = users.setdefault(key, {"pin": key, "priv": pv.value, "bks": []}); u["bks"].append(bk.value)
    info["str_argc"] = argc(dll, "FK_GetAllUserID_StringID"); info["last_err"] = dll.FK_GetLastError(h)
    return users, "str", info


def users_down(dll, h):
    _bind_users(dll); I, V = ctypes.c_int, ctypes.c_void_p; P = ctypes.POINTER(I)
    users, mode, info = _list_users(dll, h); _line({"users_info": {"mode": mode, "n": len(users), **info}, "p": 15, "stage": "read", "n": 0})
    # FK_GetUserName* : argumen ke-3 = char** (pointer ke pointer buffer; bukan buffer langsung) — hasil bongkar DLL
    nm = ctypes.create_string_buffer(256); nmp = V(ctypes.addressof(nm)); PV = ctypes.POINTER(V)
    if mode == "str":
        dll.FK_GetUserName_StringID.argtypes = [I, ctypes.c_char_p, PV]; dll.FK_GetEnrollData_StringID.argtypes = [I, ctypes.c_char_p, I, P, V, P]
        get_name = lambda key: dll.FK_GetUserName_StringID(h, key.encode("mbcs", "replace"), ctypes.byref(nmp))      # noqa: E731
        get_enr = lambda key, b, priv, buf, pw: dll.FK_GetEnrollData_StringID(h, key.encode("mbcs", "replace"), b, ctypes.byref(priv), buf, ctypes.byref(pw))      # noqa: E731
    else:
        dll.FK_GetUserName.argtypes = [I, I, PV]
        get_name = lambda key: dll.FK_GetUserName(h, key, ctypes.byref(nmp))      # noqa: E731
        get_enr = lambda key, b, priv, buf, pw: dll.FK_GetEnrollData(h, key, b, ctypes.byref(priv), buf, ctypes.byref(pw))      # noqa: E731
    prog(30, "read", 0); buf = ctypes.create_string_buffer(BUF); batch = []; n = len(users); skipped = fps = 0
    for i, (num, u) in enumerate(sorted(users.items())):
        u.update(name="", password="", card=0, fps=[]); ctypes.memset(nm, 0, 256)
        if get_name(num) == 1: u["name"] = _txt(nm.raw)
        for b in sorted(set(u.pop("bks"))):
            ctypes.memset(buf, 0, BUF); priv, pw = I(), I()
            if get_enr(num, b, priv, buf, pw) != 1: continue
            u["priv"] = priv.value
            if b < BK_PSW: u["fps"].append([b, base64.b64encode(buf.raw[:FP_SIZE]).decode()]); fps += 1
            elif b == BK_PSW: u["password"] = str(pw.value) if pw.value else ""
            elif b == BK_CARD: u["card"] = pw.value
            else: skipped += 1                                           # wajah/palm dll.: belum didukung
        batch.append(u)
        if len(batch) >= 100: _line({"users": batch}); batch = []
        if i % 5 == 0: prog(round(30 + 65 * (i + 1) / n), "read", i + 1)
    if batch: _line({"users": batch})
    prog(95, "read", n); out(ok=True, count=n, fps=fps, skipped=skipped, mode=mode, **{k: v for k, v in info.items() if k != "str_flag"}, str_flag=info["str_flag"])


def users_up(dll, h, path):
    _bind_users(dll); sent = fps = 0; bad = []
    with open(path, encoding="utf-8") as f: items = json.load(f)
    buf = ctypes.create_string_buffer(BUF); n = len(items)
    def put(num, bk, priv, data, val):
        ctypes.memset(buf, 0, BUF); ctypes.memmove(buf, data, min(len(data), BUF)) if data else None
        return dll.FK_PutEnrollData(h, num, bk, priv, buf, val) == 1
    for i, it in enumerate(items):
        prog(round(15 + 78 * i / max(n, 1)), "read", i + 1); pin = str(it.get("pin", "")).strip()
        if not pin.isdigit(): bad.append(f"PIN {pin}: mesin FK hanya menerima PIN angka"); continue
        num = int(pin); priv = it.get("priv", 0); ok = False
        try:
            for bk, b64 in it.get("fps", []):
                if put(num, int(bk), priv, base64.b64decode(b64), 0): fps += 1; ok = True
                else: bad.append(f"PIN {pin} jari {bk}: ditolak mesin (error {dll.FK_GetLastError(h)})")
            if str(it.get("password", "")).isdigit() and it.get("password"): ok = put(num, BK_PSW, priv, b"", int(it["password"])) or ok
            if it.get("card"): ok = put(num, BK_CARD, priv, b"", int(it["card"])) or ok
            if ok and it.get("name"): dll.FK_SetUserName(h, num, it["name"].encode("mbcs", "replace")[:20])
        except Exception as e: bad.append(f"PIN {pin}: {type(e).__name__}: {e}"[:120]); continue
        if ok: sent += 1
        else: bad.append(f"PIN {pin}: tidak ada jari/password/kartu yang bisa dikirim")
    if sent and dll.FK_SaveEnrollData(h) != 1: out(ok=False, error=f"FK_SaveEnrollData gagal (error {dll.FK_GetLastError(h)}); user belum tersimpan permanen di mesin.")
    prog(95, "read", n); out(ok=sent > 0 or not items, sent=sent, fps=fps, bad=bad[:50], error=("" if sent else "Tidak ada user yang terkirim."))


# ---- FK_LoadGeneralLogDataByDate: tanda tangan TIDAK didokumentasikan di berkas ekspor → jumlah argumen dibaca dari DLL, bentuk argumen dipilih dari kandidat & DIUJI (--bydate-test)
BYDATE = {"dd": 5, "i3": 3, "s3": 3, "i4": 4, "s4": 4, "d7": 7, "d8": 8}     # id → jumlah argumen (termasuk handle). i=YYYYMMDD int, s="YYYY-MM-DD" teks, d=tahun,bulan,tanggal terpisah; angka 4/8 = ada argumen nReadMark(0)
CALIB = {"FK_LoadGeneralLogData": 2, "FK_GetGeneralLogData_1": 10, "FK_ConnectNet": 7}   # tanda tangan yang sudah diketahui → bukti pembacaan argc benar


def parse_argc_error(msg, tried):
    """Pesan ValueError ctypes ('... N bytes in excess' / '... N bytes missing') → jumlah argumen sebenarnya; None bila bukan pesan itu."""
    m = re.search(r"\((\d+) bytes (in excess|missing)\)", str(msg))
    if not m: return None
    d = int(m.group(1)) // 4
    return tried - d if m.group(2) == "in excess" else tried + d


_PE = {}


def _pe_funcs(path):
    """Tabel ekspor PE → {nama: offset berkas}. Di-cache."""
    if path in _PE: return _PE[path]
    b = open(path, "rb").read(); pe = struct.unpack_from("<I", b, 0x3C)[0]; nsec = struct.unpack_from("<H", b, pe + 6)[0]; opt = pe + 24
    magic = struct.unpack_from("<H", b, opt)[0]; rva = struct.unpack_from("<I", b, opt + (96 if magic == 0x10B else 112))[0]; osz = struct.unpack_from("<H", b, pe + 20)[0]
    secs = [struct.unpack_from("<IIII", b, opt + osz + 40 * k + 8) for k in range(nsec)]
    def off(r):
        for vs, va, rs, ro in secs:
            if va <= r < va + max(vs, rs): return r - va + ro
        raise ValueError("RVA di luar seksi")
    e = off(rva); cnt = struct.unpack_from("<I", b, e + 24)[0]; fa, na, oa = (off(struct.unpack_from("<I", b, e + d)[0]) for d in (28, 32, 36)); res = {}
    for k in range(cnt):
        o = off(struct.unpack_from("<I", b, na + 4 * k)[0]); n = b[o:b.index(b"\0", o)].decode("ascii", "replace")
        res[n] = off(struct.unpack_from("<I", b, fa + 4 * struct.unpack_from("<H", b, oa + 2 * k)[0])[0])
    _PE[path] = (b, res); return _PE[path]


def argc(dll, name, tried=12, path=None):
    """Jumlah argumen fungsi stdcall (termasuk handle), dibaca STATIS dari berkas DLL: epilog `pop ebp; ret N` di dalam fungsi (dibatasi padding 0xCC) → N/4.
    (Trik lama — memanggil dengan argumen berlebih & membaca ValueError ctypes — mati sejak Python 3.12 karena pemeriksaan tumpukan dihapus.) Dikalibrasi dengan CALIB.
    None bila fungsi tidak ada / epilognya tidak seragam."""
    path = path or _DLL_PATH.get("p")
    try: b, fn = _pe_funcs(path); o = fn[name]
    except (KeyError, OSError, ValueError, TypeError): return None
    seg = b[o:o + 8000]; k = seg.find(b"\xcc\xcc"); seg = seg[:k if k > 0 else len(seg)]
    vals = {seg[m + 2] for m in range(len(seg) - 3) if seg[m] == 0x5D and seg[m + 1] == 0xC2 and seg[m + 3] == 0}
    return vals.pop() // 4 if len(vals) == 1 else None


_DLL_PATH = {}


def load_dll(path):
    _DLL_PATH["p"] = path
    try:
        os.add_dll_directory(os.path.dirname(os.path.abspath(path))) if hasattr(os, "add_dll_directory") else None
        return ctypes.WinDLL(path)
    except OSError as e: out(ok=False, error=f"DLL tidak dapat dimuat: {e}")


def bydate_call(dll, h, sig, d1, d2):
    """Muat log hanya rentang d1..d2 (date). d2 dimajukan 1 hari agar batas akhir tidak terpotong; HRIS tetap menyaring ulang menurut tanggal."""
    from datetime import timedelta
    d2 = d2 + timedelta(days=1); f8, t8 = int(d1.strftime("%Y%m%d")), int(d2.strftime("%Y%m%d")); fs, ts = d1.isoformat().encode(), d2.isoformat().encode()
    from datetime import date as _dt, datetime as _dtt
    ole = lambda x: float((_dtt(x.year, x.month, x.day) - _dtt(1899, 12, 30)).days)   # noqa: E731  tanggal OLE/TDateTime: hari sejak 1899-12-30
    args = {"dd": (ole(d1), ole(d2)), "i3": (f8, t8), "s3": (fs, ts), "i4": (0, f8, t8), "s4": (0, fs, ts), "d7": (d1.year, d1.month, d1.day, d2.year, d2.month, d2.day), "d8": (0, d1.year, d1.month, d1.day, d2.year, d2.month, d2.day)}[sig]
    dll.FK_LoadGeneralLogDataByDate.argtypes = None; dll.FK_LoadGeneralLogDataByDate.restype = ctypes.c_int
    return dll.FK_LoadGeneralLogDataByDate(h, *args)


def bydate_test(a):
    """Jalankan pembacaan penuh (patokan) lalu tiap kandidat tanda tangan, MASING-MASING di proses terpisah (tanda tangan salah bisa membuat proses crash).
    Kandidat dinyatakan cocok bila jumlah scan di rentang SAMA dengan patokan; 'cepat' bila waktunya lebih singkat."""
    import subprocess
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 4: out(ok=False, error="--bydate-test butuh Windows + Python 32-bit.")
    d0 = load_dll(a.dll); cal = {n: (argc(d0, n), want) for n, want in CALIB.items()}; got = argc(d0, "FK_LoadGeneralLogDataByDate")
    if any(g != w for g, w in cal.values()): out(ok=False, error=f"Kalibrasi jumlah argumen gagal {cal} → pembacaan argc tidak dapat dipercaya; ByDate tidak dipakai.", calib=cal)
    if not a.date_from or not a.date_to: out(ok=False, error="--from dan --to wajib untuk --bydate-test.")
    base = [sys.executable, os.path.abspath(__file__), "--dll", a.dll, "--ip", a.ip, "--port", str(a.port), "--machine", str(a.machine), "--key", str(a.key), "--from", a.date_from, "--to", a.date_to, "--count-only"]
    if a.no_disable: base.append("--no-disable")
    def run(extra):
        t = time.time()
        try: pr = subprocess.run(base + extra, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired: return {"ok": False, "error": "timeout", "t": 1800}
        last = {}
        for ln in pr.stdout.splitlines():
            try: j = json.loads(ln)
            except ValueError: continue
            if isinstance(j, dict) and "ok" in j: last = j
        return dict(last or {"ok": False, "error": f"proses berhenti (kode {pr.returncode}) {pr.stderr[-120:]}"}, t=round(time.time() - t, 1))
    ref = run([]); res = []; rec = None
    for sig, n in BYDATE.items():
        if n != got: continue
        r = run(["--bydate", sig]); r["sig"] = sig; r["cocok"] = bool(r.get("ok")) and ref.get("ok") and r.get("inwin") == ref.get("inwin"); r["cepat"] = bool(r["cocok"] and r["t"] < ref["t"]); res.append(r)
        if r["cepat"] and not rec: rec = sig
    out(ok=bool(ref.get("ok")), argc=got, calib=cal, patokan={k: ref.get(k) for k in ("ok", "inwin", "raw", "t", "error")}, kandidat=[{k: r.get(k) for k in ("sig", "ok", "inwin", "raw", "t", "cocok", "cepat", "error")} for r in res], rekomendasi=rec,
        petunjuk=(f"Set ATTENDANCE_FK_BYDATE={rec} di .env lalu restart." if rec else "Tidak ada kandidat yang cocok+lebih cepat: tetap pakai pembacaan penuh (jangan set ATTENDANCE_FK_BYDATE)."))


def stringid_flag(dll, h, name="FK_GetLogDataIsSupportStringID"):
    """Dari bongkar DLL: fungsi ini hanya menerima handle (ret 4) dan mengembalikan flag lewat nilai return (bukan lewat pointer). None bila tidak ada / argumen tidak cocok."""
    try:
        if argc(dll, name) != 1: return None
        fn = getattr(dll, name); fn.argtypes = [ctypes.c_int]; fn.restype = ctypes.c_int; return fn(h)
    except Exception as e: return f"{type(e).__name__}"


def probe_info(dll, h):
    """Keadaan mesin untuk diagnosis (BACA SAJA): 'mendukung ID teks' + nilai status 1..12. Hanya memanggil fungsi yang jumlah argumennya terbukti cocok."""
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int); v = I(); info = {}
    for name in ("FK_GetLogDataIsSupportStringID", "FK_GetIsSupportStringID"):
        info[name] = stringid_flag(dll, h, name)
    try:
        if argc(dll, "FK_GetDeviceStatus") == 3:
            fn = dll.FK_GetDeviceStatus; fn.argtypes = [I, I, P]; fn.restype = I; info["status"] = {i: (v.value if fn(h, i, ctypes.byref(v)) == 1 else None) for i in range(1, 13)}
    except Exception as e: info["status"] = f"{type(e).__name__}"
    return info


def read_logs(dll, h, new=False, cap=CAP, load=None, emit=True, win=None):
    """Baca seluruh log yang sudah dimuat. Mesin ber-ID teks (StringID) mengembalikan PIN 0 lewat fungsi numerik → bila rekaman pertama ber-PIN 0
    dan FK_GetGeneralLogData_StringID tersedia, log dimuat ulang & dibaca lewat fungsi StringID (tata letak argumen sama: pointer 32-bit).
    → statistik {count, raw, pin0, baddate, stringid, sample, inwin}; baris scan dikirim bertahap lewat _line (emit=False: hanya hitung). win=(YYYY-MM-DD, YYYY-MM-DD) → inwin = jumlah scan di rentang itu."""
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int); v = [I() for _ in range(9)]; refs = [ctypes.byref(x) for x in v]
    sbuf = ctypes.create_string_buffer(64); st = {"count": 0, "raw": 0, "pin0": 0, "baddate": 0, "stringid": False, "sample": None, "truncated": False, "inwin": 0, "first_err": None}
    def str_ok():
        """Fungsi StringID dipakai hanya bila ada DAN jumlah argumennya terbukti 5 (hasil bongkar FKAttend.dll: handle, char** id, int* mode verifikasi, int* mode in/out, double* tanggal OLE).
        Argumen ke-2 adalah POINTER KE POINTER buffer (DLL mengisi buffer dengan spasi lalu menyalin ID); tanggal diberi buffer 16 byte supaya aman bila ternyata DWORD 4 byte."""
        if not hasattr(dll, "FK_GetGeneralLogData_StringID"): return False
        n = argc(dll, "FK_GetGeneralLogData_StringID"); st["str_argc"] = n
        if n != 5: return False
        dll.FK_GetGeneralLogData_StringID.argtypes = [I, ctypes.POINTER(ctypes.c_void_p), P, P, ctypes.c_void_p]; dll.FK_GetGeneralLogData_StringID.restype = I; return True
    from datetime import datetime as _dtm, timedelta as _td
    sid = ctypes.create_string_buffer(128); sid_p = ctypes.c_void_p(ctypes.addressof(sid)); tmb = (ctypes.c_double * 2)(); vm_, io_ = I(), I()
    def nxt():
        if st["stringid"]:
            ctypes.memset(sid, 0, 128); tmb[0] = tmb[1] = 0.0
            if dll.FK_GetGeneralLogData_StringID(h, ctypes.byref(sid_p), ctypes.byref(vm_), ctypes.byref(io_), ctypes.addressof(tmb)) != 1: return None
            try: t = _dtm(1899, 12, 30) + _td(days=tmb[0]) + _td(seconds=0.5)                   # tanggal OLE (hari sejak 1899-12-30); +0.5 dtk meredam galat pembulatan double
            except (OverflowError, ValueError): t = None
            st.setdefault("tm_raw", [tmb[0], tmb[1]])
            if t is None: return (_txt(sid.raw), vm_.value, io_.value, 0, 0, 0, 0, 0, 0)       # tanggal tak terbaca → dihitung baddate
            return (_txt(sid.raw), vm_.value, io_.value, t.year, t.month, t.day, t.hour, t.minute, t.second)
        if dll.FK_GetGeneralLogData_1(h, *refs) != 1: return None
        return (str(v[0].value),) + tuple(x.value for x in v[1:])
    st["str_flag"] = stringid_flag(dll, h)
    rec = nxt()
    if rec is None: st["first_err"] = dll.FK_GetLastError(h)             # daftar kosong: simpan kode galat DLL untuk diagnosis
    if (rec is None or rec[0] == "0") and str_ok():                      # kosong ATAU PIN 0 = mesin ber-ID teks → muat ulang & baca lewat fungsi StringID
        prog(20, "load"); st["stringid"] = True; st["numeric_empty"] = rec is None
        if (load or (lambda: dll.FK_LoadGeneralLogData(h, 1 if new else 0)))() != 1: raise RuntimeError(f"FK_LoadGeneralLogData (ulang, StringID) gagal (error {dll.FK_GetLastError(h)}).")
        prog(30, "read", 0); rec = nxt()
        if rec is None: st["first_err_str"] = dll.FK_GetLastError(h)
    if rec is None: st["probe"] = probe_info(dll, h)                    # tetap kosong → sertakan keadaan mesin di hasil (tanpa perlu menjalankan --diag)
    rows = []
    while rec:
        if st["count"] + st["pin0"] + st["baddate"] >= cap: st["truncated"] = True; break
        st["raw"] += 1; pin, _vm, _io, y, mo, d, hh, mi, ss = rec
        if st["sample"] is None: st["sample"] = list(rec)
        if y < 100: y += 2000                                            # beberapa firmware memberi tahun 2 digit
        if not pin or pin == "0": st["pin0"] += 1
        elif not (1 <= mo <= 12 and 1 <= d <= 31 and 0 <= hh < 24 and 0 <= mi < 60 and 0 <= ss < 60 and 1990 <= y <= 2100): st["baddate"] += 1
        else:
            ymd = f"{y:04d}-{mo:02d}-{d:02d}"; st["count"] += 1
            if st["count"] % 2000 == 0: prog(round(30 + 60 * (1 - math.exp(-st["count"] / 30000))), "read", st["count"])        # tiap 2000 scan (bukan tiap 5000) agar log kecil juga tampak bergerak
            if win and win[0] <= ymd <= win[1]: st["inwin"] += 1
            if emit: rows.append([pin, f"{ymd} {hh:02d}:{mi:02d}:{ss:02d}"])
            if emit and len(rows) >= BATCH:
                _line({"rows": rows}); rows = []; prog(round(30 + 60 * (1 - math.exp(-st["count"] / 30000))), "read", st["count"])   # total tidak diketahui → persen perkiraan
        rec = nxt()
    if rows: _line({"rows": rows})
    return st


def diag(dll, h, a):
    """Diagnosis 'mesin tidak mengembalikan scan' (hanya BACA): status mesin, hasil muat untuk nReadMark 0 dan 1, kode galat tiap langkah, dan beberapa rekaman pertama mentah.
    Tiap panggilan DLL diberi batas waktu (--diag-timeout) & tahap kemajuan sendiri; hasil per langkah dikirim LANGSUNG ({"diag_step": ...}) sehingga bila mesin macet,
    langkah yang macet tampak jelas dan keluaran sebagian tetap ada."""
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int); rep = {"machine": a.machine, "handle": h}; tmo = a.diag_timeout
    def step(name, fn, *args):
        prog(stage=f"diag:{name}")
        try: r = timed_call(fn, tmo, *args)
        except DllTimeout as e: hard_exit(ok=False, error=str(e), diag=rep, macet=name, catatan=f"Mesin/DLL macet di langkah '{name}'. Coba --no-disable, naikkan --diag-timeout, atau restart mesin (putus listrik) lalu ulangi.")
        _line({"diag_step": name, "ret": r, "err": dll.FK_GetLastError(h)}); return r
    try: dll.FK_GetDeviceStatus.argtypes = [I, I, P]; dll.FK_GetDeviceStatus.restype = I; v = I(); rep["status"] = {}
    except AttributeError: pass
    else:
        for i in range(1, 13):                                            # indeks status (jumlah user/jari/log dsb. — arti tiap indeks menurut SDK; dicatat apa adanya)
            r = step(f"status{i}", dll.FK_GetDeviceStatus, h, i, ctypes.byref(v)); rep["status"][i] = v.value if r == 1 else f"gagal(err {dll.FK_GetLastError(h)})"
    rep["load"] = {}
    for mark in ((0, 1) if a.diag_marks else (0,)):
        r = step(f"load{mark}", dll.FK_LoadGeneralLogData, h, mark); e = dll.FK_GetLastError(h); first = []
        v9 = [I() for _ in range(9)]; refs = [ctypes.byref(x) for x in v9]
        if r == 1:
            for k in range(3):
                g = step(f"read{mark}.{k}", dll.FK_GetGeneralLogData_1, h, *refs); first.append({"ret": g, "err": dll.FK_GetLastError(h), "val": [x.value for x in v9] if g == 1 else None})
                if g != 1: break
        rep["load"][f"nReadMark={mark}"] = {"load_ret": r, "load_err": e, "first_reads": first}
    out(ok=True, diag=rep, catatan="Kirim seluruh keluaran ini. load_ret=1 & first_reads[0].ret!=1 → mesin menjawab tetapi daftar kosong; status = jumlah data di mesin menurut indeks SDK.")


SESSION_GAP = 2.0          # jeda (detik) antar sambungan: mesin perlu waktu melepas sesi sebelumnya, kalau tidak sambungan berikutnya gagal kode -2
LICENSES = (1261, 0)       # contoh resmi SDK FK memakai 1261; lisensi 0 bisa tersambung tetapi pembacaan data kosong → 1261 dulu, 0 sebagai cadangan


def session(dll, a, lic, last, trace, empty=None):
    """Satu sambungan dengan lisensi `lic`. Memanggil out() (keluar) bila ada hasil akhir; return None bila harus mencoba lisensi berikutnya (gagal sambung / log kosong).
    `empty` (dict) menampung hasil sambungan yang SUKSES tetapi 0 scan, agar kegagalan sambung pada lisensi cadangan tidak menutupi fakta itu."""
    I = ctypes.c_int; h = dll.FK_ConnectNet(a.machine, a.ip.encode(), a.port, a.timeout, 0, a.key, lic)
    if h == -2: time.sleep(SESSION_GAP); h = dll.FK_ConnectNet(a.machine, a.ip.encode(), a.port, a.timeout, 0, a.key, lic)      # -2 sering = sesi lama belum dilepas mesin → satu kali ulang setelah jeda
    if h <= 0:
        trace.append(f"lisensi {lic}: kode {h}")
        if last and not empty: out(ok=False, error=f"FK_ConnectNet gagal (kode {h}; lisensi dicoba: {trace}; periksa No Mesin={a.machine}, IP, port, kunci komunikasi).")
        return None
    prog(10, "connect")
    try:
        if a.probe: out(ok=True, rows=[], note="terhubung", license=lic)
        if not a.no_disable:
            prog(12, "disable")
            try: timed_call(dll.FK_EnableDevice, 30, h, 0)
            except DllTimeout: hard_exit(ok=False, error="FK_EnableDevice(0) tidak menjawab dalam 30 dtk; ulangi dengan --no-disable.")
        try:
            if a.diag: diag(dll, h, a)
            if a.users_down: prog(15, "read"); users_down(dll, h)
            if a.users_up: prog(12, "read"); users_up(dll, h, a.users_up)
            prog(15, "load")                                              # tahap ini bisa lama bila log mesin besar (satu panggilan DLL, tanpa laporan kemajuan)
            from datetime import date as _d
            win = (a.date_from, a.date_to) if a.date_from and a.date_to else None
            if a.bydate:
                if not win: out(ok=False, error="--bydate butuh --from dan --to.")
                n = argc(dll, "FK_LoadGeneralLogDataByDate")
                if n != BYDATE[a.bydate]: out(ok=False, error=f"FK_LoadGeneralLogDataByDate: jumlah argumen di DLL = {n}, kandidat {a.bydate} butuh {BYDATE[a.bydate]} → tidak dipanggil (mencegah crash).")
                d1, d2 = _d.fromisoformat(a.date_from), _d.fromisoformat(a.date_to); load = lambda: bydate_call(dll, h, a.bydate, d1, d2)   # noqa: E731
                r = load(); what = "FK_LoadGeneralLogDataByDate"
            else:
                load = None; r = dll.FK_LoadGeneralLogData(h, 1 if a.new else 0); what = "FK_LoadGeneralLogData"
            if r != 1:
                trace.append(f"lisensi {lic}: {what} kode {r}, error {dll.FK_GetLastError(h)}")
                if last: out(ok=False, error=f"{what} gagal (kode {r}, error {dll.FK_GetLastError(h)}; dicoba: {trace}).")
                return None
            prog(30, "read", 0)
            try: st = read_logs(dll, h, a.new, load=load, emit=not a.count_only, win=win)
            except RuntimeError as e: out(ok=False, error=str(e))
            if st["raw"] == 0 and not last:
                trace.append(f"lisensi {lic}: sambung & muat sukses tetapi 0 scan terbaca")
                if empty is not None: st["license"] = lic; empty.update(st)
                return None
            st["license"] = lic; st["tried"] = trace
            prog(95, "read", st["count"]); out(ok=True, **st)
        finally:
            if not a.no_disable: dll.FK_EnableDevice(h, 1)
    finally: dll.FK_DisConnect(h)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dll", required=True); a.add_argument("--ip"); a.add_argument("--port", type=int, default=5005)
    a.add_argument("--machine", type=int, default=1); a.add_argument("--key", type=int, default=0); a.add_argument("--timeout", type=int, default=5000)
    a.add_argument("--new", action="store_true", help="hanya scan yang belum pernah dibaca (nReadMark=1)"); a.add_argument("--probe", action="store_true")
    a.add_argument("--no-disable", action="store_true", help="jangan panggil FK_EnableDevice(0) (sebagian firmware macet di sini)")
    a.add_argument("--users-down", action="store_true", help="baca semua user + sidik jari"); a.add_argument("--users-up", metavar="BERKAS.JSON", help="kirim user dari berkas JSON ke mesin")
    a.add_argument("--from", dest="date_from"); a.add_argument("--to", dest="date_to"); a.add_argument("--bydate", choices=sorted(BYDATE)); a.add_argument("--bydate-test", action="store_true")
    a.add_argument("--count-only", action="store_true", help="hanya hitung scan di rentang --from/--to (tanpa mengirim baris)"); a.add_argument("--argc", action="store_true", help="tampilkan jumlah argumen fungsi (kalibrasi + ByDate)")
    a.add_argument("--load-est", type=float, default=None, help="perkiraan lama tahap load (detik) untuk animasi persen"); a.add_argument("--license", type=int, default=None, help="paksa lisensi FK_ConnectNet (bawaan: coba 1261 lalu 0)"); a.add_argument("--diag", action="store_true", help="diagnosis baca-saja: status mesin + hasil muat log (kirim keluarannya bila scan 0)"); a.add_argument("--exports", action="store_true", help="tampilkan fungsi yang diekspor DLL lalu selesai")
    a.add_argument("--diag-timeout", type=float, default=120.0, help="batas waktu (detik) tiap panggilan DLL pada --diag; lewat batas → berhenti dengan keterangan langkah yang macet")
    a.add_argument("--diag-marks", action="store_true", help="--diag juga mencoba nReadMark=1 (bawaan: hanya 0)")
    a = a.parse_args()
    if a.exports:
        try: out(ok=True, exports=exports(a.dll))
        except Exception as e: out(ok=False, error=f"Tidak dapat membaca ekspor DLL: {type(e).__name__}: {e}")
    if a.argc:
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 4: out(ok=False, error="--argc butuh Windows + Python 32-bit.")
        d0 = load_dll(a.dll); cal = {n: [argc(d0, n), w] for n, w in CALIB.items()}
        out(ok=all(g == w for g, w in cal.values()), calib=cal, bydate=argc(d0, "FK_LoadGeneralLogDataByDate"), catatan="calib = [terbaca, seharusnya] harus cocok semua; bydate = jumlah argumen termasuk handle")
    if a.bydate_test:
        if not a.ip: out(ok=False, error="--ip wajib.")
        bydate_test(a)
    if not a.ip: out(ok=False, error="--ip wajib.")
    if os.name != "nt": out(ok=False, error="FKAttend.dll hanya berjalan di Windows.")
    if ctypes.sizeof(ctypes.c_void_p) != 4: out(ok=False, error=f"Python yang dipakai: {sys.executable} ({8 * ctypes.sizeof(ctypes.c_void_p)}-bit). Perlu Python 32-bit (FKAttend.dll 32-bit). Pasang Python 32-bit (installer 'Windows installer (32-bit)') di folder terpisah, lalu set ATTENDANCE_FK_PYTHON32 di .env ke python.exe 32-bit tersebut (bukan Python 64-bit/venv).")
    threading.Thread(target=_heartbeat, daemon=True).start(); prog(2, "connect")
    dll = load_dll(a.dll)
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int)
    dll.FK_ConnectNet.argtypes = [I, ctypes.c_char_p, I, I, I, I, I]; dll.FK_ConnectNet.restype = I
    dll.FK_DisConnect.argtypes = [I]; dll.FK_EnableDevice.argtypes = [I, I]; dll.FK_LoadGeneralLogData.argtypes = [I, I]
    dll.FK_GetLastError.argtypes = [I]; dll.FK_GetLastError.restype = I
    dll.FK_GetGeneralLogData_1.argtypes = [I, P, P, P, P, P, P, P, P, P]
    global LOAD_EST
    if a.load_est: LOAD_EST = a.load_est
    lics = [a.license] if a.license is not None else list(LICENSES); trace = []
    empty = {}
    for i, lic in enumerate(lics):
        if i: time.sleep(SESSION_GAP)                                              # beri waktu mesin melepas sesi sebelumnya
        session(dll, a, lic, last=(i == len(lics) - 1), trace=trace, empty=empty)  # keluar lewat out(); kembali hanya bila perlu mencoba lisensi berikutnya
    if empty: empty["tried"] = trace; out(ok=True, **empty)                         # sambung+muat sukses (0 scan) lalu lisensi cadangan gagal sambung → itu hasil sahih (log kosong), bukan galat
    out(ok=False, error="FK_ConnectNet gagal untuk semua lisensi: " + "; ".join(trace))


if __name__ == "__main__": main()
