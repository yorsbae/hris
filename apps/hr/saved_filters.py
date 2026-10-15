"""Filter tersimpan (pribadi): simpan/pakai/hapus kombinasi filter sebuah halaman daftar. Hanya kunci yang dikenal yang disimpan; nilai lain dibuang."""
from urllib.parse import parse_qsl, urlencode
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from .models import SavedFilter
from .views import LIST_FILTERS

PAGES = {"employees": LIST_FILTERS + ("sort", "group", "size")}  # kunci query yang boleh disimpan per halaman
MAX_PER_PAGE = 20  # filter tersimpan per pengguna per halaman
ROLES = (Role.HRD, Role.DEPT_ADMIN, Role.POLI)


def clean_query(page, raw):
    """Saring string query ke kunci yang dikenal, buang nilai kosong/duplikat/terlalu panjang. Hasil urlencoded & stabil (terurut)."""
    keys, out = PAGES[page], {}
    for k, v in parse_qsl(raw or "", keep_blank_values=False, max_num_fields=50):
        v = v.strip()
        if k in keys and v and len(v) <= 100 and k not in out: out[k] = v
    return urlencode(sorted(out.items()))


def _row(f): return {"id": f.pk, "name": f.name, "query": f.query}


@require_roles(*ROLES)
def saved_filters(request):
    """GET ?page=employees → daftar milik sendiri. POST name, query, page → simpan (nama sama = perbarui)."""
    page = request.GET.get("page") or request.POST.get("page") or "employees"
    if page not in PAGES: return JsonResponse({"detail": "halaman tidak dikenal"}, status=400)
    qs = SavedFilter.objects.filter(user=request.user, page=page)
    if request.method == "GET": return JsonResponse({"results": [_row(f) for f in qs]})
    if request.method != "POST": return JsonResponse({"detail": "method"}, status=405)
    name, query = request.POST.get("name", "").strip(), clean_query(page, request.POST.get("query"))
    if not name or len(name) > 60: return JsonResponse({"detail": "Nama wajib diisi (maks 60 karakter)."}, status=400)
    if not query: return JsonResponse({"detail": "Belum ada filter aktif untuk disimpan."}, status=400)
    try:
        with transaction.atomic():
            f = qs.filter(name=name).first()
            if f is None and qs.count() >= MAX_PER_PAGE: return JsonResponse({"detail": f"Maksimal {MAX_PER_PAGE} filter tersimpan; hapus yang tidak dipakai."}, status=400)
            if f is None: f = SavedFilter.objects.create(user=request.user, page=page, name=name, query=query)
            else: f.query = query; f.save(update_fields=["query"])
    except IntegrityError:  # dua simpan bersamaan dengan nama sama
        return JsonResponse({"detail": "Nama sudah dipakai."}, status=409)
    log(request, "hr", "saved_filter_save", f, None, {"page": page, "name": name})
    return JsonResponse({"results": [_row(x) for x in qs], "saved": f.pk})


@require_POST
@require_roles(*ROLES)
def saved_filter_delete(request, pk):
    f = SavedFilter.objects.filter(pk=pk, user=request.user).first()  # hanya milik sendiri; milik orang lain = tidak ditemukan
    if not f: return JsonResponse({"detail": "tidak ditemukan"}, status=404)
    page = f.page; log(request, "hr", "saved_filter_delete", f, {"name": f.name}, None); f.delete()
    return JsonResponse({"results": [_row(x) for x in SavedFilter.objects.filter(user=request.user, page=page)]})
