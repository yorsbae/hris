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


# ---- kemajuan 0-100% per mesin: disimpan di cache (kunci job+mesin), dibaca popup lewat device_progress ----
import threading
_ctx = threading.local()


def progress_key(job, dev_id): return f"attjob:{job}:{dev_id}"


def set_progress(pct, note=""):
    """Laporkan kemajuan pekerjaan yang sedang berjalan (no-op bila tidak dijalankan dari popup)."""
    k = getattr(_ctx, "key", None)
    if not k: return
    from django.core.cache import cache
    try: cache.set(k, {"pct": max(0, min(100, int(pct))), "note": note}, 600)
    except Exception: pass                                                       # cache bermasalah tidak boleh menggagalkan unduhan


def get_progress(job, dev_id):
    from django.core.cache import cache
    return cache.get(progress_key(job, dev_id)) or {"pct": 0, "note": ""}


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


def _fpver(c):
    try: return int(c.get_fp_version() or 0)
    except Exception: return 0


class _Zk:
    """Langsung ke mesin (seperti Fingerspot Personnel: IP + port + kunci komunikasi). Memakai pustaka `pyzk` (protokol TCP); mesin yang tidak mendukungnya → gunakan 'Tes koneksi'."""
    def __init__(self, dev): self.dev = dev
    def _conn(self, udp=False):
        try: from zk import ZK
        except ImportError: raise SyncError("Pustaka pyzk belum terpasang (pip install -r requirements.txt).")
        try: pw = int(self.dev.comm_key or 0)
        except ValueError: raise SyncError("Kunci komunikasi mesin harus angka (bawaan 0).")
        kw = {"force_udp": True} if udp else {}
        return ZK(self.dev.host, port=self.dev.port, timeout=min(TIMEOUT, 8), password=pw, ommit_ping=True, **kw)
    def _run(self, fn):
        """Coba TCP dulu; bila mesin diam/tidak menjawab, ulangi lewat UDP (sebagian firmware Fingerspot/ZK hanya menjawab UDP)."""
        errs = []
        for udp in (False, True):
            z = self._conn(udp); c = None
            try:
                c = z.connect()
                try: c.disable_device()
                except Exception: pass                                   # sebagian firmware menolak disable; membaca log tetap bisa
                try: return fn(c)
                finally:
                    try: c.enable_device()
                    except Exception: pass
            except SyncError: raise
            except Exception as e: errs.append(f"{'UDP' if udp else 'TCP'}: {type(e).__name__}: {e}")
            finally:
                try: c and c.disconnect()
                except Exception: pass
        raise SyncError(f"Mesin {self.dev.host}:{self.dev.port} tidak merespons ({'; '.join(errs)[:200]}). "
                        "Jika port terbuka tetapi tidak menjawab protokol ZK, kemungkinan mesin Fingerspot berprotokol FK/EasyLink. "
                        "Pakai 'Impor berkas Fingerspot' (ekspor dari Fingerspot Personnel) atau protokol EasyLink SDK.")
    def fetch(self, d1, d2):
        recs = self._run(lambda c: c.get_attendance())
        return [(str(a.user_id).strip(), a.timestamp.strftime("%Y-%m-%d %H:%M:%S")) for a in recs or [] if a.user_id and d1 <= a.timestamp.date() <= d2]
    def info(self): return self._run(lambda c: {"serial": c.get_serialnumber(), "time": str(c.get_time()), "users": len(c.get_users() or [])})

    def users_down(self):
        """→ (users, fingers, fp_error, fp_version). Sidik jari gagal dibaca (mesin wajah-saja/firmware lain) tidak menggagalkan unduhan user;
        alasan dikembalikan. fp_version = versi algoritma sidik jari mesin (9/10; 0 = tidak diketahui)."""
        def f(c):
            users = list(c.get_users() or []); ver = _fpver(c)
            try: return users, list(c.get_templates() or []), "", ver
            except Exception as e: return users, [], f"{type(e).__name__}: {e}", ver
        return self._run(f)

    def users_up(self, items):
        """Kirim user (+sidik jari) ke mesin: tambah user baru / perbarui yang PIN-nya sama; user lain di mesin TIDAK dihapus.
        uid internal mesin dipertahankan untuk PIN yang sudah ada dan memakai uid kosong berikutnya untuk yang baru.
        Template dengan versi algoritma beda dari mesin tujuan dilewati (tidak terbaca di mesin).
        → (terkirim, jari, gagal[list pesan], jari dilewati)."""
        try: from zk.base import User; from zk.finger import Finger
        except ImportError: raise SyncError("Pustaka pyzk belum terpasang (pip install -r requirements.txt).")
        def f(c):
            have = {str(u.user_id).strip(): u.uid for u in (c.get_users() or [])}; used = set(have.values())
            nxt = 1; sent = fps = skipped = 0; bad = []; ver = _fpver(c)
            for i, it in enumerate(items):
                set_progress(10 + 85 * i / max(len(items), 1), f"Mengirim user {i + 1} dari {len(items)}"); n0 = len(it["fingers"])
                it = dict(it, fingers=[x for x in it["fingers"] if not (ver and x[3] and x[3] != ver)]); skipped += n0 - len(it["fingers"])
                uid = have.get(it["pin"])
                if uid is None:
                    while nxt in used: nxt += 1
                    uid = nxt; used.add(uid)
                try:
                    if it["fingers"]:
                        c.save_user_template(User(uid, it["name"], it["privilege"], it["password"], it["group_id"], it["pin"], it["card"]),
                                             [Finger(uid, fid, valid, tpl) for fid, valid, tpl, _v in it["fingers"]]); fps += len(it["fingers"])
                    else:
                        c.set_user(uid=uid, name=it["name"], privilege=it["privilege"], password=it["password"],
                                   group_id=it["group_id"], user_id=it["pin"], card=it["card"])
                    sent += 1
                except Exception as e: bad.append(f"PIN {it['pin']}: {type(e).__name__}: {e}"[:120])
            if sent:
                try: c.refresh_data()
                except Exception: pass
            return sent, fps, bad, skipped
        return self._run(f)


class _Fk:
    """Mesin Fingerspot berprotokol FK (port 5005, 'No Mesin' wajib benar) lewat FKAttend.dll milik Fingerspot Personnel.
    DLL 32-bit/Windows → dijalankan sebagai proses terpisah (apps/hr/fk/fk_dump.py) dengan Python 32-bit.
    Pengaturan: ATTENDANCE_FK_DLL (jalur FKAttend.dll), ATTENDANCE_FK_PYTHON32 (python.exe 32-bit; bawaan = Python yang sedang jalan)."""
    def __init__(self, dev): self.dev = dev
    STAGES = {"connect": "Menghubungi mesin", "disable": "Mengunci mesin", "load": "Mesin menyiapkan log (bisa lama bila log besar)", "read": "Membaca scan"}

    def _exec(self, cmd, on_line, limit):
        """Jalankan pembaca; setiap baris keluarannya diteruskan ke on_line. Dihentikan paksa setelah `limit` detik. → (returncode, stderr). Dipisah agar mudah dipalsukan di tes."""
        import subprocess, threading
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        err = []; rd = threading.Thread(target=lambda: err.append(pr.stderr.read()), daemon=True); rd.start()
        timer = threading.Timer(limit, pr.kill); timer.start()
        try:
            for ln in pr.stdout: on_line(ln)
            rc = pr.wait()
        finally:
            timer.cancel()
            if pr.poll() is None: pr.kill()
        rd.join(2); return rc, "".join(err)

    def _call(self, *extra, base=0, span=100):
        import sys, time
        from pathlib import Path
        dll = getattr(settings, "ATTENDANCE_FK_DLL", "") or r"C:\Program Files (x86)\Fingerspot Personnel\FKAttend.dll"
        py = getattr(settings, "ATTENDANCE_FK_PYTHON32", "") or sys.executable
        limit = getattr(settings, "ATTENDANCE_FK_TIMEOUT", 900)
        try: pw = int(self.dev.comm_key or 0)
        except ValueError: raise SyncError("Kunci komunikasi mesin harus angka (bawaan 0).")
        try: no = int(self.dev.machine_no or 1)
        except ValueError: raise SyncError("No Mesin harus angka (lihat kolom No Mesin di Fingerspot Personnel).")
        cmd = [py, str(Path(__file__).parent / "fk" / "fk_dump.py"), "--dll", dll, "--ip", self.dev.host, "--port", str(self.dev.port), "--machine", str(no), "--key", str(pw), *extra]
        if getattr(settings, "ATTENDANCE_FK_NO_DISABLE", False): cmd.append("--no-disable")
        last = {}; final = {}; t0 = time.time()
        def on_line(ln):
            try: j = json.loads(ln)
            except ValueError: return
            if not isinstance(j, dict): return
            if "ok" in j: final.update(j); return
            last.update(j); st = self.STAGES.get(j.get("stage"), "")
            set_progress(base + span * j.get("p", 0) / 100, st + (f" · {j['n']:,} scan".replace(",", ".") if j.get("n") else ""))
        try: rc, stderr = self._exec(cmd, on_line, limit)
        except OSError as e: raise SyncError(f"Tidak dapat menjalankan pembaca FK ({type(e).__name__}: {e}). Periksa ATTENDANCE_FK_PYTHON32.")
        if not final and time.time() - t0 >= limit - 1:
            raise SyncError(f"Pembaca FK dihentikan setelah {limit} detik pada tahap '{last.get('stage', '?')}'"
                            f"{' (' + str(last['n']) + ' scan terbaca)' if last.get('n') else ''}. "
                            "Tahap 'load' = mesin lambat menyiapkan log: coba unduh 'Sejak scan terakhir' atau naikkan ATTENDANCE_FK_TIMEOUT di .env. "
                            "Tahap 'disable' = firmware macet saat dikunci: set ATTENDANCE_FK_NO_DISABLE=1. Tahap 'connect' = periksa No Mesin/kunci komunikasi.")
        if not final: raise SyncError(f"Pembaca FK gagal: {(stderr or 'tanpa keluaran')[-200:]}")
        if not final.get("ok"):
            msg = final.get("error", "FK gagal.")
            if not getattr(settings, "ATTENDANCE_FK_PYTHON32", ""): msg += " [ATTENDANCE_FK_PYTHON32 kosong di server ini — pastikan ada di .env lalu RESTART runserver (.env hanya dibaca saat start)]"
            raise SyncError(msg)
        return final
    def fetch(self, d1, d2):
        out = []
        for pin, at in self._call(span=85)["rows"]:
            try: t = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
            except ValueError: continue
            if pin and pin != "0" and d1 <= t.date() <= d2: out.append((pin, at))
        return out
    def info(self): self._call("--probe"); return {"serial": self.dev.serial, "time": "-", "users": "?"}


DRIVERS = {"easylink": _Easylink, "zk": _Zk, "fk": _Fk}


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
    if dev.protocol in ("zk", "fk") and out["ok"]:
        try:
            if dev.protocol == "fk": _Fk(dev).info(); out["note"] += " · terhubung via FKAttend.dll"; return out
            i = _Zk(dev).info(); out["note"] += f" · SN mesin {i['serial']} · jam {i['time']} · {i['users']} user"
            if dev.serial and i["serial"] and dev.serial.strip().upper() != str(i["serial"]).strip().upper(): out["note"] += " · PERINGATAN: SN tidak sama dengan data HRIS"
        except SyncError as e: out["ok"] = False; out["note"] += f" · protokol {dev.protocol} gagal: {e}"
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
    set_progress(1, "Memulai"); a, b = window(dev, mode, d1, d2); taps, err = fetch_device(dev, a, b)
    set_progress(88, f"{len(taps):,} scan diterima · menyimpan ke HRIS".replace(",", ".")) if not err else None
    r = ingest(dev, taps, err, commit, window=(a, b)); set_progress(100, "Selesai" if r["ok"] else "Gagal"); return r


def sync_many(devs, d1=None, d2=None, commit=True, workers=8, mode="latest"):
    """Unduh banyak mesin: jaringan paralel (28 mesin tidak menunggu satu per satu), simpan ke DB berurutan."""
    from concurrent.futures import ThreadPoolExecutor
    devs = list(devs); wins = [window(d, mode, d1, d2) for d in devs]       # jendela dihitung di thread utama (butuh DB)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(devs) or 1))) as ex: got = list(ex.map(lambda x: fetch_device(x[0], *x[1]), zip(devs, wins)))
    return [ingest(d, t, e, commit, window=w) for d, (t, e), w in zip(devs, got, wins)]


# ---------------------------------------------------------------- user & sidik jari (Download user / Upload user)
import base64


def _clean(v, n): return str(v if v is not None else "").replace("\x00", "").strip()[:n]


def _zk_only(dev):
    check_host(dev.host)
    if dev.protocol == "fk": raise SyncError("Mesin ini berprotokol FK (FKAttend.dll): scanlog sudah didukung, tetapi Download/Upload user & sidik jari BELUM — butuh tanda tangan fungsi FK_*User* dari DLL. Jalankan: python32 apps/hr/fk/fk_dump.py --dll <FKAttend.dll> --exports, lalu kirim hasilnya agar dukungan dapat ditambahkan. Sementara itu, kelola user lewat Fingerspot Personnel.")
    if dev.protocol != "zk": raise SyncError("Download/Upload user hanya untuk protokol ZK (TCP); protokol EasyLink belum mendukung user & sidik jari (scanlog saja).")


def download_users(dev):
    """Baca user + sidik jari dari mesin → simpan/perbarui di HRIS (kunci PIN). Idempoten: template yang sama tidak dihitung 'diperbarui'.
    → {ok, users, new, updated, fingers, note}. Template tidak pernah masuk ke respons/log."""
    from django.db import transaction
    from .models import DeviceFingerprint, DeviceUser
    out = {"device": dev.code, "ok": False, "users": 0, "new": 0, "updated": 0, "fingers": 0, "note": ""}
    try:
        _zk_only(dev); set_progress(10, "Menghubungi mesin & membaca user · sidik jari"); users, fingers, fp_err, ver = _Zk(dev).users_down(); set_progress(60, f"{len(users)} user terbaca · menyimpan ke HRIS")
    except SyncError as e: out["note"] = str(e); return out
    by_uid = {}; new = upd = nfp = 0
    with transaction.atomic():
        for u in users:
            pin = _clean(getattr(u, "user_id", "") or getattr(u, "uid", ""), 24)
            if not pin: continue
            vals = {"name": _clean(u.name, 80), "privilege": int(u.privilege or 0), "password": _clean(u.password, 20), "card": int(u.card or 0),
                    "group_id": _clean(u.group_id, 10), "source_device": dev.code}
            obj, made = DeviceUser.objects.get_or_create(pin=pin, defaults=vals); by_uid[u.uid] = obj
            if made: new += 1
            elif any(getattr(obj, k) != v for k, v in vals.items() if k != "source_device"):
                for k, v in vals.items(): setattr(obj, k, v)
                obj.save(); upd += 1
        for f in fingers:
            obj = by_uid.get(f.uid)
            if not obj or not f.template: continue
            tpl = base64.b64encode(bytes(f.template)).decode()
            fp, made = DeviceFingerprint.objects.get_or_create(user=obj, finger=int(f.fid), defaults={"template": tpl, "valid": int(f.valid or 1),
                                                                                                   "fp_version": ver, "source_device": dev.code})
            if not made and fp.template != tpl: fp.template, fp.valid, fp.fp_version, fp.source_device = tpl, int(f.valid or 1), ver, dev.code; fp.save()
            nfp += 1
    set_progress(100, "Selesai"); out.update(ok=True, users=len(by_uid), new=new, updated=upd, fingers=nfp)
    out["note"] = (f"{len(by_uid)} user dibaca ({new} baru, {upd} diperbarui), {nfp} sidik jari." + (f" Algoritma sidik jari v{ver}." if ver and nfp else "")
                   + (f" Sidik jari tidak terbaca: {fp_err}" if fp_err else ""))
    return out


def upload_users(dev, pins=None, fingerprints=True):
    """Kirim user (+ sidik jari) tersimpan di HRIS ke mesin. pins = daftar PIN (None/kosong = semua). Tidak menghapus user yang sudah ada di mesin."""
    from .models import DeviceUser
    out = {"device": dev.code, "ok": False, "users": 0, "fingers": 0, "failed": 0, "note": ""}
    try: _zk_only(dev)                                                      # protokol dulu: mesin FK/EasyLink tidak perlu diberi tahu "belum ada user"
    except SyncError as e: out["note"] = str(e); return out
    qs = DeviceUser.objects.prefetch_related("fingers")
    if pins: qs = qs.filter(pin__in=pins)
    items = [{"pin": u.pin, "name": u.name, "privilege": u.privilege, "password": u.password or "", "group_id": u.group_id, "card": u.card,
              "fingers": [(f.finger, f.valid, base64.b64decode(f.template), f.fp_version) for f in u.fingers.all()] if fingerprints else []} for u in qs]
    if not items:
        out["note"] = "Belum ada user tersimpan di HRIS" + (" untuk PIN tersebut." if pins else ". Lakukan Download user dari mesin lebih dulu.")
        return out
    try: _zk_only(dev); set_progress(5, "Menghubungi mesin"); sent, fps, bad, skip = _Zk(dev).users_up(items)
    except SyncError as e: out["note"] = str(e); return out
    out.update(ok=not bad or sent > 0, users=sent, fingers=fps, failed=len(bad), skipped=skip)
    note = f"{sent} dari {len(items)} user dikirim, {fps} sidik jari."
    if skip: note += f" {skip} sidik jari dilewati (versi algoritma beda dengan mesin ini)."
    if bad: note += f" {len(bad)} gagal: " + "; ".join(bad[:3])
    out["note"] = note
    if not sent: out["ok"] = False
    set_progress(100, "Selesai" if out["ok"] else "Gagal"); return out


ACTIONS = ("sync", "test", "users_down", "users_up")


def run_action(dev, action, mode="latest", d1=None, d2=None, pins=None, fingerprints=True, job=None):
    """Satu aksi untuk SATU mesin (dipanggil per mesin oleh popup progres). job = id pekerjaan dari popup → kemajuan 0-100% dibaca lewat get_progress."""
    _ctx.key = progress_key(job, dev.pk) if job else None
    try:
        if action == "sync": return sync_device(dev, d1, d2, mode=mode)
        if action == "test": set_progress(10, "Menguji koneksi"); r = probe(dev); set_progress(100, "Selesai"); return r
        if action == "users_down": return download_users(dev)
        if action == "users_up": return upload_users(dev, pins, fingerprints)
        raise ValueError("Aksi tidak dikenal.")
    finally: _ctx.key = None


def probe_many(devs, workers=8):
    from concurrent.futures import ThreadPoolExecutor
    devs = list(devs)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(devs) or 1))) as ex: return list(ex.map(probe, devs))


# ---------------------------------------------------------------- daftar mesin dari Fingerspot Personnel (Nama · SN · No Mesin · Koneksi · IP Address / URL · Layar)
HEAD = {"nama": "name", "sn": "serial", "nomesin": "machine_no", "koneksi": "connection", "ipaddressurl": "host", "ipaddress": "host", "ip": "host", "layar": "screen", "lokasi": "location"}


def _numstr(v):
    """Excel menyimpan angka sebagai float ('11.0') → '11'."""
    try: f = float(v)
    except (TypeError, ValueError): return v
    return str(int(f)) if f == int(f) else v


def import_machine_list(raw, filename="paste.txt", protocol="zk", port=4370, set_protocol=False):
    """Salin tabel Mesin dari Fingerspot Personnel (atau Export-nya) → master mesin. Kode mesin = SN. Mesin yang sudah ada (SN sama) hanya diperbarui nama/IP/dll,
    kunci komunikasi & status tidak diubah (protokol ikut diubah hanya bila set_protocol=True). → (dibuat, diperbarui, daftar peringatan)."""
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
        d.name = (g("name") or d.name)[:80]; d.serial = sn; d.machine_no = _numstr(g("machine_no"))[:10]; d.screen = g("screen")[:10]
        if g("connection") in dict(AttendanceDevice.CONNECTIONS): d.connection = g("connection")
        if host_ok: d.host = host
        if set_protocol and not created: d.protocol = protocol
        if created: d.sync_enabled = host_ok
        d.save(); made += created; upd += not created
    if not cols: raise ValueError("Kolom tidak dikenali. Butuh minimal kolom 'SN' dan 'IP Address / URL' (salin tabel dari Fingerspot Personnel termasuk judul kolom).")
    return made, upd, warn
