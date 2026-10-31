"""Pembaca scanlog mesin Fingerspot (protokol FK, port 5005) lewat FKAttend.dll bawaan Fingerspot Personnel.
Dijalankan sebagai PROSES TERPISAH oleh HRIS karena DLL-nya 32-bit & hanya untuk Windows:
    python32 fk_dump.py --dll "C:\\Program Files (x86)\\Fingerspot Personnel\\FKAttend.dll" --ip 192.168.11.40 --port 5005 --machine 12 [--key 0] [--new]
    python32 fk_dump.py --dll "...\\FKAttend.dll" --exports        # diagnostik: daftar fungsi yang diekspor DLL
Keluaran (satu JSON per baris):
    {"p": <persen 0-100>, "stage": "connect|disable|load|read", "n": <jumlah scan>, "t": <detik>}   ← kemajuan + detak (tiap 5 dtk, juga saat DLL sedang menunggu mesin)
    {"rows": [["PIN","YYYY-MM-DD HH:MM:SS"],...]}   ← bertahap (5000 scan per baris) agar log besar tidak jadi satu JSON raksasa
    {"ok": true, "count": N, "truncated": false} atau {"ok": false, "error": "..."}   ← baris terakhir
Tanda tangan fungsi diturunkan dari disassembly FKAttend.dll (jumlah argumen stdcall cocok); verifikasi akhir tetap pada mesin sungguhan."""
import argparse, ctypes, json, math, os, struct, sys, threading, time

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


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dll", required=True); a.add_argument("--ip"); a.add_argument("--port", type=int, default=5005)
    a.add_argument("--machine", type=int, default=1); a.add_argument("--key", type=int, default=0); a.add_argument("--timeout", type=int, default=5000)
    a.add_argument("--new", action="store_true", help="hanya scan yang belum pernah dibaca (nReadMark=1)"); a.add_argument("--probe", action="store_true")
    a.add_argument("--no-disable", action="store_true", help="jangan panggil FK_EnableDevice(0) (sebagian firmware macet di sini)")
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
            prog(15, "load")                                              # tahap ini bisa lama bila log mesin besar (satu panggilan DLL, tanpa laporan kemajuan)
            r = dll.FK_LoadGeneralLogData(h, 1 if a.new else 0)
            if r != 1: out(ok=False, error=f"FK_LoadGeneralLogData gagal (kode {r}, error {dll.FK_GetLastError(h)}).")
            prog(30, "read", 0); v = [I() for _ in range(9)]; rows = []; refs = [ctypes.byref(x) for x in v]; total = 0; truncated = False
            while True:
                if total >= CAP: truncated = True; break
                if dll.FK_GetGeneralLogData_1(h, *refs) != 1: break
                pin, _vm, _io, y, mo, d, hh, mi, ss = (x.value for x in v)
                if y < 100: y += 2000                                    # beberapa firmware memberi tahun 2 digit
                rows.append([str(pin), f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mi:02d}:{ss:02d}"]); total += 1
                if len(rows) >= BATCH:
                    _line({"rows": rows}); rows = []; prog(round(30 + 60 * (1 - math.exp(-total / 30000))), "read", total)   # total tidak diketahui → persen perkiraan
            if rows: _line({"rows": rows})
            prog(95, "read", total); out(ok=True, count=total, truncated=truncated)
        finally:
            if not a.no_disable: dll.FK_EnableDevice(h, 1)
    finally: dll.FK_DisConnect(h)


if __name__ == "__main__": main()
