"""Pembaca scanlog mesin Fingerspot (protokol FK, port 5005) lewat FKAttend.dll bawaan Fingerspot Personnel.
Dijalankan sebagai PROSES TERPISAH oleh HRIS karena DLL-nya 32-bit & hanya untuk Windows:
    python32 fk_dump.py --dll "C:\\Program Files (x86)\\Fingerspot Personnel\\FKAttend.dll" --ip 192.168.11.40 --port 5005 --machine 12 [--key 0] [--new]
Keluaran: satu baris JSON. {"ok":true,"rows":[["PIN","YYYY-MM-DD HH:MM:SS"],...]} atau {"ok":false,"error":"..."}.
Tanda tangan fungsi diturunkan dari disassembly FKAttend.dll (jumlah argumen stdcall cocok); verifikasi akhir tetap pada mesin sungguhan."""
import argparse, ctypes, json, os, sys


def out(**kw): sys.stdout.write(json.dumps(kw)); sys.stdout.flush(); sys.exit(0 if kw.get("ok") else 1)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dll", required=True); a.add_argument("--ip", required=True); a.add_argument("--port", type=int, default=5005)
    a.add_argument("--machine", type=int, default=1); a.add_argument("--key", type=int, default=0); a.add_argument("--timeout", type=int, default=5000)
    a.add_argument("--new", action="store_true", help="hanya scan yang belum pernah dibaca (nReadMark=1)"); a.add_argument("--probe", action="store_true")
    a = a.parse_args()
    if os.name != "nt": out(ok=False, error="FKAttend.dll hanya berjalan di Windows.")
    if ctypes.sizeof(ctypes.c_void_p) != 4: out(ok=False, error=f"Python yang dipakai: {sys.executable} ({8 * ctypes.sizeof(ctypes.c_void_p)}-bit). Perlu Python 32-bit (FKAttend.dll 32-bit). Pasang Python 32-bit (installer 'Windows installer (32-bit)') di folder terpisah, lalu set ATTENDANCE_FK_PYTHON32 di .env ke python.exe 32-bit tersebut (bukan Python 64-bit/venv).")
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
    try:
        if a.probe: out(ok=True, rows=[], note="terhubung")
        dll.FK_EnableDevice(h, 0)
        try:
            r = dll.FK_LoadGeneralLogData(h, 1 if a.new else 0)
            if r != 1: out(ok=False, error=f"FK_LoadGeneralLogData gagal (kode {r}, error {dll.FK_GetLastError(h)}).")
            v = [I() for _ in range(9)]; rows = []
            while len(rows) < 2_000_000:
                if dll.FK_GetGeneralLogData_1(h, *[ctypes.byref(x) for x in v]) != 1: break
                pin, _vm, _io, y, mo, d, hh, mi, ss = (x.value for x in v)
                if y < 100: y += 2000                                    # beberapa firmware memberi tahun 2 digit
                rows.append([str(pin), f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mi:02d}:{ss:02d}"])
            out(ok=True, rows=rows)
        finally: dll.FK_EnableDevice(h, 1)
    finally: dll.FK_DisConnect(h)


if __name__ == "__main__": main()
