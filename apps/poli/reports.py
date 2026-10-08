"""Rekap stok obat (harian/bulanan) dari kartu stok (StockMovement, append-only).

Rumus per obat:  stok akhir = stok awal + masuk + retur − keluar ± penyesuaian
  • masuk        = reason purchase (penerimaan)
  • retur        = reason return  (obat dikurangi dari resep → kembali ke stok)
  • keluar       = reason prescription (resep)
  • penyesuaian  = reason adjustment (opname/rusak/kedaluwarsa, bertanda ±)
Stok awal periode = saldo setelah gerakan terakhir sebelum periode; bila belum ada gerakan sebelumnya, = saldo sebelum gerakan pertama
di dalam/sesudah periode (menangani obat lama yang punya stok tanpa baris penerimaan awal); bila tak ada gerakan sama sekali = stok sekarang.
Semua dihitung di database (agregasi + subquery), bukan dengan memuat seluruh kartu stok ke Python.
"""
import calendar
from datetime import date, datetime, time, timedelta
from django.db.models import F, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import Medicine, StockMovement


def bounds(period, d):
    """(awal, akhir-eksklusif) sebagai datetime aware lokal. period: 'day' | 'month'."""
    if period == "month": a, b = d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1]) + timedelta(days=1)
    else: a, b = d, d + timedelta(days=1)
    mk = lambda x: timezone.make_aware(datetime.combine(x, time.min))
    return mk(a), mk(b), a, b - timedelta(days=1)


def stock_report(period, d, q="", show_all=False):
    start, end, first_day, last_day = bounds(period, d)
    meds = Medicine.objects.all()
    if q: meds = meds.filter(Q(name__icontains=q) | Q(code__istartswith=q))
    mv = StockMovement.objects.filter(medicine=OuterRef("pk"))
    last_before = Subquery(mv.filter(created_at__lt=start).order_by("-id").values("balance_after")[:1], output_field=IntegerField())
    first_after = Subquery(mv.filter(created_at__gte=start).order_by("id").annotate(b=F("balance_after") - F("qty")).values("b")[:1], output_field=IntegerField())
    inp = Q(movements__created_at__gte=start, movements__created_at__lt=end)
    S = lambda cond: Coalesce(Sum("movements__qty", filter=inp & cond), 0)
    rows = meds.annotate(opening=Coalesce(last_before, first_after, F("stock")),
                         masuk=S(Q(movements__reason="purchase")), retur=S(Q(movements__reason="return")),
                         keluar=-S(Q(movements__reason="prescription")), adj=S(Q(movements__reason="adjustment"))).order_by("name")
    out, tot = [], {"opening": 0, "masuk": 0, "retur": 0, "keluar": 0, "adj": 0, "closing": 0}
    for m in rows:
        # alasan lain (bila kelak ada) tetap dihitung agar rumus selalu cocok dengan kartu stok
        closing = m.opening + m.masuk + m.retur - m.keluar + m.adj
        if not show_all and not (m.opening or m.masuk or m.retur or m.keluar or m.adj or closing): continue
        out.append({"id": m.pk, "code": m.code, "name": m.name, "unit": m.unit, "opening": m.opening, "masuk": m.masuk, "retur": m.retur,
                    "keluar": m.keluar, "adj": m.adj, "closing": closing})
        for k in tot: tot[k] += out[-1][k]
    return {"rows": out, "total": tot, "start": first_day, "end": last_day, "period": period}
