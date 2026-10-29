"""Pengadaan obat: saran pesanan dari stok habis/menipis, pembuatan PO, dan penerimaan barang yang LANGSUNG menambah stok (kartu stok)."""
import uuid
from datetime import date
from django.db import transaction
from django.db.models import F, Sum
from .models import GoodsReceipt, GoodsReceiptLine, Medicine, PurchaseOrder, PurchaseOrderLine
from .services import _move

MAX_QTY = 1_000_000


def target_stock(m): return max(m.min_stock * 2, m.min_stock + 1)   # tujuan pengisian: 2× minimum


def pending_map():
    """{obat: jumlah masih ditunggu} dari pesanan yang belum selesai (agar tidak memesan dua kali)."""
    d = {}
    for l in PurchaseOrderLine.objects.filter(order__status__in=PurchaseOrder.OPEN).values("medicine_id", "qty_ordered", "qty_received"):
        d[l["medicine_id"]] = d.get(l["medicine_id"], 0) + max(l["qty_ordered"] - l["qty_received"], 0)
    return d


def order_rows(q="", show_all=False):
    """Baris form pesan obat: habis (stok 0) lebih dulu, lalu menipis (stok <= minimum). show_all → semua obat."""
    qs = Medicine.objects.all()
    if q: qs = qs.filter(name__icontains=q) | qs.filter(code__istartswith=q)
    if not show_all: qs = qs.filter(stock__lte=F("min_stock"))
    pend = pending_map(); rows = []
    for m in qs.order_by("stock", "name")[:2000]:
        p = pend.get(m.pk, 0)
        rows.append({"m": m, "pending": p, "suggest": max(target_stock(m) - m.stock - p, 0), "state": "habis" if m.stock == 0 else ("menipis" if m.stock <= m.min_stock else "cukup")})
    return rows


def _num(prefix, obj):
    return f"{prefix}-{obj.created_at:%y%m}-{obj.pk:05d}"


def _qty(v, label):
    if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= MAX_QTY): raise ValueError(f"Jumlah {label} harus bilangan bulat 1–{MAX_QTY:,}".replace(",", "."))
    return v


@transaction.atomic
def create_order(user, items, supplier="", note=""):
    """items: [(medicine_id, qty)]. Hanya yang dipilih dipesan; tanpa stok yang berubah (stok baru bertambah saat barang DITERIMA)."""
    merged = {}
    for mid, qty in items: merged[mid] = merged.get(mid, 0) + _qty(qty, "pesanan")
    if not merged: raise ValueError("Pilih minimal satu obat untuk dipesan")
    meds = {m.pk: m for m in Medicine.objects.filter(pk__in=merged)}
    if len(meds) != len(merged): raise ValueError("Ada obat yang tidak ditemukan")
    o = PurchaseOrder.objects.create(number=uuid.uuid4().hex[:20], supplier=supplier.strip()[:150], note=note.strip()[:300], created_by=user)
    o.number = _num("PO", o); o.save(update_fields=["number"])
    PurchaseOrderLine.objects.bulk_create([PurchaseOrderLine(order=o, medicine=meds[mid], qty_ordered=q, stock_at_order=meds[mid].stock) for mid, q in merged.items()])
    return o


def _status(order):
    ls = list(order.lines.all())
    if all(l.outstanding == 0 for l in ls): return PurchaseOrder.CLOSED if any(l.qty_received < l.qty_ordered for l in ls) else PurchaseOrder.RECEIVED   # selesai; ada selisih final → "Ditutup"
    return PurchaseOrder.PARTIAL if any(l.qty_received or l.closed for l in ls) else PurchaseOrder.ORDERED


@transaction.atomic
def receive(user, items, received_on, order=None, supplier="", ref="", note=""):
    """items: [(medicine_id, qty, po_line_id|None, keterangan, tandai_tidak_datang)].
    Satu transaksi: penerimaan + stok + kartu stok + selisih per baris pesanan. Contoh: pesan 8 datang 4 → sisa 4 MENUNGGU (keterangan opsional) atau,
    bila dicentang 'sisa tidak akan datang', ditutup final dengan alasan WAJIB. Baris yang tidak datang sama sekali: qty 0 + tandai + alasan (tanpa mutasi stok).
    Mengembalikan GoodsReceipt, atau None bila hanya menandai selisih (tidak ada barang masuk)."""
    items = [(m, q or 0, l, (n or "").strip()[:200], bool(c)) for m, q, l, n, c in items]
    stock_items = [i for i in items if i[1]]
    flags = [i for i in items if i[4] or (i[3] and not i[1] and i[2])]
    if not stock_items and not flags: raise ValueError("Isi jumlah diterima, atau tandai obat yang tidak datang beserta keterangannya")
    if stock_items and (received_on is None or received_on > date.today()): raise ValueError("Tanggal diterima tidak boleh kosong atau di masa depan")
    lines = {}
    if order:
        order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
        if order.status not in PurchaseOrder.OPEN: raise ValueError(f"Pesanan {order.number} sudah {order.get_status_display().lower()}; tidak bisa menerima lagi")
        lines = {l.pk: l for l in order.lines.select_for_update().select_related("medicine")}
    seen = set()
    for mid, qty, lid, n, close in items:
        if qty: _qty(qty, "diterima")
        if order:
            if lid not in lines or lines[lid].medicine_id != mid: raise ValueError("Baris tidak termasuk pesanan ini")
            if (mid, lid) in seen: raise ValueError("Obat yang sama muncul dua kali")
            seen.add((mid, lid))
            l = lines[lid]
            if lid and close:
                if not n: raise ValueError(f"Keterangan wajib diisi untuk {l.medicine.name} yang ditandai tidak akan datang")
                if l.qty_received + qty >= l.qty_ordered: raise ValueError(f"{l.medicine.name}: tidak ada sisa yang perlu ditandai (jumlah sudah lengkap)")
    gr = None
    if stock_items:
        merged = {}
        for mid, qty, lid, n, close in stock_items:
            k = (mid, lid); old = merged.get(k, (0, ""))
            merged[k] = (old[0] + qty, n or old[1])
        gr = GoodsReceipt.objects.create(number=uuid.uuid4().hex[:20], order=order, supplier=(supplier or (order.supplier if order else "")).strip()[:150], ref=ref.strip()[:40],
                                         note=note.strip()[:300], received_on=received_on, created_by=user)
        gr.number = _num("GR", gr); gr.save(update_fields=["number"])
        for (mid, lid), (qty, n) in merged.items():
            m, mv = _move(mid, qty, user, "purchase", ref=gr.number, note=" · ".join(x for x in (gr.supplier, gr.ref, f"PO {order.number}" if order else "", n) if x))
            GoodsReceiptLine.objects.create(receipt=gr, medicine=m, qty=qty, po_line_id=lid, movement=mv, note=n)
            if lid: PurchaseOrderLine.objects.filter(pk=lid).update(qty_received=F("qty_received") + qty)
    if order:
        for mid, qty, lid, n, close in items:                       # selisih per baris: keterangan + penutupan final
            upd = {}
            if close: upd["closed"] = True
            if n and (close or lines[lid].qty_received + qty < lines[lid].qty_ordered): upd["shortfall_note"] = n
            if upd: PurchaseOrderLine.objects.filter(pk=lid).update(**upd)
        order.status = _status(order); order.save(update_fields=["status"])
    return gr


@transaction.atomic
def cancel_order(order, note=""):
    o = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if o.status != PurchaseOrder.ORDERED or o.receipts.exists(): raise ValueError("Hanya pesanan yang belum menerima barang yang bisa dibatalkan (gunakan Tutup untuk pesanan sebagian)")
    o.status = PurchaseOrder.CANCELLED; o.closed_note = note.strip()[:300]; o.save(update_fields=["status", "closed_note"]); return o


@transaction.atomic
def close_order(order, note):
    o = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if o.status != PurchaseOrder.PARTIAL: raise ValueError("Hanya pesanan yang diterima sebagian yang bisa ditutup")
    if not (note or "").strip(): raise ValueError("Alasan penutupan wajib diisi")
    for l in o.lines.all():
        if l.outstanding: PurchaseOrderLine.objects.filter(pk=l.pk).update(closed=True, shortfall_note=note.strip()[:200])
    o.status = PurchaseOrder.CLOSED; o.closed_note = note.strip()[:300]; o.save(update_fields=["status", "closed_note"]); return o
