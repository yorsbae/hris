"""Tagihan Mitra (putaran 24, P6). SEMUA view: Poli/Superadmin saja (HRD & Admin Dept → 403): tagihan memuat keluhan & diagnosa = data medis.
Keluhan terenkripsi; keluhan/diagnosa TIDAK masuk audit (hanya penanda); membuka detail tagihan tercatat. Tagihan tidak diubah/dihapus: tolak (alasan) lalu catat ulang."""
from collections import OrderedDict
from datetime import date
from urllib.parse import urlencode
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.hr.models import Department
from . import services
from .forms import BillLineFormSet, PartnerBillForm, PartnerForm
from .models import Diagnosis, Partner, PartnerBill
from .pages import paginate, poli_only

EXPORT_MAX = 20000
TOP_EMPLOYEES = 100


def _period_range(v):
    if not (v and len(v) == 7 and v[4] == "-" and v[:4].isdigit() and v[5:].isdigit() and 2000 <= int(v[:4]) <= 2100 and 1 <= int(v[5:]) <= 12): return None
    y, m = int(v[:4]), int(v[5:]); return date(y, m, 1), (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _filters(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "period", "partner", "status", "payer", "department", "service_type", "diagnosis")}
    if "period" not in request.GET: f["period"] = date.today().strftime("%Y-%m")     # periode = bulan TANGGAL PELAYANAN; kosong = semua
    return f


def _qs(f):
    qs = PartnerBill.objects.select_related("partner", "employee", "employee__department", "diagnosis")
    rng = _period_range(f["period"])
    if rng: qs = qs.filter(service_date__gte=rng[0], service_date__lt=rng[1])
    if _int(f["partner"]): qs = qs.filter(partner_id=_int(f["partner"]))
    if f["status"] in dict(PartnerBill.STATUSES): qs = qs.filter(status=f["status"])
    if f["payer"] in dict(PartnerBill.PAYERS): qs = qs.filter(payer=f["payer"])
    if f["service_type"] in dict(PartnerBill.SERVICES): qs = qs.filter(service_type=f["service_type"])
    if _int(f["department"]): qs = qs.filter(employee__department_id=_int(f["department"]))
    if f["diagnosis"]: qs = qs.filter(diagnosis__code__iexact=f["diagnosis"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]) | Q(bill_number__icontains=f["q"]))
    return qs


SUMMARY_HEADER = ["mitra", "nomor_tagihan", "tanggal_tagihan", "tanggal_layanan", "nik", "nama", "departemen", "jenis_layanan", "penanggung", "total", "status", "tanggal_bayar", "nomor_bukti"]
DETAIL_HEADER = SUMMARY_HEADER + ["diagnosa", "keluhan"]


def _row(b, medical):
    base = [b.partner.name, b.bill_number, b.bill_date, b.service_date, b.employee.nik, b.employee.name, b.employee.department.name, b.get_service_type_display(), b.get_payer_display(),
            b.total_amount, b.get_status_display(), b.paid_date or "", b.payment_ref]
    return base + [str(b.diagnosis) if b.diagnosis else "", b.complaint] if medical else base


@poli_only
def bills(request):
    f = _filters(request); qs = _qs(f); kind = request.GET.get("export")
    if kind:
        medical = kind == "detail"
        log(request, "poli", "bill_export", None, None, {"kind": "detail" if medical else "summary", "period": f["period"], "rows": qs.count()})   # isi medis tidak masuk audit
        rows = [_row(b, medical) for b in qs.order_by("service_date", "id")[:EXPORT_MAX]]
        return tabular.export_response(f"tagihan-mitra-{f['period'] or 'semua'}" + ("-medis" if medical else ""), DETAIL_HEADER if medical else SUMMARY_HEADER, rows, request,
                                       sheet="Tagihan Mitra", money_cols=("total",))
    live = qs.exclude(status="ditolak")        # rekap uang tidak menghitung yang ditolak
    by_status = OrderedDict((k, {"label": lbl, "n": 0, "total": 0}) for k, lbl in PartnerBill.STATUSES)
    for r in qs.order_by().values("status").annotate(n=Count("id"), total=Sum("total_amount")): by_status[r["status"]].update(n=r["n"], total=r["total"] or 0)
    outstanding = live.exclude(status="dibayar").aggregate(t=Sum("total_amount"))["t"] or 0
    ctx = {"page": paginate(request, qs.order_by("-service_date", "-id")), "f": f, "by_status": list(by_status.values()), "outstanding": outstanding,
           "total_live": live.aggregate(t=Sum("total_amount"))["t"] or 0,
           "by_partner": list(live.order_by().values("partner__name").annotate(n=Count("id"), total=Sum("total_amount")).order_by("-total")),
           "by_dept": list(live.order_by().values("employee__department__name").annotate(n=Count("id"), total=Sum("total_amount")).order_by("employee__department__name")),
           "by_diag": list(live.order_by().values("diagnosis__code", "diagnosis__name").annotate(n=Count("id"), total=Sum("total_amount")).order_by("-total")[:50]),
           "employees": list(live.order_by().values("employee__nik", "employee__name", "employee__department__name").annotate(n=Count("id"), total=Sum("total_amount")).order_by("-total")[:TOP_EMPLOYEES]),
           "partners": Partner.objects.order_by("name"), "departments": Department.objects.order_by("name"), "statuses": PartnerBill.STATUSES, "payers": PartnerBill.PAYERS, "services": PartnerBill.SERVICES,
           "qs": urlencode({k: v for k, v in f.items() if v or k == "period"})}
    return render(request, "poli/bills.html", ctx)


@poli_only
def bill_new(request):
    form = PartnerBillForm(request.POST or None, user=request.user)
    fs = BillLineFormSet(request.POST or None, prefix="ln")
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        try:
            with transaction.atomic():
                b = form.save(fs.lines())
                log(request, "poli", "bill_create", b, None, {"partner": b.partner.name, "number": b.bill_number, "employee": b.employee.nik, "total": str(b.total_amount), "lines": b.lines.count()})
            messages.success(request, "Tagihan dicatat." + (" Perhatian: jumlah rincian berbeda dari total tagihan." if b.mismatch else ""))
            return redirect("poli_bill_detail", pk=b.pk)
        except ValueError as e: messages.error(request, str(e))
    return render(request, "poli/bill_form.html", {"form": form, "fs": fs})


@poli_only
def bill_detail(request, pk):
    b = get_object_or_404(PartnerBill.objects.select_related("partner", "employee", "employee__department", "diagnosis", "created_by"), pk=pk)
    log(request, "poli", "bill_view", b)                 # membuka tagihan (memuat keluhan/diagnosa) tercatat; isinya tidak
    return render(request, "poli/bill_detail.html", {"b": b, "lines": b.lines.all(), "events": b.events.select_related("user"), "next": sorted(PartnerBill.FLOW.get(b.status, ())), "today": date.today()})


@poli_only
@require_POST
def bill_action(request, pk, action):
    to = {"verify": "diverifikasi", "approve": "disetujui", "reject": "ditolak", "pay": "dibayar"}.get(action)
    get_object_or_404(PartnerBill, pk=pk)
    if not to:
        messages.error(request, "Aksi tidak dikenal."); return redirect("poli_bill_detail", pk=pk)
    try:
        paid = None
        if action == "pay":
            try: paid = date.fromisoformat(request.POST.get("paid_date", ""))
            except ValueError: raise ValueError("Tanggal bayar tidak valid.")
        with transaction.atomic():
            b, before = services.bill_transition(pk, to, request.user, request.POST.get("note", ""), paid, request.POST.get("payment_ref", ""))
            log(request, "poli", f"bill_{action}", b, {"status": before}, {"status": b.status, "number": b.bill_number, "total": str(b.total_amount)})
        messages.success(request, f"Status tagihan: {b.get_status_display()}.")
    except ValueError as e: messages.error(request, str(e))
    return redirect("poli_bill_detail", pk=pk)


@poli_only
def partners(request):
    form = PartnerForm(request.POST or None) if request.POST.get("action") == "add" else PartnerForm()
    if request.method == "POST":
        if request.POST.get("action") == "add" and form.is_valid():
            with transaction.atomic():
                p = Partner.objects.create(name=form.cleaned_data["name"], kind=form.cleaned_data["kind"], contact=form.cleaned_data["contact"].strip())
                log(request, "poli", "partner_add", p, None, {"name": p.name})
            messages.success(request, "Mitra ditambahkan."); return redirect("poli_partners")
        if request.POST.get("action") == "toggle":
            with transaction.atomic():
                p = Partner.objects.select_for_update().filter(pk=_int(request.POST.get("id"))).first()
                if p:
                    p.is_active = not p.is_active; p.save(update_fields=["is_active"]); log(request, "poli", "partner_toggle", p, None, {"active": p.is_active})
                    messages.success(request, "Status mitra diubah."); return redirect("poli_partners")
            messages.error(request, "Mitra tidak ditemukan.")
    return render(request, "poli/partners.html", {"partners": Partner.objects.annotate(n=Count("bills")).order_by("name"), "form": form})
