"""Penelusuran audit log (Superadmin saja; role lain → 403). Hanya baca: AuditLog append-only dan tidak ada aksi ubah/hapus di sini.

Performa: tabel ini tumbuh paling cepat. Tanpa filter tanggal eksplisit, bawaan = 7 hari terakhir (rentang terindeks `created_at`);
daftar tidak memuat kolom JSON before/after (hanya di halaman detail); urut `-id` (monoton, memakai indeks PK).
Membuka detail sebuah entri dicatat sendiri di audit (`audit/view_entry`) karena before/after bisa memuat data pribadi.
"""
import ipaddress
import json
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .audit import log
from .models import AuditLog
from .scope import superadmin_only

PER_PAGE = 50
DEFAULT_DAYS = 7


def _date(v):
    """Tanggal ISO dalam rentang wajar. Di luar 2000–2100 dianggap tidak valid (tanggal ekstrem → OverflowError saat +1 hari / konversi zona waktu)."""
    try: d = date.fromisoformat(v)
    except (TypeError, ValueError): return None
    return d if 2000 <= d.year <= 2100 else None


def _ip(v):
    try: return str(ipaddress.ip_address(v.strip()))
    except (AttributeError, ValueError): return None


def _dt(d, end=False):
    return timezone.make_aware(datetime.combine(d + timedelta(days=1) if end else d, time.min))


@superadmin_only
def audit_list(request):
    g = request.GET
    today = timezone.localdate()
    if "from" in g or "to" in g:  # filter tanggal dikosongkan sengaja = tanpa batas
        dfrom, dto = _date(g.get("from")), _date(g.get("to"))
    else:
        dfrom, dto = today - timedelta(days=DEFAULT_DAYS - 1), today
    f = {"from": dfrom.isoformat() if dfrom else "", "to": dto.isoformat() if dto else "",
         "module": g.get("module", "").strip().lower(), "action": g.get("action", "").strip().lower(),
         "user": g.get("user", "").strip(), "object_type": g.get("object_type", "").strip(), "object_id": g.get("object_id", "").strip(),
         "ip": g.get("ip", "").strip()}
    qs = AuditLog.objects.select_related("user").defer("before", "after").order_by("-id")
    if dfrom: qs = qs.filter(created_at__gte=_dt(dfrom))
    if dto: qs = qs.filter(created_at__lt=_dt(dto, end=True))
    if f["module"]: qs = qs.filter(module=f["module"])
    if f["action"]: qs = qs.filter(action=f["action"])
    if f["user"]: qs = qs.filter(user__username__iexact=f["user"])
    if f["object_type"]: qs = qs.filter(object_type=f["object_type"])
    if f["object_id"]: qs = qs.filter(object_id=f["object_id"])
    if f["ip"]:
        ip = _ip(f["ip"])
        qs = qs.filter(ip=ip) if ip else qs.none()  # IP tidak valid → hasil kosong (bukan diabaikan diam-diam)
    page = Paginator(qs, PER_PAGE).get_page(g.get("page", 1))
    qstr = urlencode({k: v for k, v in f.items() if v or k in ("from", "to")})
    return render(request, "audit/list.html", {"page": page, "f": f, "qs": qstr, "default_days": DEFAULT_DAYS})


@superadmin_only
def audit_detail(request, pk):
    e = get_object_or_404(AuditLog.objects.select_related("user"), pk=pk)
    log(request, "audit", "view_entry", e)
    fmt = lambda v: "" if v is None else json.dumps(v, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    return render(request, "audit/detail.html", {"e": e, "before": fmt(e.before), "after": fmt(e.after)})
