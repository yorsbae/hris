"""Pembaca scanlog mesin Fingerspot (protokol FK, port 5005) lewat FKAttend.dll bawaan Fingerspot Personnel.
Dijalankan sebagai PROSES TERPISAH oleh HRIS karena DLL-nya 32-bit & hanya untuk Windows:
    python32 fk_dump.py --dll "C:\\Program Files (x86)\\Fingerspot Personnel\\FKAttend.dll" --ip 192.168.11.40 --port 5005 --machine 12 [--key 0] [--new]
    python32 fk_dump.py --dll "...\\FKAttend.dll" --exports        # diagnostik: daftar fungsi yang diekspor DLL
    python32 fk_dump.py --dll ... --ip ... --machine 12 --users-down            # baca user + sidik jari (+ password/kartu)
    python32 fk_dump.py --dll ... --ip ... --machine 12 --users-up berkas.json  # kirim user dari berkas JSON (dihapus HRIS setelah selesai)
Keluaran (satu JSON per baris):
    {"p": <persen 0-100>, "stage": "connect|disable|load|read", "n": <jumlah scan>, "t": <detik>}   ← kemajuan + detak (tiap 5 dtk, juga saat DLL sedang menunggu mesin)
    {"rows": [["PIN","YYYY-MM-DD HH:MM:SS"],...]}   ← bertahap (5000 scan per baris) agar log besar tidak jadi satu JSON raksasa
    {"users": [{"pin","name","priv","password","card","fps":[[jari,"<base64>"],...]},...]}   ← bertahap (--users-down)
    {"ok": true, "count": N, "truncated": false} atau {"ok": false, "error": "..."}   ← baris terakhir
    (--users-up) baris terakhir {"ok": true, "sent": N, "fps": M, "bad": ["PIN x: ..."]}
Tanda tangan fungsi diturunkan dari disassembly FKAttend.dll (jumlah argumen stdcall cocok); verifikasi akhir tetap pada mesin sungguhan."""
import argparse, base64, ctypes, json, math, os, struct, sys, threading, time

_T0 = time.time(); _ST = {"p": 0, "stage": "start", "n": 0}; _LOCK = threading.Lock()


def _line(d):
    with _LOCK: sys.stdout.write(json.dumps(d) + "\n"); sys.stdout.flush()


def prog(p=None, stage=None, n=None):
    if p is not None: _ST["p"] = p
    if stage is not None: _ST["stage"] = stage
    if n is not None: _ST["n"] = n
    _line(dict(_ST, t=round(time.time() - _T0, 1)))


def _heartbeat():
    while True:
        time.sleep(5); _line(dict(_ST, t=round(time.time() - _T0, 1), hb=1))     # ctypes melepas GIL saat DLL menunggu → detak tetap jalan


BATCH = 5000; CAP = 2_000_000                                                     # CAP: pengaman memori; bila tercapai → truncated=true (HRIS menolak hasil terpotong)


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


def users_down(dll, h):
    _bind_users(dll); I = ctypes.c_int
    if dll.FK_ReadAllUserID(h) != 1: out(ok=False, error=f"FK_ReadAllUserID gagal (error {dll.FK_GetLastError(h)}).")
    pin, bk, pv, en = I(), I(), I(), I(); users = {}
    while dll.FK_GetAllUserID(h, ctypes.byref(pin), ctypes.byref(bk), ctypes.byref(pv), ctypes.byref(en)) == 1:
        u = users.setdefault(pin.value, {"pin": str(pin.value), "priv": pv.value, "bks": []}); u["bks"].append(bk.value)
    prog(30, "read", 0); buf = ctypes.create_string_buffer(BUF); nm = ctypes.create_string_buffer(256); batch = []; n = len(users); skipped = fps = 0
    for i, (num, u) in enumerate(sorted(users.items())):
        u.update(name="", password="", card=0, fps=[])
        if dll.FK_GetUserName(h, num, nm) == 1: u["name"] = _txt(nm.raw)
        for b in sorted(set(u.pop("bks"))):
            ctypes.memset(buf, 0, BUF); priv, pw = I(), I()
            if dll.FK_GetEnrollData(h, num, b, ctypes.byref(priv), buf, ctypes.byref(pw)) != 1: continue
            u["priv"] = priv.value
            if b < BK_PSW: u["fps"].append([b, base64.b64encode(buf.raw[:FP_SIZE]).decode()]); fps += 1
            elif b == BK_PSW: u["password"] = str(pw.value) if pw.value else ""
            elif b == BK_CARD: u["card"] = pw.value
            else: skipped += 1                                           # wajah/palm dll.: belum didukung
        batch.append(u)
        if len(batch) >= 100: _line({"users": batch}); batch = []
        if i % 5 == 0: prog(round(30 + 65 * (i + 1) / n), "read", i + 1)
    if batch: _line({"users": batch})
    prog(95, "read", n); out(ok=True, count=n, fps=fps, skipped=skipped)


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


def read_logs(dll, h, new=False, cap=CAP):
    """Baca seluruh log yang sudah dimuat. Mesin ber-ID teks (StringID) mengembalikan PIN 0 lewat fungsi numerik → bila rekaman pertama ber-PIN 0
    dan FK_GetGeneralLogData_StringID tersedia, log dimuat ulang & dibaca lewat fungsi StringID (tata letak argumen sama: pointer 32-bit).
    → statistik {count, raw, pin0, baddate, stringid, sample}; baris scan dikirim bertahap lewat _line."""
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int); v = [I() for _ in range(9)]; refs = [ctypes.byref(x) for x in v]
    sbuf = ctypes.create_string_buffer(64); st = {"count": 0, "raw": 0, "pin0": 0, "baddate": 0, "stringid": False, "sample": None, "truncated": False}
    has_str = hasattr(dll, "FK_GetGeneralLogData_StringID")
    if has_str: dll.FK_GetGeneralLogData_StringID.argtypes = [I, ctypes.c_char_p, P, P, P, P, P, P, P, P]; dll.FK_GetGeneralLogData_StringID.restype = I
    def nxt():
        if st["stringid"]:
            ctypes.memset(sbuf, 0, 64)
            if dll.FK_GetGeneralLogData_StringID(h, sbuf, *refs[1:]) != 1: return None
            return (_txt(sbuf.raw),) + tuple(x.value for x in v[1:])
        if dll.FK_GetGeneralLogData_1(h, *refs) != 1: return None
        return (str(v[0].value),) + tuple(x.value for x in v[1:])
    rec = nxt()
    if rec and rec[0] == "0" and has_str:                                # PIN 0 = ID teks → ulangi dengan fungsi StringID
        prog(20, "load"); st["stringid"] = True
        if dll.FK_LoadGeneralLogData(h, 1 if new else 0) != 1: raise RuntimeError(f"FK_LoadGeneralLogData (ulang, StringID) gagal (error {dll.FK_GetLastError(h)}).")
        prog(30, "read", 0); rec = nxt()
    rows = []
    while rec:
        if st["count"] + st["pin0"] + st["baddate"] >= cap: st["truncated"] = True; break
        st["raw"] += 1; pin, _vm, _io, y, mo, d, hh, mi, ss = rec
        if st["sample"] is None: st["sample"] = list(rec)
        if y < 100: y += 2000                                            # beberapa firmware memberi tahun 2 digit
        if not pin or pin == "0": st["pin0"] += 1
        elif not (1 <= mo <= 12 and 1 <= d <= 31 and 0 <= hh < 24 and 0 <= mi < 60 and 0 <= ss < 60 and 1990 <= y <= 2100): st["baddate"] += 1
        else:
            rows.append([pin, f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mi:02d}:{ss:02d}"]); st["count"] += 1
            if len(rows) >= BATCH:
                _line({"rows": rows}); rows = []; prog(round(30 + 60 * (1 - math.exp(-st["count"] / 30000))), "read", st["count"])   # total tidak diketahui → persen perkiraan
        rec = nxt()
    if rows: _line({"rows": rows})
    return st


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dll", required=True); a.add_argument("--ip"); a.add_argument("--port", type=int, default=5005)
    a.add_argument("--machine", type=int, default=1); a.add_argument("--key", type=int, default=0); a.add_argument("--timeout", type=int, default=5000)
    a.add_argument("--new", action="store_true", help="hanya scan yang belum pernah dibaca (nReadMark=1)"); a.add_argument("--probe", action="store_true")
    a.add_argument("--no-disable", action="store_true", help="jangan panggil FK_EnableDevice(0) (sebagian firmware macet di sini)")
    a.add_argument("--users-down", action="store_true", help="baca semua user + sidik jari"); a.add_argument("--users-up", metavar="BERKAS.JSON", help="kirim user dari berkas JSON ke mesin")
    a.add_argument("--exports", action="store_true", help="tampilkan fungsi yang diekspor DLL lalu selesai")
    a = a.parse_args()
    if a.exports:
        try: out(ok=True, exports=exports(a.dll))
        except Exception as e: out(ok=False, error=f"Tidak dapat membaca ekspor DLL: {type(e).__name__}: {e}")
    if not a.ip: out(ok=False, error="--ip wajib.")
    if os.name != "nt": out(ok=False, error="FKAttend.dll hanya berjalan di Windows.")
    if ctypes.sizeof(ctypes.c_void_p) != 4: out(ok=False, error=f"Python yang dipakai: {sys.executable} ({8 * ctypes.sizeof(ctypes.c_void_p)}-bit). Perlu Python 32-bit (FKAttend.dll 32-bit). Pasang Python 32-bit (installer 'Windows installer (32-bit)') di folder terpisah, lalu set ATTENDANCE_FK_PYTHON32 di .env ke python.exe 32-bit tersebut (bukan Python 64-bit/venv).")
    threading.Thread(target=_heartbeat, daemon=True).start(); prog(2, "connect")
    try:
        os.add_dll_directory(os.path.dirname(os.path.abspath(a.dll))) if hasattr(os, "add_dll_directory") else None
        dll = ctypes.WinDLL(a.dll)
    except OSError as e: out(ok=False, error=f"DLL tidak dapat dimuat: {e}")
    I, P = ctypes.c_int, ctypes.POINTER(ctypes.c_int)
    dll.FK_ConnectNet.argtypes = [I, ctypes.c_char_p, I, I, I, I, I]; dll.FK_ConnectNet.restype = I
    dll.FK_DisConnect.argtypes = [I]; dll.FK_EnableDevice.argtypes = [I, I]; dll.FK_LoadGeneralLogData.argtypes = [I, I]
    dll.FK_GetLastError.argtypes = [I]; dll.FK_GetLastError.restype = I
    dll.FK_GetGeneralLogData_1.argtypes = [I, P, P, P, P, P, P, P, P, P]
    h = 0
    for lic in (0, 1261):                                                  # 0 = SDK baru, 1261 = SDK lama
        h = dll.FK_ConnectNet(a.machine, a.ip.encode(), a.port, a.timeout, 0, a.key, lic)
        if h > 0: break
    if h <= 0: out(ok=False, error=f"FK_ConnectNet gagal (kode {h}; periksa No Mesin={a.machine}, IP, port, kunci komunikasi).")
    prog(10, "connect")
    try:
        if a.probe: out(ok=True, rows=[], note="terhubung")
        if not a.no_disable: prog(12, "disable"); dll.FK_EnableDevice(h, 0)
        try:
            if a.users_down: prog(15, "read"); users_down(dll, h)
            if a.users_up: prog(12, "read"); users_up(dll, h, a.users_up)
            prog(15, "load")                                              # tahap ini bisa lama bila log mesin besar (satu panggilan DLL, tanpa laporan kemajuan)
            r = dll.FK_LoadGeneralLogData(h, 1 if a.new else 0)
            if r != 1: out(ok=False, error=f"FK_LoadGeneralLogData gagal (kode {r}, error {dll.FK_GetLastError(h)}).")
            prog(30, "read", 0)
            try: st = read_logs(dll, h, a.new)
            except RuntimeError as e: out(ok=False, error=str(e))
            prog(95, "read", st["count"]); out(ok=True, **st)
        finally:
            if not a.no_disable: dll.FK_EnableDevice(h, 1)
    finally: dll.FK_DisConnect(h)


if __name__ == "__main__": main()
