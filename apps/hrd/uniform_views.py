"""Seragam (putaran 23 P5 → disatukan putaran 38): SATU halaman `/hrd/uniforms/` berpusat pada Kartu Stok dengan empat tab —
Stok (saldo, barang masuk/koreksi, minimum, perlu dipesan), Pembelian karyawan, Riwayat gerak, Master & tarif. Alamat lama (`stock/`, `stock/in/`, `stock/adjust/`,
`master/`) tetap hidup sebagai pintu masuk ke tab yang sama. Hanya HRD/Superadmin (Admin Dept & Poli → 403).
Pembelian tidak diubah/dihapus: koreksi = pembatalan beralasan; 'sudah dipotong' satu arah."""
from collections import OrderedDict
from datetime import date
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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


TABS = ("stok", "pembelian", "riwayat", "master")
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
    """SATU tabel stok jenis × ukuran untuk periode filter (menggantikan 'rekap stok' + 'saldo saat ini' yang dulu terpisah): stok awal, masuk, keluar (L/P/total dari
    pembelian aktif), koreksi/kembali (residual: koreksi + pembatalan), stok akhir periode; ditambah minimum, saldo SEKARANG, status (menipis/minus) dan jumlah yang perlu dipesan.
    Memakai kartu stok (UniformStockMovement). Filter karyawan/departemen/status tidak relevan untuk stok. Baris: jenis×ukuran yang punya gerak/pembelian, saldo ≠ 0, atau minimum > 0."""
    rng = _month_range(f["period"]); mv = UniformStockMovement.objects.all()
    if _int(f["utype"]): mv = mv.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): mv = mv.filter(size_id=_int(f["size"]))
    key = lambda r: (r["utype__name"], r["size__sort"], r["size__code"])
    grp = lambda qs: qs.order_by().values("utype_id", "utype__name", "size_id", "size__sort", "size__code")
    rows = OrderedDict()

    def row(r):
        return rows.setdefault(key(r), {"utype": r["utype__name"], "size": r["size__code"], "utype_id": r["utype_id"], "size_id": r["size_id"], "awal": 0, "masuk": 0, "L": 0, "P": 0,
                                        "keluar": 0, "adj": 0, "saldo": 0, "min": 0, "now": 0, "low": False, "order_qty": 0})
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
    for r in lv.order_by().values("utype_id", "utype__name", "size_id", "size__sort", "size__code", "gender").annotate(n=Sum("quantity")):
        x = row(r); x[r["gender"]] += r["n"]; x["keluar"] += r["n"]
    cur = UniformStock.objects.select_related("utype", "size")
    if _int(f["utype"]): cur = cur.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): cur = cur.filter(size_id=_int(f["size"]))
    for b in cur:
        x = row({"utype__name": b.utype.name, "size__sort": b.size.sort, "size__code": b.size.code, "utype_id": b.utype_id, "size_id": b.size_id})
        x["min"], x["now"], x["low"] = b.min_stock, b.balance, b.low
        x["order_qty"] = max(0, b.min_stock - b.balance) if b.min_stock else 0
        if not rng: x["saldo"] = b.balance
    out = []
    for k in sorted(rows):
        x = rows[k]
        if not (x["awal"] or x["masuk"] or x["keluar"] or x["saldo"] or x["min"]): continue
        x["adj"] = x["saldo"] - x["awal"] - x["masuk"] + x["keluar"]; x["minus"] = x["saldo"] < 0; x["now_minus"] = x["now"] < 0; out.append(x)
    return out


def _detail_row(p):
    return (p.employee.nik, p.employee.name, p.employee.department.name, p.purchase_date, p.utype.name, p.size.code, p.gender, p.quantity, p.rate_amount, p.deduction_amount,
            p.get_deduction_status_display(), p.deducted_period, "batal" if p.is_void else "aktif", p.void_reason, p.note)


DETAIL_HEADER = ["nik", "nama", "departemen", "tanggal", "jenis", "ukuran", "jk", "jumlah", "tarif_per_satuan", "total_potongan", "status_potongan", "periode_potong", "status", "alasan_batal", "catatan"]




# ---------------------------------------------------------------- Halaman tunggal Seragam
def _stock_move_post(request, action):
    """Barang masuk / koreksi dari form sebaris di tab Stok. Mengembalikan (redirect|None, form)."""
    form = UniformStockForm(request.POST, mode=action)
    if form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            m = services.stock_move(d["utype"].pk, d["size"].pk, action, d["quantity"], d["movement_date"], request.user, note=d["note"])
            log(request, "hrd", "uniform_stock_" + action, m, None, {"type": d["utype"].name, "size": d["size"].code, "qty": m.quantity, "balance": m.balance_after, "note": m.note})
        messages.success(request, f"Stok {d['utype'].name} {d['size'].code} dicatat ({m.quantity:+d}); saldo sekarang {m.balance_after} pcs.")
        return redirect(f"{reverse('hrd_uniforms')}?tab=stok"), form
    return None, form


def _min_stock_post(request):
    t, z, n = UniformType.objects.filter(pk=_int(request.POST.get("utype"))).first(), UniformSize.objects.filter(pk=_int(request.POST.get("size"))).first(), _int(request.POST.get("min_stock"))
    try:
        if not (t and z): raise ValueError("Pilih jenis dan ukuran.")
        with transaction.atomic():
            st, old = services.set_min_stock(t.pk, z.pk, n)
            log(request, "hrd", "uniform_stock_min", st, {"min": old}, {"type": t.name, "size": z.code, "min": st.min_stock})
        messages.success(request, f"Minimum stok {t.name} {z.code} = {st.min_stock} pcs.")
    except ValueError as e: messages.error(request, str(e))


def _master_post(request, rate_form):
    """Master jenis, ukuran, dan tarif. Jenis/ukuran hanya dinonaktifkan (bukan dihapus); tarif hanya ditambah (append-only). Mengembalikan True bila berhasil."""
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
        messages.success(request, "Master seragam disimpan."); return True
    except ValueError as e:
        if str(e): messages.error(request, str(e))
    return False


def _export(request, f, base, live, shown, kind):
    if kind == "recap":
        log(request, "hrd", "uniform_export", None, None, {"kind": "recap", "period": f["period"], "rows": live.count()})
        rows = [(r["utype"], r["size"], r["awal"], r["masuk"], r["L"], r["P"], r["keluar"], r["adj"], r["saldo"]) for r in _stock_recap(f, live)]
        return tabular.export_response(f"rekap-stok-seragam-{f['period'] or 'semua'}", ["jenis", "ukuran", "stok_awal", "masuk", "keluar_laki_laki", "keluar_perempuan", "keluar_total", "koreksi_kembali", "stok_akhir"],
                                       rows, request, sheet="Rekap Stok Seragam", num_cols=("stok_awal", "masuk", "keluar_laki_laki", "keluar_perempuan", "keluar_total", "koreksi_kembali", "stok_akhir"))
    if kind == "order":
        # Daftar pesanan ke vendor: semua jenis×ukuran di bawah minimum (termasuk minus), jumlah pesan = minimum − saldo. Tidak bergantung filter periode.
        rows = [(b.utype.name, b.size.code, b.balance, b.min_stock, b.min_stock - b.balance) for b in UniformStock.objects.select_related("utype", "size").filter(min_stock__gt=0) if b.balance < b.min_stock]
        log(request, "hrd", "uniform_stock_export", None, None, {"kind": "order", "rows": len(rows)})
        return tabular.export_response("pesanan-seragam-vendor", ["jenis", "ukuran", "stok_sekarang", "minimum", "jumlah_dipesan"], rows, request, sheet="Pesanan Vendor", num_cols=("stok_sekarang", "minimum", "jumlah_dipesan"))
    if kind == "ledger":
        mv = _movements(f); log(request, "hrd", "uniform_stock_export", None, None, {"period": f["period"]})
        rows = [(m.movement_date, m.utype.name, m.size.code, m.get_kind_display(), m.quantity, m.balance_after, m.note) for m in mv.order_by("movement_date", "id")[:EXPORT_MAX]]
        return tabular.export_response(f"kartu-stok-seragam-{f['period'] or 'semua'}", ["tanggal", "jenis", "ukuran", "gerak", "jumlah", "saldo_sesudah", "catatan"], rows, request, sheet="Kartu Stok", num_cols=("jumlah", "saldo_sesudah"))
    log(request, "hrd", "uniform_export", None, None, {"kind": "detail", "period": f["period"], "rows": shown.count()})
    rows = [_detail_row(p) for p in shown.order_by("purchase_date", "employee__name", "id")[:EXPORT_MAX]]
    return tabular.export_response(f"seragam-{f['period'] or 'semua'}", DETAIL_HEADER, rows, request, sheet="Seragam", money_cols=("tarif_per_satuan", "total_potongan"), num_cols=("jumlah",))


def _movements(f):
    mv = UniformStockMovement.objects.select_related("utype", "size", "purchase", "created_by"); rng = _month_range(f["period"])
    if rng: mv = mv.filter(movement_date__gte=rng[0], movement_date__lt=rng[1])
    if _int(f["utype"]): mv = mv.filter(utype_id=_int(f["utype"]))
    if _int(f["size"]): mv = mv.filter(size_id=_int(f["size"]))
    return mv


@hrd_only
def uniforms(request, tab=None, mode=None):
    """Halaman tunggal Seragam. `tab` bawaan dari alamat (alamat lama) atau ?tab=; `mode` ('in'/'adjust') membuka form barang masuk/koreksi."""
    f = _filters(request); tab = request.GET.get("tab") or tab or "stok"
    if tab not in TABS: tab = "stok"
    mode = mode or {"masuk": "in", "koreksi": "adjust"}.get(request.GET.get("form"))      # ?form=in|adjust membuka form di tab Stok
    sform, rate_form, open_form = None, UniformRateForm(prefix="r"), bool(mode)
    sform_mode = {"in": "masuk", "adjust": "koreksi"}.get(mode, "masuk")
    if request.GET.get("form") in ("masuk", "koreksi"): sform_mode, open_form = request.GET["form"], True
    if request.method == "POST":
        act = request.POST.get("action", "") or ("min" if "min_stock" in request.POST else sform_mode)
        if act in ("masuk", "koreksi"):
            resp, sform = _stock_move_post(request, act)
            if resp: return resp
            tab, open_form, sform_mode = "stok", True, act
        elif act == "min":
            _min_stock_post(request); return redirect(request.get_full_path())
        else:
            tab = "master"
            if act == "add_rate": rate_form = UniformRateForm(request.POST, prefix="r")
            if _master_post(request, rate_form): return redirect(f"{reverse('hrd_uniforms')}?tab=master")
    base = _base(f); live = base.filter(voided_at__isnull=True); shown = base if f["batal"] else live
    kind = request.GET.get("export")
    if kind: return _export(request, f, base, live, shown, "ledger" if kind == "1" else (kind if kind in ("recap", "order", "ledger") else "detail"))
    totals = live.aggregate(n=Count("id"), pcs=Sum("quantity"), total=Sum("deduction_amount"), belum=Sum("deduction_amount", filter=Q(deduction_status="belum")))
    belum_n = live.filter(deduction_status="belum").count()
    from urllib.parse import urlencode
    stocks = list(UniformStock.objects.all())
    ctx = {"f": f, "tab": tab, "totals": totals, "belum_n": belum_n, "low_n": sum(1 for b in stocks if b.low), "minus_n": sum(1 for b in stocks if b.balance < 0),
           "departments": Department.objects.order_by("name"), "utypes": UniformType.objects.order_by("name"), "sizes": UniformSize.objects.all(),
           "qs": urlencode({k: v for k, v in f.items() if v or k == "period"}), "mode": sform_mode, "open_form": open_form, "flow_now": 1 if not stocks else (3 if belum_n else 2),
           "needs_n": sum(1 for b in stocks if b.low or b.balance < 0), "needs_badge": str(sum(1 for b in stocks if b.low or b.balance < 0) or ""),
           "sform": sform or UniformStockForm(mode=sform_mode, initial={"movement_date": date.today()}), "rate_form": rate_form}
    if tab == "stok":
        stock = _stock_recap(f, live); ctx["stock"] = stock; ctx["order_n"] = sum(1 for r in stock if r["order_qty"])
    elif tab == "pembelian":
        ctx["pqs"] = ctx["qs"] + "&tab=pembelian"; ctx["page"] = paginate(request, shown.order_by("-purchase_date", "-id"))
        ctx["by_dept"] = list(live.order_by().values("employee__department__name").annotate(n=Count("id"), pcs=Sum("quantity"), total=Sum("deduction_amount")).order_by("employee__department__name"))
        emp_qs = live.order_by().values("employee__nik", "employee__name", "employee__department__name").annotate(pcs=Sum("quantity"), total=Sum("deduction_amount")).order_by("-total", "employee__name")
        ctx["employees"], ctx["employees_n"] = list(emp_qs[:SHOW_EMPLOYEES]), live.order_by().values("employee_id").distinct().count()
    elif tab == "riwayat":
        ctx["pqs"] = ctx["qs"] + "&tab=riwayat"; ctx["page"] = ctx["mpage"] = paginate(request, _movements(f))
    else:
        today = date.today()
        ctx.update(types=UniformType.objects.order_by("name"), rates=list(UniformRate.objects.order_by("gender", "-effective_from")),
                   current={g: services.uniform_rate_for(g, today) for g, _ in UniformRate.GENDERS})
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

