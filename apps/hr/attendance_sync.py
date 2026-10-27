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
from .models import AttendanceDevice, AttendanceImport

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


DRIVERS = {"easylink": _Easylink}


def _to_file(dev, taps):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["PIN", "Tanggal Scan", "Mesin"])
    for pin, at in taps: w.writerow([pin, at, dev.code])
    f = io.BytesIO(buf.getvalue().encode("utf-8")); f.name = f"sync_{dev.code}_{timezone.localdate()}.csv"; return f


def sync_device(dev, d1=None, d2=None, commit=True):
    """Tarik scan satu mesin. Mengembalikan dict ringkasan; error jaringan dicatat di mesin (tidak melempar ke cron lain)."""
    today = timezone.localdate(); d2 = d2 or today; d1 = d1 or (d2 - timedelta(days=getattr(settings, "ATTENDANCE_SYNC_DAYS", 3)))
    out = {"device": dev.code, "ok": False, "new": 0, "dup": 0, "fetched": 0, "note": ""}
    try:
        check_host(dev.host)
        drv = DRIVERS.get(dev.protocol)
        if not drv: raise SyncError("Protokol tidak dikenal.")
        taps = drv(dev).fetch(d1, d2); out["fetched"] = len(taps)
        if taps:
            i = imp.stage_upload(_to_file(dev, taps), None); imp.analyze(i)
            if commit:
                imp.commit(i, None, ignore_invalid=True)
                out["new"], out["dup"] = i.taps_new, i.taps_duplicate
                if i.state == "selesai": calc.process(i.date_min - timedelta(days=1), i.date_max + timedelta(days=1))
            else: out["new"], out["dup"] = i.taps_new, i.taps_duplicate; imp.cancel(i)
        out["ok"] = True; out["note"] = f"{out['fetched']} scan diterima, {out['new']} baru, {out['dup']} sudah ada."
    except (SyncError, ValueError) as e: out["note"] = str(e)[:290]
    if commit:
        dev.last_sync_at, dev.last_sync_ok, dev.last_sync_note = timezone.now(), out["ok"], out["note"]
        dev.save(update_fields=["last_sync_at", "last_sync_ok", "last_sync_note"])
    return out
