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
