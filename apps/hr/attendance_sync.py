"""Tarik log scan (scanlog) dari mesin Fingerspot lewat jaringan lokal → masuk ke alur impor yang sama dengan berkas (idempoten, pemetaan PIN, laporan).

Protokol 'easylink' = web server Fingerspot EasyLink SDK (Windows, biasanya http://<ip-server-sdk>:8080) dengan endpoint:
  POST /scanlog/new            sn=<SN>                          → scan yang belum pernah diunduh
  POST /scanlog/all/paging     sn=<SN>&start=YYYY-MM-DD&stop=YYYY-MM-DD&paging=<n>  → semua scan dalam rentang (berhalaman)
Respons JSON: {"Result": true, "Data": [{"SN","ScanDate","PIN","VerifyMode","IOMode","WorkCode"}], "IsSession"/"Next": ...}
CATATAN: bentuk endpoint/respons mengikuti dokumentasi SDK yang dikenal; cocokkan dengan dokumen SDK mesin Anda dan ubah `_Easylink` saja bila beda.
Keamanan: host hanya boleh alamat privat (LAN) kecuali ATTENDANCE_SYNC_ALLOW_PUBLIC=True (cegah SSRF dari form admin)."""
import csv, io, ipaddress, json, socket, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from django.conf import settings
from django.utils import timezone
from . import attendance_calc as calc, attendance_import as imp
from .models import AttendanceDevice, AttendanceImport, AttendanceRaw

TIMEOUT = getattr(settings, "ATTENDANCE_SYNC_TIMEOUT", 15)
MAX_PAGES = 200


class SyncError(Exception): pass


def check_host(host):
    """Hanya IP/hostname yang resolve ke alamat privat/loopback/link-local (jaringan lokal)."""
    host = (host or "").strip()
    if not host or any(c in host for c in "/@?# \\"): raise SyncError("Alamat mesin/server SDK tidak valid.")
    try: infos = socket.getaddrinfo(host, None)
    except socket.gaierror: raise SyncError(f"Alamat '{host}' tidak dapat di-resolve.")
    if getattr(settings, "ATTENDANCE_SYNC_ALLOW_PUBLIC", False): return
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local): raise SyncError("Hanya alamat jaringan lokal (privat) yang diizinkan.")


class _Easylink:
    def __init__(self, dev): self.dev = dev; self.base = f"http://{dev.host}:{dev.port}"
    def _post(self, path, **data):
        if self.dev.comm_key: data["key"] = self.dev.comm_key
        req = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(data).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r: body = r.read(50_000_000)
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e: raise SyncError(f"Tidak dapat terhubung ke {self.dev.host}:{self.dev.port} ({e}).")
        try: js = json.loads(body.decode("utf-8-sig"))
        except ValueError: raise SyncError("Respons server SDK bukan JSON (periksa alamat/port).")
        if not isinstance(js, dict) or js.get("Result") is False: raise SyncError(f"Server SDK menolak permintaan: {(js or {}).get('Message', 'tanpa pesan') if isinstance(js, dict) else js}")
        return js
    def fetch(self, d1, d2):
        sn = self.dev.serial or self.dev.code; rows = []
        js = self._post("/scanlog/all/paging", sn=sn, start=d1.isoformat(), stop=d2.isoformat(), paging="100")
        for _ in range(MAX_PAGES):
            rows += js.get("Data") or []
            nxt = js.get("Next") or js.get("IsSession")
            if not (nxt and js.get("Data")): break
            js = self._post("/scanlog/all/paging", sn=sn, start=d1.isoformat(), stop=d2.isoformat(), paging="100", next="1")
        return [(str(r.get("PIN", "")).strip(), str(r.get("ScanDate", "")).strip()) for r in rows if r.get("PIN") not in (None, "") and r.get("ScanDate")]


class _Zk:
    """Langsung ke mesin (seperti Fingerspot Personnel: IP + port + kunci komunikasi). Memakai pustaka `pyzk` (protokol TCP); mesin yang tidak mendukungnya → gunakan 'Tes koneksi'."""
    def __init__(self, dev): self.dev = dev
    def _conn(self):
        try: from zk import ZK
        except ImportError: raise SyncError("Pustaka pyzk belum terpasang (pip install -r requirements.txt).")
        try: pw = int(self.dev.comm_key or 0)
        except ValueError: raise SyncError("Kunci komunikasi mesin harus angka (bawaan 0).")
        return ZK(self.dev.host, port=self.dev.port, timeout=TIMEOUT, password=pw, ommit_ping=True)
    def _run(self, fn):
        z = self._conn(); c = None
        try:
            c = z.connect(); c.disable_device()
            try: return fn(c)
            finally: c.enable_device()
        except SyncError: raise
        except Exception as e: raise SyncError(f"Mesin {self.dev.host}:{self.dev.port} tidak merespons ({type(e).__name__}: {e}).")
        finally:
            try: c and c.disconnect()
            except Exception: pass
    def fetch(self, d1, d2):
        recs = self._run(lambda c: c.get_attendance())
        return [(str(a.user_id).strip(), a.timestamp.strftime("%Y-%m-%d %H:%M:%S")) for a in recs or [] if a.user_id and d1 <= a.timestamp.date() <= d2]
    def info(self): return self._run(lambda c: {"serial": c.get_serialnumber(), "time": str(c.get_time()), "users": len(c.get_users() or [])})


DRIVERS = {"easylink": _Easylink, "zk": _Zk}


def probe(dev, ports=(4370, 5005, 8080)):
    """Tes koneksi: port TCP mana yang terbuka + (protokol zk) SN & jam mesin. Dipakai untuk menentukan protokol/port tiap mesin."""
    out = {"device": dev.code, "ok": False, "open": [], "note": ""}
    try: check_host(dev.host)
    except SyncError as e: out["note"] = str(e); return out
    for pt in dict.fromkeys((dev.port,) + tuple(ports)):
        try:
            with socket.create_connection((dev.host, pt), timeout=min(TIMEOUT, 3)): out["open"].append(pt)
        except OSError: pass
    if not out["open"]: out["note"] = f"Tidak ada port terbuka di {dev.host} (mesin mati / beda jaringan / firewall)."; return out
    out["note"] = "Port terbuka: " + ", ".join(map(str, out["open"])); out["ok"] = dev.port in out["open"]
    if dev.protocol == "zk" and out["ok"]:
        try:
            i = _Zk(dev).info(); out["note"] += f" · SN mesin {i['serial']} · jam {i['time']} · {i['users']} user"
            if dev.serial and i["serial"] and dev.serial.strip().upper() != str(i["serial"]).strip().upper(): out["note"] += " · PERINGATAN: SN tidak sama dengan data HRIS"
        except SyncError as e: out["ok"] = False; out["note"] += f" · protokol zk gagal: {e}"
    elif not out["ok"]: out["note"] += f" (port {dev.port} yang diset tertutup)"
    return out


def _to_file(dev, taps):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["PIN", "Tanggal Scan", "Mesin"])
    for pin, at in taps: w.writerow([pin, at, dev.code])
    f = io.BytesIO(buf.getvalue().encode("utf-8")); f.name = f"sync_{dev.code}_{timezone.localdate()}.csv"; return f


def fetch_device(dev, d1, d2):
    """Bagian jaringan saja (tanpa akses DB → aman di thread). → (taps, error)."""
    try:
        check_host(dev.host); drv = DRIVERS.get(dev.protocol)
        if not drv: raise SyncError("Protokol tidak dikenal.")
        return drv(dev).fetch(d1, d2), ""
    except SyncError as e: return [], str(e)


def ingest(dev, taps, err="", commit=True, window=None):
    out = {"device": dev.code, "ok": False, "new": 0, "dup": 0, "fetched": len(taps), "note": err}
    if not err:
        try:
            if taps:
                i = imp.stage_upload(_to_file(dev, taps), None); imp.analyze(i)
                out["new"], out["dup"] = i.taps_new, i.taps_duplicate
                if not commit or not i.taps_new: imp.cancel(i); i.delete()          # tidak ada yang baru → tidak meninggalkan baris riwayat/berkas sementara
                else:
                    imp.commit(i, None, ignore_invalid=True)
                    out["new"], out["dup"] = i.taps_new, i.taps_duplicate
                    if i.state == "selesai": calc.process(i.date_min - timedelta(days=1), i.date_max + timedelta(days=1))
            out["ok"] = True; out["note"] = f"{out['fetched']} scan diterima, {out['new']} baru, {out['dup']} sudah ada (dilewati)." + (f" Rentang {window[0] if window[0] > ALL_FROM else 'awal'} s/d {window[1]}." if window else "")
        except ValueError as e: out["note"] = str(e)[:290]
    if commit:
        dev.last_sync_at, dev.last_sync_ok, dev.last_sync_note = timezone.now(), out["ok"], out["note"][:300]
        dev.save(update_fields=["last_sync_at", "last_sync_ok", "last_sync_note"])
    return out


ALL_FROM = date(2000, 1, 1)
OVERLAP_DAYS = getattr(settings, "ATTENDANCE_SYNC_OVERLAP_DAYS", 1)   # tumpang-tindih pengaman; duplikat dilewati, jadi tidak ada data ganda


def last_stored(dev):
    """Indikator 'data terbaru': waktu scan terakhir mesin ini yang SUDAH tersimpan di HRIS (None = belum pernah)."""
    from django.db.models import Max
    return AttendanceRaw.objects.filter(device_id=dev.code).aggregate(m=Max("punched_at"))["m"]


def window(dev, mode="latest", d1=None, d2=None):
    """mode: 'all' = seluruh log di mesin · 'latest' = sejak scan terakhir tersimpan (−OVERLAP_DAYS; mesin baru/kosong = seluruhnya) · 'range' = d1..d2 (bawaan ATTENDANCE_SYNC_DAYS hari)."""
    d2 = d2 or timezone.localdate()
    if mode == "all": return ALL_FROM, d2
    if mode == "latest" and not d1:
        last = last_stored(dev)
        return (timezone.localtime(last).date() - timedelta(days=OVERLAP_DAYS) if last else ALL_FROM), d2
    return (d1 or d2 - timedelta(days=getattr(settings, "ATTENDANCE_SYNC_DAYS", 3))), d2


def sync_device(dev, d1=None, d2=None, commit=True, mode="latest"):
    a, b = window(dev, mode, d1, d2); taps, err = fetch_device(dev, a, b); return ingest(dev, taps, err, commit, window=(a, b))


def sync_many(devs, d1=None, d2=None, commit=True, workers=8, mode="latest"):
    """Unduh banyak mesin: jaringan paralel (28 mesin tidak menunggu satu per satu), simpan ke DB berurutan."""
    from concurrent.futures import ThreadPoolExecutor
    devs = list(devs); wins = [window(d, mode, d1, d2) for d in devs]       # jendela dihitung di thread utama (butuh DB)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(devs) or 1))) as ex: got = list(ex.map(lambda x: fetch_device(x[0], *x[1]), zip(devs, wins)))
    return [ingest(d, t, e, commit, window=w) for d, (t, e), w in zip(devs, got, wins)]


def probe_many(devs, workers=8):
    from concurrent.futures import ThreadPoolExecutor
    devs = list(devs)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(devs) or 1))) as ex: return list(ex.map(probe, devs))


# ---------------------------------------------------------------- daftar mesin dari Fingerspot Personnel (Nama · SN · No Mesin · Koneksi · IP Address / URL · Layar)
HEAD = {"nama": "name", "sn": "serial", "nomesin": "machine_no", "koneksi": "connection", "ipaddressurl": "host", "ipaddress": "host", "ip": "host", "layar": "screen", "lokasi": "location"}


def import_machine_list(raw, filename="paste.txt", protocol="zk", port=4370):
    """Salin tabel Mesin dari Fingerspot Personnel (atau Export-nya) → master mesin. Kode mesin = SN. Mesin yang sudah ada (SN sama) hanya diperbarui nama/IP/dll,
    kunci komunikasi & status tidak diubah. → (dibuat, diperbarui, daftar peringatan)."""
    grid = imp.read_grid(raw, filename); cols = None; made = upd = 0; warn = []
    for r in grid:
        cells = [str(c).strip() if c is not None else "" for c in r]
        m = {HEAD[imp._norm(c)]: i for i, c in enumerate(cells) if imp._norm(c) in HEAD}
        if "serial" in m and "host" in m: cols = m; continue
        if not cols or not any(cells): continue
        g = lambda k: cells[cols[k]] if k in cols and cols[k] < len(cells) else ""   # noqa: E731
        sn, host = g("serial")[:40], g("host")[:100]
        if not sn: continue
        try: check_host(host)
        except SyncError as e: warn.append(f"{g('name') or sn}: {e}"); host_ok = False
        else: host_ok = True
        d, created = AttendanceDevice.objects.get_or_create(code=sn, defaults={"name": (g("name") or sn)[:80], "protocol": protocol, "port": port})
        d.name = (g("name") or d.name)[:80]; d.serial = sn; d.machine_no = g("machine_no")[:10]; d.screen = g("screen")[:10]
        if g("connection") in dict(AttendanceDevice.CONNECTIONS): d.connection = g("connection")
        if host_ok: d.host = host
        if created: d.sync_enabled = host_ok
        d.save(); made += created; upd += not created
    if not cols: raise ValueError("Kolom tidak dikenali. Butuh minimal kolom 'SN' dan 'IP Address / URL' (salin tabel dari Fingerspot Personnel termasuk judul kolom).")
    return made, upd, warn
