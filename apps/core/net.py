"""Alamat IP klien yang benar di belakang reverse proxy (Nginx).

Tanpa ini, di belakang Nginx semua request tampak berasal dari 127.0.0.1: kolom IP audit tidak berguna dan rate limit login
dipakai bersama oleh SELURUH pengguna. `X-Forwarded-For` hanya dipercaya bila request benar-benar datang dari proxy yang
didaftarkan di `TRUSTED_PROXY_IPS` (satu IP atau CIDR per entri). Header dari klien biasa diabaikan, sehingga tidak bisa
dipalsukan untuk menghindari rate limit atau menyamarkan jejak di audit.
"""
import ipaddress
from django.conf import settings


def _networks():
    out = []
    for raw in getattr(settings, "TRUSTED_PROXY_IPS", None) or []:
        try: out.append(ipaddress.ip_network(str(raw).strip(), strict=False))
        except ValueError: continue  # entri salah dibuang (jangan sampai membuka kepercayaan ke semua)
    return out


def _ip(value):
    try: return ipaddress.ip_address(str(value).strip())
    except ValueError: return None


def _trusted(ip, nets): return any(ip in n for n in nets)


def client_ip(request):
    """IP klien (str) atau None. Aturan: ambil entri X-Forwarded-For dari KANAN, lewati proxy tepercaya, ambil yang pertama bukan proxy."""
    remote = request.META.get("REMOTE_ADDR")
    nets = _networks()
    r = _ip(remote)
    if not nets or r is None or not _trusted(r, nets): return remote or None
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    for part in reversed([p for p in xff.split(",") if p.strip()]):
        ip = _ip(part)
        if ip is None: return remote  # entri rusak di sisi kanan = tidak bisa dipercaya; pakai alamat proxy
        if not _trusted(ip, nets): return str(ip)
    return remote
