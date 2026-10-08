import math
import time
from django.core.cache import cache
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.cache import patch_cache_control
from .net import client_ip


def hit(key, limit, window):
    """Jendela tetap berbasis cache (Redis bila REDIS_URL diisi). Mengembalikan (melebihi?, detik_tunggu)."""
    now = time.time()
    if cache.add(key, 1, window):
        cache.set(key + ":s", now, window); n = 1
    else:
        try: n = cache.incr(key)
        except ValueError: cache.set(key, 1, window); cache.set(key + ":s", now, window); n = 1
    start = cache.get(key + ":s", now)
    return n > limit, max(1, math.ceil(window - (now - start)))


def too_many(request, message, wait):
    if request.path.startswith("/api/"): r = JsonResponse({"detail": "too_many_requests", "retry_after": wait}, status=429)
    else: r = HttpResponse(message, status=429, content_type="text/plain; charset=utf-8")
    r["Retry-After"] = str(wait)
    return r


class RateLimitLoginMiddleware:
    LOGIN_PATHS = ("/login/", "/admin/login/")  # /admin/login/ sebelumnya tidak dibatasi

    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.path in self.LOGIN_PATHS and request.method == "POST":
            n, window = settings.LOGIN_RATE_LIMIT
            over, wait = hit(f"login:{client_ip(request)}", n, window)
            if over: return too_many(request, "Terlalu banyak percobaan login", wait)
        return self.get_response(request)


class ThrottleMiddleware:
    """Pembatasan laju per (user, IP) menurut zona (putaran 21, P1): `api`, `export`, `upload`; batas di `settings.THROTTLE_ZONES`
    ({zona: (jumlah, detik)}; kosong = nonaktif, dipakai di tes). Lewat batas → 429 + `Retry-After`. Pelengkap `limit_req` di Nginx."""
    def __init__(self, get_response): self.get_response = get_response

    @staticmethod
    def zones(request):
        z, p = [], request.path
        if request.method in ("GET", "HEAD") and (request.GET.get("export") or "/export/" in p): z.append("export")
        if p.startswith("/api/"): z.append("api")
        if request.method == "POST" and request.content_type == "multipart/form-data": z.append("upload")
        return z

    def __call__(self, request):
        cfg = getattr(settings, "THROTTLE_ZONES", None)
        if cfg:
            u = getattr(request, "user", None)
            who = f"u{u.pk}" if u is not None and u.is_authenticated else "anon"
            for z in self.zones(request):
                if z not in cfg: continue
                limit, window = cfg[z]
                over, wait = hit(f"thr:{z}:{who}:{client_ip(request)}", limit, window)
                if over: return too_many(request, "Terlalu banyak permintaan. Coba lagi sebentar lagi.", wait)
        return self.get_response(request)


class IdleTimeoutMiddleware:
    """Sesi berakhir bila tidak ada aktivitas selama `SESSION_IDLE_TIMEOUT` detik (0 = nonaktif). Stempel aktivitas disegarkan paling sering
    tiap `min(60, timeout/4)` detik agar tidak menulis sesi di setiap request (konsekuensi: batas sebenarnya bisa ≤ selisih itu lebih awal)."""
    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        t = getattr(settings, "SESSION_IDLE_TIMEOUT", 0)
        u = getattr(request, "user", None)
        if t and u is not None and u.is_authenticated and not request.path.startswith("/static/"):
            now, last = int(time.time()), request.session.get("_last")
            if last is not None and now - last > t:
                from django.contrib.auth import logout
                from .models import AuditLog
                AuditLog.objects.create(user=u, ip=client_ip(request), module="auth", action="session_timeout")
                logout(request)
                if request.path.startswith("/api/"): return JsonResponse({"detail": "session_expired"}, status=401)
                return redirect(f"/login/?timeout=1&next={request.path}" if request.method == "GET" else "/login/?timeout=1")
            if last is None or now - last >= min(60, t // 4): request.session["_last"] = now
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """CSP, Permissions-Policy, dan `Cache-Control: no-store` untuk halaman bagi user yang login (data pribadi/medis jangan tertinggal di cache
    peramban bersama). CSP masih mengizinkan 'unsafe-inline' karena halaman memakai gaya/skrip inline; migrasi ke nonce = utang (lihat PROGRESS)."""
    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        r = self.get_response(request)
        if settings.CSP_POLICY and "Content-Security-Policy" not in r: r["Content-Security-Policy"] = settings.CSP_POLICY
        if "Permissions-Policy" not in r: r["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
        u = getattr(request, "user", None)
        if u is not None and u.is_authenticated and not request.path.startswith("/static/") and "Cache-Control" not in r:
            patch_cache_control(r, private=True, no_store=True)
        return r


class RejectNulMiddleware:
    """Tolak byte NUL (0x00) di parameter GET/POST dengan 400. PostgreSQL menolak NUL di teks (DataError → 500) sedangkan SQLite
    menolerirnya, sehingga bug ini tidak terlihat di tes SQLite. Satu pintu terpusat agar setiap view (lama & baru) aman."""
    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD", "POST") and (self._bad(request.GET) or (request.method == "POST" and self._bad(request.POST))):
            return HttpResponse("Permintaan tidak valid", status=400)
        return self.get_response(request)

    @staticmethod
    def _bad(qd): return any("\x00" in k or any("\x00" in v for v in qd.getlist(k)) for k in qd)


class ForcePasswordChangeMiddleware:
    """User yang sandinya dibuat/direset Superadmin (`must_change_password`) hanya boleh membuka halaman ganti sandi dan keluar.
    Dipasang SETELAH AuthenticationMiddleware. Halaman lain → redirect; /api/ → 403 JSON."""
    ALLOWED = ("/password/change/", "/logout/", "/login/")

    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u is not None and u.is_authenticated and getattr(u, "must_change_password", False) \
                and request.path not in self.ALLOWED and not request.path.startswith("/static/"):
            if request.path.startswith("/api/"): return JsonResponse({"detail": "password_change_required"}, status=403)
            return redirect("/password/change/")
        return self.get_response(request)
