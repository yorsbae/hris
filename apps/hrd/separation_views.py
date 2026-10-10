"""Karyawan Keluar (putaran 38): rekap mengundurkan diri / habis kontrak / PHK / pensiun, dengan identitas karyawan, tanggal masuk & keluar, masa kerja,
tali asih, dan paklaring (surat keterangan kerja, PDF). Hanya HRD/Superadmin. Catatan tidak diubah/dihapus: salah catat = dibatalkan dengan alasan;
nominal tali asih diubah lewat aksi khusus (tercatat di audit) dan terkunci setelah dibayar."""
from datetime import date
from urllib.parse import urlencode
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.hr.models import Department
from . import services
from .forms import SeparationForm, SeparationTaliForm
from .models import Separation
from .pdf import paklaring_pdf
from .views import _int, hrd_only, paginate

EXPORT_MAX = 20000
KIND_LABEL = dict(Separation.Kind.choices)
MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def _filters(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "year", "month", "kind", "department", "paklaring", "tali", "batal")}
    if "year" not in request.GET: f["year"] = str(date.today().year)       # bawaan tahun ini; "year=" (kosong) = semua tahun
    return f


def _base(f):
    qs = Separation.objects.select_related("employee")
    y, m = _int(f["year"]), _int(f["month"])
    if y and 2000 <= y <= 2100: qs = qs.filter(last_date__year=y)
    if m and 1 <= m <= 12: qs = qs.filter(last_date__month=m)
    if f["kind"] in KIND_LABEL: qs = qs.filter(kind=f["kind"])
    if f["department"]: qs = qs.filter(department_name=f["department"])
    if f["paklaring"] == "belum": qs = qs.filter(paklaring_number="")
    elif f["paklaring"] == "sudah": qs = qs.exclude(paklaring_number="")
    if f["tali"] == "belum": qs = qs.filter(tali_asih__gt=0, tali_asih_paid_on__isnull=True)
    elif f["tali"] == "sudah": qs = qs.filter(tali_asih_paid_on__isnull=False)
    elif f["tali"] == "ada": qs = qs.filter(tali_asih__gt=0)
    if f["q"]: qs = qs.filter(Q(emp_name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    return qs


HEADER = ["nik", "nama", "departemen", "jabatan", "tanggal_masuk", "tanggal_mengajukan", "tanggal_keluar", "masa_kerja", "jenis", "alasan", "tali_asih", "dasar_tali_asih", "tali_asih_dibayar",
          "no_paklaring", "tanggal_paklaring", "status"]


def _row(s):
    return (s.employee.nik, s.emp_name, s.department_name, s.position_name, s.join_date, s.request_date or "", s.last_date, services.service_period(s.join_date, s.last_date), KIND_LABEL[s.kind],
            s.reason, s.tali_asih, s.tali_asih_note, s.tali_asih_paid_on or "", s.paklaring_number, s.paklaring_date or "", "batal" if s.is_void else ("keluar" if s.applied_at else "terjadwal"))


@hrd_only
def separations(request):
    f = _filters(request); base = _base(f); live = base.filter(voided_at__isnull=True); shown = base if f["batal"] else live
    if request.GET.get("export"):
        log(request, "hrd", "separation_export", None, None, {"year": f["year"], "month": f["month"], "rows": shown.count()})
        rows = [_row(s) for s in shown.order_by("last_date", "emp_name", "id")[:EXPORT_MAX]]
        return tabular.export_response(f"karyawan-keluar-{f['year'] or 'semua'}{'-' + f['month'].zfill(2) if f['month'] else ''}", HEADER, rows, request, sheet="Karyawan Keluar", money_cols=("tali_asih",))
    totals = live.aggregate(n=Count("id"), tali=Sum("tali_asih"), tali_belum=Sum("tali_asih", filter=Q(tali_asih__gt=0, tali_asih_paid_on__isnull=True)))
    tali_belum_n = live.filter(tali_asih__gt=0, tali_asih_paid_on__isnull=True).count()
    pak_belum = live.filter(paklaring_number="", applied_at__isnull=False).count()
    terjadwal = live.filter(applied_at__isnull=True).count()
    by_kind = [(KIND_LABEL[r["kind"]], r["n"], r["tali"] or 0) for r in live.order_by().values("kind").annotate(n=Count("id"), tali=Sum("tali_asih")).order_by("-n")]
    by_dept = list(live.order_by().values("department_name").annotate(n=Count("id"), tali=Sum("tali_asih")).order_by("-n", "department_name"))
    depts = list(Separation.objects.order_by().values_list("department_name", flat=True).distinct().order_by("department_name"))
    ctx = {"f": f, "page": paginate(request, shown.order_by("-last_date", "-id")), "totals": totals, "tali_belum_n": tali_belum_n, "pak_belum": pak_belum, "terjadwal": terjadwal,
           "by_kind": by_kind, "by_dept": by_dept, "kinds": Separation.Kind.choices, "depts": depts, "months": list(enumerate(MONTHS, 1)), "year_now": date.today().year,
           "qs": urlencode({k: v for k, v in f.items() if v or k == "year"}), "today": timezone.localdate(),
           "flow_now": 1 if not Separation.objects.filter(voided_at__isnull=True).exists() else (3 if pak_belum else (2 if tali_belum_n else 3))}
    return render(request, "hrd/separations.html", ctx)


@hrd_only
def separation_new(request):
    initial = {"kind": request.GET.get("kind") or Separation.Kind.RESIGN, "nik": request.GET.get("nik", "")}
    form = SeparationForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            s = form.save()
            log(request, "hrd", "separation_create", s, None, {"employee": form.employee.nik, "kind": s.kind, "last_date": str(s.last_date), "tali_asih": s.tali_asih, "applied": bool(s.applied_at)})
        messages.success(request, "Karyawan keluar dicatat" + (" dan statusnya dinonaktifkan." if s.status_changed else ("." if s.applied_at else f". Status karyawan dinonaktifkan setelah {s.last_date:%d-%m-%Y} (otomatis, atau klik Nonaktifkan sekarang di halaman ini).")))
        return redirect("hrd_separation_detail", pk=s.pk)
    return render(request, "hrd/separation_form.html", {"form": form})


@hrd_only
def separation_detail(request, pk):
    s = get_object_or_404(Separation.objects.select_related("employee", "created_by", "voided_by", "paklaring_by"), pk=pk)
    tali = SeparationTaliForm(initial={"tali_asih": s.tali_asih or None, "tali_asih_note": s.tali_asih_note, "paid_on": date.today() if s.tali_asih else None})
    today = timezone.localdate()
    step_now = 2 if not s.applied_at else (3 if s.tali_asih and not s.tali_asih_paid_on else (4 if not s.paklaring_number else 5))
    return render(request, "hrd/separation_detail.html", {"s": s, "tali": tali, "step_now": step_now, "service": services.service_period(s.join_date, s.last_date), "today": today,
                                                          "can_apply_now": not s.applied_at and not s.is_void and s.last_date <= today,
                                                          "kind_label": KIND_LABEL[s.kind]})


@hrd_only
@require_POST
def separation_action(request, pk, action):
    s = get_object_or_404(Separation, pk=pk)
    try:
        with transaction.atomic():
            if action == "apply":
                s = services.apply_separation(pk, request.user)
                log(request, "hrd", "separation_apply", s, None, {"employee": s.employee.nik, "status_changed": s.status_changed})
                messages.success(request, "Karyawan dinonaktifkan; riwayat status tercatat.")
            elif action == "tali":
                form = SeparationTaliForm(request.POST)
                if not form.is_valid(): raise ValueError("; ".join(m for ms in form.errors.values() for m in ms))
                d = form.cleaned_data
                s, old = services.set_tali_asih(pk, d["tali_asih"], d["tali_asih_note"], d["paid_on"], request.user)
                log(request, "hrd", "separation_tali_asih", s, old, {"tali_asih": s.tali_asih, "paid_on": str(s.tali_asih_paid_on or "")})
                messages.success(request, "Tali asih disimpan" + (" dan ditandai dibayar." if s.tali_asih_paid_on else "."))
            elif action == "paklaring":
                try: issue = date.fromisoformat(request.POST.get("issue_date", "")) if request.POST.get("issue_date") else timezone.localdate()
                except ValueError: raise ValueError("Tanggal terbit tidak valid.")
                s = services.issue_paklaring(pk, issue, request.user)
                log(request, "hrd", "separation_paklaring", s, None, {"employee": s.employee.nik, "number": s.paklaring_number})
                messages.success(request, f"Paklaring {s.paklaring_number} diterbitkan. Klik Cetak paklaring untuk PDF.")
            elif action == "void":
                s = services.void_separation(pk, request.POST.get("reason", ""), request.user)
                log(request, "hrd", "separation_void", s, None, {"reason": s.void_reason, "employee": s.employee.nik})
                messages.success(request, "Catatan dibatalkan" + ("; status karyawan dipulihkan." if s.status_changed else "."))
            else: raise Http404
    except ValueError as e: messages.error(request, str(e))
    return redirect("hrd_separation_detail", pk=pk)


@hrd_only
def separation_pdf(request, pk):
    s = get_object_or_404(Separation.objects.select_related("employee"), pk=pk)
    if s.is_void or not s.paklaring_number: raise Http404
    log(request, "hrd", "print_paklaring", s, None, {"number": s.paklaring_number})
    resp = HttpResponse(paklaring_pdf(s), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{s.paklaring_number.replace("/", "-")}.pdf"'
    return resp
