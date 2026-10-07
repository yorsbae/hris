from django.core.cache import cache
from django.conf import settings
from django.http import HttpResponse
class RateLimitLoginMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.path == "/login/" and request.method == "POST":
            n, window = settings.LOGIN_RATE_LIMIT
            key = f"login:{request.META.get('REMOTE_ADDR')}"
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
