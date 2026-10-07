from django.core.cache import cache
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from .net import client_ip
class RateLimitLoginMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.path == "/login/" and request.method == "POST":
            n, window = settings.LOGIN_RATE_LIMIT
            key = f"login:{client_ip(request)}"
            cache.add(key, 0, window)
            if cache.incr(key) > n:
                return HttpResponse("Terlalu banyak percobaan login", status=429)
        return self.get_response(request)


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
