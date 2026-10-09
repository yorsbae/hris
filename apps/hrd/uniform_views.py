"""Rekap Seragam (putaran 23, P5): pembelian seragam + ukuran, tarif potongan L/P berlaku-sejak, rekap, ekspor XLSX/CSV, impor.
Hanya HRD/Superadmin (Admin Dept & Poli → 403). Pembelian tidak diubah/dihapus: koreksi = pembatalan beralasan; 'sudah dipotong' satu arah."""
from collections import OrderedDict
from datetime import date
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.hr.models import Department
from . import services
from .forms import UniformPurchaseForm, UniformRateForm, UniformStockForm
from .models import UniformPurchase, UniformRate, UniformSize, UniformStock, UniformStockMovement, UniformType
from .views import _int, hrd_only, paginate

EXPORT_MAX = 20000
SHOW_EMPLOYEES = 100


def _month_range(period):
    """'YYYY-MM' → (awal, awal bulan berikutnya) atau None bila tidak valid."""
    if not services._period_valid(period): return None
    y, m = int(period[:4]), int(period[5:])
    return date(y, m, 1), (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))


def _filters(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "period", "department", "utype", "size", "status", "batal")}
    if "period" not in request.GET: f["period"] = date.today().strftime("%Y-%m")   # bawaan bulan ini; "period=" (kosong) = semua periode
    return f


def _base(f):
    """Pembelian terfilter, TERMASUK yang dibatalkan (pemanggil memilih)."""
    qs = UniformPurchase.objects.select_related("employee", "employee__department", "utype", "size")
    rng = _month_range(f["period"])
    if rng: qs = qs.filter(purchase_date__gte=rng[0], purchase_date__lt=rng[1])
    if _int(f["department"]): qs = qs.filter(employee__department_id=_int(f["department"]))
    if _int(f["utype"]): qs = qs.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): qs = qs.filter(size_id=_int(f["size"]))
    if f["status"] in ("belum", "sudah"): qs = qs.filter(deduction_status=f["status"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    return qs


def _stock_recap(f, live):
    """Rekap STOK jenis × ukuran untuk periode filter (menggantikan 'rekap ukuran' putaran 23): stok awal, masuk, keluar (L/P/total dari pembelian aktif),
    koreksi/kembali (residual: koreksi + pembatalan), saldo akhir. Memakai kartu stok (UniformStockMovement), bukan hitung ulang pembelian. Hanya periode/jenis/ukuran
    yang berlaku; filter karyawan/departemen/status tidak relevan untuk stok. Baris: semua jenis×ukuran yang punya gerak/pembelian atau saldo ≠ 0."""
    rng = _month_range(f["period"]); mv = UniformStockMovement.objects.all()
    if _int(f["utype"]): mv = mv.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): mv = mv.filter(size_id=_int(f["size"]))
    key = lambda r: (r["utype__name"], r["size__sort"], r["size__code"])
    grp = lambda qs: qs.order_by().values("utype__name", "size__sort", "size__code")
    rows = OrderedDict()

    def row(r):
        return rows.setdefault(key(r), {"utype": r["utype__name"], "size": r["size__code"], "awal": 0, "masuk": 0, "L": 0, "P": 0, "keluar": 0, "adj": 0, "saldo": 0})
    for r in grp(mv).annotate(n=Sum("quantity")): row(r)["saldo"] = r["n"]   # saldo akhir = semua gerak s/d akhir periode (di bawah dikoreksi)
    if rng:
        for r in grp(mv.filter(movement_date__gte=rng[1])).annotate(n=Sum("quantity")): row(r)["saldo"] -= r["n"]
        for r in grp(mv.filter(movement_date__lt=rng[0])).annotate(n=Sum("quantity")): row(r)["awal"] = r["n"]
        inr = mv.filter(movement_date__gte=rng[0], movement_date__lt=rng[1])
    else: inr = mv
    for r in grp(inr.filter(kind="masuk")).annotate(n=Sum("quantity")): row(r)["masuk"] = r["n"]
    lv = live
    if _int(f["utype"]): lv = lv.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): lv = lv.filter(size_id=_int(f["size"]))
    for r in lv.order_by().values("utype__name", "size__sort", "size__code", "gender").annotate(n=Sum("quantity")):
        x = row(r); x[r["gender"]] += r["n"]; x["keluar"] += r["n"]
    out = []
    for k in sorted(rows):
        x = rows[k]
        if not (x["awal"] or x["masuk"] or x["keluar"] or x["saldo"]): continue
        x["adj"] = x["saldo"] - x["awal"] - x["masuk"] + x["keluar"]; x["minus"] = x["saldo"] < 0; out.append(x)
    return out


def _detail_row(p):
    return (p.employee.nik, p.employee.name, p.employee.department.name, p.purchase_date, p.utype.name, p.size.code, p.gender, p.quantity, p.rate_amount, p.deduction_amount,
            p.get_deduction_status_display(), p.deducted_period, "batal" if p.is_void else "aktif", p.void_reason, p.note)


DETAIL_HEADER = ["nik", "nama", "departemen", "tanggal", "jenis", "ukuran", "jk", "jumlah", "tarif_per_satuan", "total_potongan", "status_potongan", "periode_potong", "status", "alasan_batal", "catatan"]


@hrd_only
def uniforms(request):
    f = _filters(request); base = _base(f); live = base.filter(voided_at__isnull=True); shown = base if f["batal"] else live
    kind = request.GET.get("export")
    if kind:
        which = "recap" if kind == "recap" else "detail"
        log(request, "hrd", "uniform_export", None, None, {"kind": which, "period": f["period"], "rows": shown.count() if which == "detail" else live.count()})
        if which == "recap":
            rows = [(r["utype"], r["size"], r["awal"], r["masuk"], r["L"], r["P"], r["keluar"], r["adj"], r["saldo"]) for r in _stock_recap(f, live)]
            return tabular.export_response(f"rekap-stok-seragam-{f['period'] or 'semua'}", ["jenis", "ukuran", "stok_awal", "masuk", "keluar_laki_laki", "keluar_perempuan", "keluar_total", "koreksi_kembali", "stok_akhir"],
                                           rows, request, sheet="Rekap Stok Seragam", num_cols=("stok_awal", "masuk", "keluar_laki_laki", "keluar_perempuan", "keluar_total", "koreksi_kembali", "stok_akhir"))
        rows = [_detail_row(p) for p in shown.order_by("purchase_date", "employee__name", "id")[:EXPORT_MAX]]
        return tabular.export_response(f"seragam-{f['period'] or 'semua'}", DETAIL_HEADER, rows, request, sheet="Seragam",
                                       money_cols=("tarif_per_satuan", "total_potongan"), num_cols=("jumlah",))
    totals = live.aggregate(n=Count("id"), pcs=Sum("quantity"), total=Sum("deduction_amount"), belum=Sum("deduction_amount", filter=Q(deduction_status="belum")))
    by_dept = list(live.order_by().values("employee__department__name").annotate(n=Count("id"), pcs=Sum("quantity"), total=Sum("deduction_amount")).order_by("employee__department__name"))
    emp_qs = live.order_by().values("employee__nik", "employee__name", "employee__department__name").annotate(pcs=Sum("quantity"), total=Sum("deduction_amount")).order_by("-total", "employee__name")
    from urllib.parse import urlencode
    ctx = {"page": paginate(request, shown.order_by("-purchase_date", "-id")), "f": f, "totals": totals, "by_dept": by_dept, "stock": _stock_recap(f, live), "low_n": sum(1 for b in UniformStock.objects.filter(min_stock__gt=0) if b.low), "minus_n": UniformStock.objects.filter(balance__lt=0).count(),
           "employees": list(emp_qs[:SHOW_EMPLOYEES]), "employees_n": live.order_by().values("employee_id").distinct().count(),
           "departments": Department.objects.order_by("name"), "utypes": UniformType.objects.order_by("name"), "sizes": UniformSize.objects.all(),
           "qs": urlencode({k: v for k, v in f.items() if v or k == "period"})}
    return render(request, "hrd/uniforms.html", ctx)


@hrd_only
def uniform_new(request):
    initial = {}
    d = request.GET.get("date")
    try: initial["purchase_date"] = date.fromisoformat(d) if d else date.today()
    except ValueError: initial["purchase_date"] = date.today()
    form = UniformPurchaseForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            p = form.save()
            log(request, "hrd", "uniform_purchase", p, None, {"employee": form.employee.nik, "date": str(p.purchase_date), "type": p.utype.name, "size": p.size.code, "qty": p.quantity,
                                                               "rate": str(p.rate_amount), "total": str(p.deduction_amount)})
        messages.success(request, f"Pembelian seragam dicatat. Potongan {p.quantity} × tarif = tersimpan.")
        if request.POST.get("again"): return redirect(f"/hrd/uniforms/new/?date={p.purchase_date.isoformat()}")
        return redirect(f"/hrd/uniforms/?period={p.purchase_date:%Y-%m}")
    return render(request, "hrd/form.html", {"form": form, "title": "Catat pembelian seragam", "back": "/hrd/uniforms/", "again": True,
                                             "hint": "Tarif potongan diambil otomatis dari jenis kelamin karyawan dan tanggal pembelian (Master Seragam), lalu disalin ke catatan ini. "
                                                     "Catatan tidak dapat diubah; bila keliru, batalkan dengan alasan lalu catat ulang."})


@hrd_only
def uniform_detail(request, pk):
    p = get_object_or_404(UniformPurchase.objects.select_related("employee", "employee__department", "utype", "size", "created_by", "voided_by"), pk=pk)
    return render(request, "hrd/uniform_detail.html", {"p": p, "this_period": date.today().strftime("%Y-%m")})


@hrd_only
@require_POST
def uniform_void(request, pk):
    p = get_object_or_404(UniformPurchase, pk=pk)
    try:
        with transaction.atomic():
            p = services.void_uniform_purchase(pk, request.POST.get("reason", ""), request.user)
            log(request, "hrd", "uniform_void", p, None, {"reason": p.void_reason, "employee": p.employee.nik, "total": str(p.deduction_amount)})
        messages.success(request, "Pembelian dibatalkan (tidak dihitung di rekap).")
    except ValueError as e: messages.error(request, str(e))
    return redirect(f"/hrd/uniforms/{pk}/")


@hrd_only
@require_POST
def uniform_mark(request, pk):
    get_object_or_404(UniformPurchase, pk=pk)
    try:
        with transaction.atomic():
            p = services.mark_uniform_deducted(pk, request.POST.get("period", ""), request.user)
            log(request, "hrd", "uniform_mark_deducted", p, None, {"period": p.deducted_period, "employee": p.employee.nik, "total": str(p.deduction_amount)})
        messages.success(request, f"Ditandai sudah dipotong pada periode {p.deducted_period}.")
    except ValueError as e: messages.error(request, str(e))
    return redirect(f"/hrd/uniforms/{pk}/")


@hrd_only
def uniform_master(request):
    """Master jenis, ukuran, dan tarif. Jenis/ukuran hanya dinonaktifkan (bukan dihapus); tarif hanya ditambah (append-only)."""
    rate_form = UniformRateForm(request.POST or None, prefix="r") if request.POST.get("action") == "add_rate" else UniformRateForm(prefix="r")
    if request.method == "POST":
        act, name = request.POST.get("action", ""), request.POST
        try:
            with transaction.atomic():
                if act == "add_type":
                    v = name.get("name", "").strip()
                    if not v or len(v) > 80: raise ValueError("Nama jenis wajib diisi (maks. 80 karakter).")
                    if UniformType.objects.filter(name__iexact=v).exists(): raise ValueError("Jenis dengan nama itu sudah ada.")
                    t = UniformType.objects.create(name=v); log(request, "hrd", "uniform_master_type_add", t, None, {"name": v})
                elif act in ("toggle_type", "toggle_size"):
                    model = UniformType if act == "toggle_type" else UniformSize
                    o = model.objects.select_for_update().filter(pk=_int(name.get("id"))).first()
                    if not o: raise ValueError("Data tidak ditemukan.")
                    o.is_active = not o.is_active; o.save(update_fields=["is_active"])
                    log(request, "hrd", "uniform_master_" + ("type" if model is UniformType else "size") + "_toggle", o, None, {"active": o.is_active, "label": str(o)})
                elif act == "add_size":
                    v = name.get("code", "").strip().upper()
                    if not v or len(v) > 10: raise ValueError("Kode ukuran wajib diisi (maks. 10 karakter).")
                    if UniformSize.objects.filter(code__iexact=v).exists(): raise ValueError("Ukuran dengan kode itu sudah ada.")
                    nxt = (UniformSize.objects.aggregate(m=Max("sort"))["m"] or 0) + 10
                    z = UniformSize.objects.create(code=v, sort=_int(name.get("sort")) if _int(name.get("sort")) is not None and 0 <= _int(name.get("sort")) <= 32000 else nxt)
                    log(request, "hrd", "uniform_master_size_add", z, None, {"code": v})
                elif act == "add_rate":
                    if not rate_form.is_valid(): raise ValueError("")
                    d = rate_form.cleaned_data
                    r = UniformRate.objects.create(gender=d["gender"], amount=d["amount"], effective_from=d["effective_from"], created_by=request.user)
                    log(request, "hrd", "uniform_master_rate_add", r, None, {"gender": r.gender, "amount": str(r.amount), "effective_from": str(r.effective_from)})
                else: raise ValueError("Aksi tidak dikenal.")
            messages.success(request, "Master seragam disimpan."); return redirect("hrd_uniform_master")
        except ValueError as e:
            if str(e): messages.error(request, str(e))
    today = date.today()
    rates = list(UniformRate.objects.order_by("gender", "-effective_from"))
    current = {g: services.uniform_rate_for(g, today) for g, _ in UniformRate.GENDERS}
    return render(request, "hrd/uniform_master.html", {"types": UniformType.objects.order_by("name"), "sizes": UniformSize.objects.all(), "rates": rates, "current": current, "rate_form": rate_form})


@hrd_only
def uniform_stock_in(request): return _stock_form(request, "masuk")


@hrd_only
def uniform_stock_adjust(request): return _stock_form(request, "koreksi")


def _stock_form(request, mode):
    form = UniformStockForm(request.POST or None, mode=mode)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            m = services.stock_move(d["utype"].pk, d["size"].pk, mode, d["quantity"], d["movement_date"], request.user, note=d["note"])
            log(request, "hrd", "uniform_stock_" + mode, m, None, {"type": d["utype"].name, "size": d["size"].code, "qty": m.quantity, "balance": m.balance_after, "note": m.note})
        messages.success(request, f"Stok {d['utype'].name} {d['size'].code} dicatat ({m.quantity:+d}); saldo sekarang {m.balance_after} pcs.")
        return redirect("hrd_uniform_stock")
    masuk = mode == "masuk"
    return render(request, "hrd/form.html", {"form": form, "title": "Barang masuk seragam" if masuk else "Koreksi stok seragam", "back": "/hrd/uniforms/stock/",
                                             "hint": "Catat barang yang diterima dari vendor; stok bertambah. Catatan tidak dapat diubah." if masuk else
                                                     "Untuk selisih hasil hitung fisik/rusak/hilang. Alasan wajib; catatan tidak dapat diubah (koreksi berikutnya = baris baru)."})


@hrd_only
def uniform_stock(request):
    """Kartu stok: saldo per jenis × ukuran + riwayat gerak (masuk/keluar/kembali/koreksi), difilter jenis, ukuran, dan periode."""
    f = _filters(request); mv = UniformStockMovement.objects.select_related("utype", "size", "purchase", "created_by")
    rng = _month_range(f["period"])
    if rng: mv = mv.filter(movement_date__gte=rng[0], movement_date__lt=rng[1])
    if _int(f["utype"]): mv = mv.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): mv = mv.filter(size_id=_int(f["size"]))
    if request.method == "POST":
        t, z, n = UniformType.objects.filter(pk=_int(request.POST.get("utype"))).first(), UniformSize.objects.filter(pk=_int(request.POST.get("size"))).first(), _int(request.POST.get("min_stock"))
        try:
            if not (t and z): raise ValueError("Pilih jenis dan ukuran.")
            with transaction.atomic():
                st, old = services.set_min_stock(t.pk, z.pk, n)
                log(request, "hrd", "uniform_stock_min", st, {"min": old}, {"type": t.name, "size": z.code, "min": st.min_stock})
            messages.success(request, f"Minimum stok {t.name} {z.code} = {st.min_stock} pcs.")
        except ValueError as e: messages.error(request, str(e))
        return redirect(request.get_full_path())
    balances = UniformStock.objects.select_related("utype", "size")
    if _int(f["utype"]): balances = balances.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): balances = balances.filter(size_id=_int(f["size"]))
    if request.GET.get("export"):
        log(request, "hrd", "uniform_stock_export", None, None, {"period": f["period"]})
        rows = [(m.movement_date, m.utype.name, m.size.code, m.get_kind_display(), m.quantity, m.balance_after, m.note) for m in mv.order_by("movement_date", "id")[:EXPORT_MAX]]
        return tabular.export_response(f"kartu-stok-seragam-{f['period'] or 'semua'}", ["tanggal", "jenis", "ukuran", "gerak", "jumlah", "saldo_sesudah", "catatan"], rows, request, sheet="Kartu Stok",
                                       num_cols=("jumlah", "saldo_sesudah"))
    from urllib.parse import urlencode
    return render(request, "hrd/uniform_stock.html", {"page": paginate(request, mv), "f": f, "balances": list(balances), "minus": any(b.balance < 0 for b in balances), "low_n": sum(1 for b in balances if b.low),
                                                      "utypes": UniformType.objects.order_by("name"), "sizes": UniformSize.objects.all(),
                                                      "qs": urlencode({k: v for k, v in f.items() if k in ("period", "utype", "size") and (v or k == "period")})})
