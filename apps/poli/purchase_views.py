"""Pesan obat & Terima obat (/poli/orders/, /poli/receipts/). Hanya Poli/Superadmin. Penerimaan menambah stok lewat kartu stok (purchasing.receive)."""
from datetime import date
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.core.audit import log
from . import purchasing
from .models import GoodsReceipt, Medicine, PurchaseOrder
from .pages import _date, paginate, poli_only

PILL = {"ordered": "dipesan", "partial": "pending", "received": "diterima", "closed": "batal", "cancelled": "dibatalkan"}


def _int(v):
    try: return int(str(v).strip())
    except (TypeError, ValueError): return None


# ------------------------------------------------------------------ Pesan obat
@poli_only
def order_new(request):
    q = request.GET.get("q", "").strip(); show_all = request.GET.get("all") == "1"
    rows = purchasing.order_rows(q, show_all)
    posted = {}
    if request.method == "POST":
        sel = {_int(x) for x in request.POST.getlist("sel")}
        items, bad = [], False
        for mid in sel:
            qty = _int(request.POST.get(f"qty_{mid}"))
            posted[mid] = request.POST.get(f"qty_{mid}", "")
            if mid is None: continue
            if qty is None: bad = True
            else: items.append((mid, qty))
        try:
            if bad: raise ValueError("Jumlah pesan harus berupa angka bulat")
            o = purchasing.create_order(request.user, items, request.POST.get("supplier", ""), request.POST.get("note", ""))
        except ValueError as ex:
            messages.error(request, str(ex))
            rows = purchasing.order_rows(q, show_all) if not posted else _rows_with_posted(rows, sel, show_all, q)
        else:
            log(request, "poli", "po_create", o, None, {"number": o.number, "lines": [(l.medicine.code, l.qty_ordered) for l in o.lines.select_related("medicine")]})
            messages.success(request, f"Pesanan {o.number} dibuat ({o.lines.count()} obat). Stok bertambah setelah barang diterima.")
            return redirect("poli_order_detail", pk=o.pk)
    for r in rows:
        mid = r["m"].pk
        r["checked"] = (mid in posted) if request.method == "POST" else r["suggest"] > 0
        r["qty"] = posted.get(mid, r["suggest"] or "")
    n_low = sum(1 for r in rows if r["state"] != "cukup")
    return render(request, "poli/order_form.html", {"rows": rows, "q": q, "all": show_all, "n_low": n_low, "supplier": request.POST.get("supplier", ""), "note": request.POST.get("note", "")})


def _rows_with_posted(rows, sel, show_all, q):
    have = {r["m"].pk for r in rows}
    if sel - have:   # obat terpilih yang tidak tampil di filter sekarang: tetap ditampilkan agar pilihan tidak hilang
        extra = [r for r in purchasing.order_rows("", True) if r["m"].pk in (sel - have)]
        rows = rows + extra
    return rows


@poli_only
def order_list(request):
    st = request.GET.get("status", "")
    qs = PurchaseOrder.objects.select_related("created_by").prefetch_related("lines")
    if st in dict(PurchaseOrder.STATUS): qs = qs.filter(status=st)
    elif st == "open": qs = qs.filter(status__in=PurchaseOrder.OPEN)
    page = paginate(request, qs)
    for o in page: o.pill = PILL[o.status]; o.n_lines = len(o.lines.all()); o.n_out = sum(l.outstanding for l in o.lines.all())
    return render(request, "poli/order_list.html", {"page": page, "st": st, "qs": f"status={st}" if st else "", "statuses": PurchaseOrder.STATUS})


@poli_only
def order_detail(request, pk):
    o = get_object_or_404(PurchaseOrder.objects.select_related("created_by"), pk=pk)
    lines = list(o.lines.select_related("medicine"))
    if request.GET.get("export"):
        from apps.core import tabular
        rows = [[l.medicine.code, l.medicine.name, l.medicine.unit, l.qty_ordered, l.qty_received, l.gap, l.get_state_text, l.shortfall_note] for l in lines]
        log(request, "poli", "po_export", o, None, {"rows": len(rows)})
        return tabular.export_response(o.number, ["code", "name", "unit", "qty_ordered", "qty_received", "selisih", "status", "keterangan"], rows, request, sheet="Pesanan")
    return render(request, "poli/order_detail.html", {"o": o, "lines": lines, "pill": PILL[o.status], "receipts": o.receipts.prefetch_related("lines__medicine"),
                                                      "can_receive": o.status in PurchaseOrder.OPEN})


@poli_only
def order_print(request, pk):
    """Surat pesanan obat siap cetak (HTML A4, Ctrl+P / Simpan PDF). Pencetakan diaudit."""
    from apps.core import letterhead
    o = get_object_or_404(PurchaseOrder.objects.select_related("created_by"), pk=pk)
    lines = list(o.lines.select_related("medicine"))
    log(request, "poli", "po_print", o, None, {"number": o.number, "lines": len(lines)})
    half = (len(lines) + 1) // 2  # dua kolom kiri-kanan: kiri 1..half, kanan half+1..n (nomor lanjut)
    pairs = [{"l": lines[i], "ln": i + 1, "r": lines[i + half] if i + half < len(lines) else None, "rn": i + half + 1} for i in range(half)]
    pairs += [{"l": None, "r": None}] * max(0, 10 - len(pairs))  # baris kosong sampai minimal 10 agar form terlihat utuh
    return render(request, "poli/order_print.html", {"o": o, "lines": lines, "pairs": pairs, "kop": letterhead.info(request.user), "now": date.today(), "total": sum(l.qty_ordered for l in lines)})


@poli_only
@require_POST
def order_action(request, pk, action):
    o = get_object_or_404(PurchaseOrder, pk=pk)
    try:
        if action == "cancel": purchasing.cancel_order(o, request.POST.get("note", ""))
        elif action == "close": purchasing.close_order(o, request.POST.get("note", ""))
        else: return redirect("poli_order_detail", pk=pk)
    except ValueError as ex: messages.error(request, str(ex)); return redirect("poli_order_detail", pk=pk)
    log(request, "poli", f"po_{action}", o, None, {"number": o.number, "note": request.POST.get("note", "")[:300]})
    messages.success(request, f"Pesanan {o.number} {'dibatalkan' if action == 'cancel' else 'ditutup'}."); return redirect("poli_order_detail", pk=pk)


# ------------------------------------------------------------------ Terima obat
@poli_only
def receipt_new(request):
    order = None
    if request.GET.get("order") or request.POST.get("order"):
        order = get_object_or_404(PurchaseOrder, pk=_int(request.GET.get("order") or request.POST.get("order")) or 0)
        if order.status not in PurchaseOrder.OPEN:
            messages.error(request, f"Pesanan {order.number} sudah {order.get_status_display().lower()}."); return redirect("poli_order_detail", pk=order.pk)
    lines = list(order.lines.select_related("medicine")) if order else []
    for l in lines: l.posted = request.POST.get(f"qty_{l.pk}", ""); l.posted_note = request.POST.get(f"note_{l.pk}", l.shortfall_note if request.method != "POST" else ""); l.posted_done = request.POST.get(f"done_{l.pk}") == "1"
    meds = list(Medicine.objects.order_by("name").values("pk", "code", "name", "unit")) if not order else []
    ctx = {"order": order, "lines": lines, "meds": meds, "today": date.today().isoformat(), "extra_rows": range(6), "p": request.POST}
    if request.method == "POST":
        items = []
        try:
            if order:
                for l in lines:
                    raw = request.POST.get(f"qty_{l.pk}", "").strip()
                    note, close = request.POST.get(f"note_{l.pk}", "").strip(), request.POST.get(f"done_{l.pk}") == "1"
                    if raw == "" and not note and not close: continue
                    q = _int(raw) if raw != "" else 0
                    if q is None or q < 0: raise ValueError(f"Jumlah {l.medicine.name} harus angka bulat ≥ 0")
                    items.append((l.medicine_id, q, l.pk, note, close))
            else:
                for i in range(min(_int(request.POST.get("rows")) or 0, 60)):
                    mid, raw = _int(request.POST.get(f"med_{i}")), request.POST.get(f"mqty_{i}", "").strip()
                    if mid is None and raw == "": continue
                    q = _int(raw)
                    if mid is None or q is None or q < 0: raise ValueError(f"Baris {i + 1}: pilih obat dan isi jumlah (angka bulat)")
                    items.append((mid, q, None, request.POST.get(f"mnote_{i}", ""), False))
            gr = purchasing.receive(request.user, items, _date(request.POST.get("received_on")), order, request.POST.get("supplier", ""), request.POST.get("ref", ""), request.POST.get("note", ""))
        except (ValueError, Medicine.DoesNotExist) as ex:
            messages.error(request, str(ex) or "Obat tidak ditemukan")
        else:
            if gr is None:
                log(request, "poli", "po_shortfall", order, None, {"number": order.number, "lines": [(l.medicine.code, l.shortfall_note) for l in order.lines.select_related("medicine") if l.closed]})
                messages.success(request, "Selisih pesanan dicatat (tidak ada barang masuk, stok tidak berubah)."); return redirect("poli_order_detail", pk=order.pk)
            log(request, "poli", "gr_create", gr, None, {"number": gr.number, "po": order.number if order else "", "lines": [(l.medicine.code, l.qty, l.note) for l in gr.lines.select_related("medicine")]})
            messages.success(request, f"Penerimaan {gr.number} dicatat. Stok sudah bertambah dan tercatat di kartu stok.")
            return redirect("poli_receipt_detail", pk=gr.pk)
    return render(request, "poli/receipt_form.html", ctx)


@poli_only
def receipt_list(request):
    return render(request, "poli/receipt_list.html", {"page": paginate(request, GoodsReceipt.objects.select_related("order", "created_by").prefetch_related("lines")), "qs": ""})


@poli_only
def receipt_detail(request, pk):
    g = get_object_or_404(GoodsReceipt.objects.select_related("order", "created_by"), pk=pk)
    return render(request, "poli/receipt_detail.html", {"g": g, "lines": g.lines.select_related("medicine", "movement")})
