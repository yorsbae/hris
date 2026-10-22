"""Health check (/health/, /healthz) + logika pemeriksaan yang dipakai perintah `check_health` (alarm). Hanya memuat status ringkas: TIDAK ada path, versi,
nama berkas, atau pesan galat mentah di keluaran (endpoint ini tanpa login, sebaiknya dibatasi ke LAN/monitor lewat Nginx — lihat scripts/nginx-hris.conf.example)."""
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache

log = logging.getLogger("hris.health")


def _db():
    try:
        with connection.cursor() as c: c.execute("SELECT 1")
        return True, "ok"
    except Exception:  # noqa: BLE001
        log.exception("Health: database tidak bisa dihubungi"); return False, "database tidak dapat dihubungi"


def pending_migrations():
    ex = MigrationExecutor(connection)
    return len(ex.migration_plan(ex.loader.graph.leaf_nodes()))


def _migrations():
    try: n = pending_migrations()
    except Exception:  # noqa: BLE001
        log.exception("Health: gagal memeriksa migrasi"); return False, "migrasi tidak dapat diperiksa"
    return (n == 0, "ok" if n == 0 else f"{n} migrasi belum dijalankan")


def _cache():
    try: cache.set("hris:health", "1", 10); ok = cache.get("hris:health") == "1"
    except Exception:  # noqa: BLE001
        log.exception("Health: cache bermasalah"); ok = False
    return ok, "ok" if ok else "cache tidak berfungsi"


def _disk():
    path = Path(settings.MEDIA_ROOT)
    try:
        free_mb = shutil.disk_usage(path if path.exists() else settings.BASE_DIR).free // (1024 * 1024)
    except OSError:
        return False, "ruang disk tidak dapat diperiksa"
    need = getattr(settings, "HEALTH_MIN_FREE_MB", 500)
    return free_mb >= need, f"ruang bebas {free_mb} MB" if free_mb >= need else f"ruang bebas tinggal {free_mb} MB (minimum {need} MB)"


def _key():
    ok = bool(getattr(settings, "FIELD_ENCRYPTION_KEY", ""))
    return ok, "ok" if ok else "kunci enkripsi belum diisi"


def _backup(now=None):
    """Status dari berkas JSON yang ditulis scripts/backup.sh: {"ok": bool, "finished_at": ISO-8601 UTC, "error": "..."}.
    Gagal bila backup terakhir gagal, atau terlalu tua (backup tidak berjalan sama sekali juga terdeteksi). Berkas belum ada = gagal hanya bila HEALTH_REQUIRE_BACKUP."""
    now = now or datetime.now(dt_tz.utc)
    p, require = Path(getattr(settings, "BACKUP_STATUS_FILE", "")), getattr(settings, "HEALTH_REQUIRE_BACKUP", False)
    if not getattr(settings, "BACKUP_STATUS_FILE", ""): return (not require), "pemantauan backup tidak dikonfigurasi"
    try: d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError: return (not require), "belum ada catatan backup"
    except (OSError, ValueError): return False, "catatan backup tidak terbaca"
    if not d.get("ok"): return False, "backup terakhir GAGAL"
    try: t = datetime.fromisoformat(str(d.get("finished_at")).replace("Z", "+00:00"))
    except ValueError: return False, "waktu backup tidak valid"
    if t.tzinfo is None: t = t.replace(tzinfo=dt_tz.utc)
    age = now - t; limit = timedelta(hours=getattr(settings, "BACKUP_MAX_AGE_HOURS", 26))
    h = int(age.total_seconds() // 3600)
    return (age <= limit, f"backup terakhir {h} jam lalu" if age <= limit else f"backup terakhir {h} jam lalu (batas {int(limit.total_seconds() // 3600)} jam)")


def _drill(now=None):
    """Status latihan restore (scripts/restore_drill.sh → restore_drill.json). Gagal bila latihan terakhir GAGAL atau terlalu lama tidak dijalankan.
    Berkas belum ada = gagal hanya bila HEALTH_REQUIRE_DRILL; tanpa DRILL_STATUS_FILE pemantauan ini mati."""
    now = now or datetime.now(dt_tz.utc)
    f, require = getattr(settings, "DRILL_STATUS_FILE", ""), getattr(settings, "HEALTH_REQUIRE_DRILL", False)
    if not f: return (not require), "pemantauan latihan restore tidak dikonfigurasi"
    try: d = json.loads(Path(f).read_text(encoding="utf-8"))
    except FileNotFoundError: return (not require), "belum pernah ada latihan restore"
    except (OSError, ValueError): return False, "catatan latihan restore tidak terbaca"
    if not d.get("ok"): return False, "latihan restore terakhir GAGAL"
    try: t = datetime.fromisoformat(str(d.get("finished_at")).replace("Z", "+00:00"))
    except ValueError: return False, "waktu latihan restore tidak valid"
    if t.tzinfo is None: t = t.replace(tzinfo=dt_tz.utc)
    age = now - t; limit = timedelta(days=getattr(settings, "DRILL_MAX_AGE_DAYS", 35)); days = age.days
    return (age <= limit, f"latihan restore terakhir {days} hari lalu" if age <= limit else f"latihan restore terakhir {days} hari lalu (batas {limit.days} hari)")


CHECKS = (("database", _db), ("migrasi", _migrations), ("cache", _cache), ("disk", _disk), ("kunci_enkripsi", _key), ("backup", _backup), ("latihan_restore", _drill))


def collect():
    checks = {}
    for name, fn in CHECKS:
        ok, detail = fn(); checks[name] = {"ok": bool(ok), "detail": detail}
    return {"status": "ok" if all(c["ok"] for c in checks.values()) else "fail", "checks": checks}


def failing(result): return sorted(k for k, c in result["checks"].items() if not c["ok"])


@never_cache
def health(request):
    """Pemeriksaan lengkap. 200 bila semua baik; 503 bila ada yang gagal (cocok untuk uptime monitor)."""
    r = collect()
    return JsonResponse(r, status=200 if r["status"] == "ok" else 503, json_dumps_params={"ensure_ascii": False})


@never_cache
def healthz(request):
    """Liveness ringan: hanya memastikan proses hidup dan database terjangkau (untuk load balancer / systemd)."""
    ok, _ = _db()
    return HttpResponse("ok" if ok else "database down", content_type="text/plain", status=200 if ok else 503)
