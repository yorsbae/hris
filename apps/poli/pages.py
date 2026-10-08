"""Halaman Poliklinik (/poli/…). SEMUA view: login + role Poli (Superadmin lolos). HRD & Admin Departemen → 403 (data medis tertutup).
Rekam medis tidak diubah/dihapus: koreksi lewat catatan tambahan (append-only). Akses baca data medis dicatat di audit."""
from datetime import date, datetime, time, timedelta
from functools import wraps
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
import csv
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from apps.hr.models import Employee
from . import services
from . import reports
from .forms import (AddendumForm, AddPrescriptionForm, DiagnosisForm, DiagnosisMedicineForm, MedicineForm, PrescriptionFormSet, RecordForm, ReferralForm,
                    ReturnPrescriptionForm, StockAdjustForm, StockInForm)
from .models import Diagnosis, DiagnosisMedicine, MedicalRecord, Medicine, Prescription, Referral, SickLeaveLetter, StockMovement
from .pdf import referral_pdf

PER_PAGE = 50


def poli_only(fn):
    @wraps(fn)
    @login_required
    @require_roles(Role.POLI)
    def wrap(*a, **k): return fn(*a, **k)
    return wrap


def paginate(request, qs): return Paginator(qs, PER_PAGE).get_page(request.GET.get("page", 1))


def _date(v):
    try: return date.fromisoformat(v)
    except (TypeError, ValueError): return None


def _day_start(d): return timezone.make_aware(datetime.combine(d, time.min))


def _day_range(a=None, b=None):
    """Filter hari sebagai RENTANG waktu (visit_at >= awal hari, < awal hari berikutnya) agar indeks visit_at terpakai (visit_at__date = cast per baris)."""
    out = {}
    if a: out["visit_at__gte"] = _day_start(a)
    if b: out["visit_at__lt"] = _day_start(b + timedelta(days=1))
    return out


def _qs(request, *keys): return urlencode({k: request.GET.get(k, "") for k in keys if request.GET.get(k, "")})


def _errors(form): return "; ".join(f"{e}" for errs in form.errors.values() for e in errs)


def identity_rows(e):
    """Identitas MINIMUM (sama dengan yang dilihat Poli di modul karyawan). Tidak ada data sensitif."""
    return [("NIK", e.nik), ("Nama", e.name), ("Jenis kelamin", e.get_gender_display()), ("Departemen", e.department.name),
            ("Jabatan", e.position.name if e.position else "-")]


EXAM_LABELS = {"tensi": "Tensi", "suhu": "Suhu (°C)", "nadi": "Nadi (x/menit)", "bb": "Berat badan (kg)", "tb": "Tinggi badan (cm)"}
INCIDENT_LABELS = {"lokasi": "Lokasi kejadian", "kronologi": "Kronologi"}
PREG_LABELS = {"usia_minggu": "Usia kehamilan (minggu)", "hpl": "HPL", "tfu": "TFU (cm)", "djj": "DJJ (x/menit)"}


def exam_rows(exam):
    """Daftar (label, nilai) dari JSON pemeriksaan; kunci tak dikenal diabaikan (template tidak pernah mencetak JSON mentah)."""
    if not isinstance(exam, dict): return []
    rows = [(EXAM_LABELS[k], exam[k]) for k in EXAM_LABELS if k in exam]
    for key, labels in (("kecelakaan", INCIDENT_LABELS), ("kehamilan", PREG_LABELS)):
        sub = exam.get(key)
        if isinstance(sub, dict): rows += [(labels[k], sub[k]) for k in labels if k in sub]
    return rows


# ================================================================ Hub
@poli_only
def hub(request):
    today = date.today()
    cards = [
        ("Rekam medis", "/poli/records/", MedicalRecord.objects.filter(**_day_range(today, today)).count(), "kunjungan hari ini"),
        ("Kecelakaan kerja", "/poli/records/?kind=kecelakaan_kerja", MedicalRecord.objects.filter(kind="kecelakaan_kerja", **_day_range(today.replace(day=1), today)).count(), "bulan ini"),
        ("Rujukan", "/poli/referrals/", Referral.objects.filter(status__in=("diajukan", "dirujuk")).count(), "rujukan berjalan"),
        ("Obat & kartu stok", "/poli/medicines/", Medicine.objects.filter(stock__lte=F("min_stock")).count(), "obat stok minimum"),
        ("Rekap stok obat", "/poli/reports/stock/", Medicine.objects.count(), "obat — rekap harian & bulanan"),
        ("Master diagnosa", "/poli/diagnoses/", Diagnosis.objects.count(), "diagnosa terdaftar"),
    ]
    return render(request, "poli/hub.html", {"cards": cards})


# ================================================================ Rekam medis
@poli_only
def record_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "kind", "date_from", "date_to")}
    qs = MedicalRecord.objects.select_related("employee", "employee__department", "diagnosis", "created_by")
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if f["kind"] in dict(MedicalRecord.KINDS): qs = qs.filter(kind=f["kind"])
    qs = qs.filter(**_day_range(_date(f["date_from"]), _date(f["date_to"])))
    page = paginate(request, qs.order_by("-visit_at", "-id"))
    return render(request, "poli/record_list.html", {"page": page, "f": f, "kinds": MedicalRecord.KINDS, "qs": _qs(request, "q", "kind", "date_from", "date_to")})


@poli_only
def record_new(request):
    form = RecordForm(request.POST or None, initial={"kind": request.GET.get("kind", "berobat"), "nik": request.GET.get("nik", "")})
    fs = PrescriptionFormSet(request.POST or None, prefix="rx")
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        try:
            r, low = services.create_record(request.user, form.employee, form.cleaned_data["kind"], form.cleaned_data["complaint"].strip(), form.exam(),
                                            form.diagnosis, form.cleaned_data["treatment"].strip(), fs.lines())
        except ValueError as ex: form.add_error(None, str(ex))
        else:
            # audit TANPA isi medis (keluhan/tindakan/tanda vital): hanya penanda
            log(request, "poli", "create_record", r, None, {"employee": form.employee.nik, "kind": r.kind, "diagnosis": form.diagnosis.code if form.diagnosis else None,
                                                             "prescriptions": [(p.medicine.code, p.qty) for p in r.prescriptions.select_related("medicine")]})
            messages.success(request, "Rekam medis disimpan." + (f" Stok minimum: {', '.join(m.name for m in low)}." if low else ""))
            return redirect("poli_record_detail", pk=r.pk)
    return render(request, "poli/record_form.html", {"form": form, "fs": fs})


@poli_only
def record_detail(request, pk):
    r = get_object_or_404(MedicalRecord.objects.select_related("employee", "employee__department", "employee__position", "diagnosis", "created_by"), pk=pk)
    log(request, "poli", "view_record", r)  # akses data medis tercatat
    letter = SickLeaveLetter.objects.filter(record=r).first()
    rx = list(r.prescriptions.select_related("medicine", "added_by").prefetch_related("returns").order_by("id"))
    for p in rx: p.returned = sum(x.qty for x in p.returns.all()); p.net = p.qty - p.returned  # jumlah bersih = diberikan − retur
    return render(request, "poli/record_detail.html", {
        "r": r, "idn": identity_rows(r.employee), "rx": rx, "add_form": AddPrescriptionForm(), "ret_form": ReturnPrescriptionForm(), "addenda": r.addenda.select_related("created_by"),
        "referrals": r.referral_set.order_by("-id"), "letter": letter, "addendum_form": AddendumForm(),
        "exam_rows": exam_rows(r.exam)})


@poli_only
@require_POST
def record_addendum(request, pk):
    r = get_object_or_404(MedicalRecord, pk=pk)
    form = AddendumForm(request.POST)
    if form.is_valid():
        a = services.add_addendum(request.user, r, form.cleaned_data["note"])
        log(request, "poli", "add_addendum", a)  # isi catatan tidak masuk audit
        messages.success(request, "Catatan tambahan disimpan.")
    else: messages.error(request, _errors(form))
    return redirect("poli_record_detail", pk=pk)


@poli_only
def employee_history(request, pk):
    e = get_object_or_404(Employee.objects.select_related("department", "position"), pk=pk)
    log(request, "poli", "view_history", e)
    page = paginate(request, e.medical_records.select_related("diagnosis").order_by("-visit_at", "-id"))
    return render(request, "poli/employee_history.html", {"e": e, "idn": identity_rows(e), "page": page, "qs": ""})


# ================================================================ Obat & kartu stok
@poli_only
def medicine_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "low")}
    qs = Medicine.objects.all()
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(code__istartswith=f["q"]))
    if f["low"]: qs = qs.filter(stock__lte=F("min_stock"))
    return render(request, "poli/medicine_list.html", {"page": paginate(request, qs.order_by("name")), "f": f, "qs": _qs(request, "q", "low")})


@poli_only
def medicine_form(request, pk=None):
    m = get_object_or_404(Medicine, pk=pk) if pk else None
    # snapshot SEBELUM form dibuat: ModelForm.is_valid() sudah mengubah instance (bug audit "before" berisi nilai baru bila diambil sesudahnya)
    before = {k: getattr(m, k) for k in ("code", "name", "unit", "min_stock")} if m else None
    form = MedicineForm(request.POST or None, instance=m)  # stok TIDAK ada di form: hanya berubah lewat kartu stok
    if request.method == "POST" and form.is_valid():
        m = form.save(commit=False); m.save()
        log(request, "poli", "medicine_update" if before else "medicine_create", m, before, {k: getattr(m, k) for k in ("code", "name", "unit", "min_stock")})
        messages.success(request, "Obat disimpan." + ("" if before else " Stok awal 0 — catat penerimaan lewat \"Stok masuk\"."))
        return redirect("poli_medicine_detail", pk=m.pk)
    return render(request, "poli/form.html", {"form": form, "title": f"Ubah obat {m.code}" if m else "Obat baru", "back": "/poli/medicines/"})


@poli_only
def medicine_detail(request, pk):
    m = get_object_or_404(Medicine, pk=pk)
    page = paginate(request, StockMovement.objects.filter(medicine=m).select_related("created_by"))
    return render(request, "poli/medicine_detail.html", {"m": m, "page": page, "qs": "", "in_form": StockInForm(), "adj_form": StockAdjustForm(), "low": m.stock <= m.min_stock})


@poli_only
@require_POST
def medicine_stock(request, pk, action):
    if action not in ("in", "adjust"): raise Http404
    get_object_or_404(Medicine, pk=pk)
    form = (StockInForm if action == "in" else StockAdjustForm)(request.POST)
    if not form.is_valid(): messages.error(request, _errors(form)); return redirect("poli_medicine_detail", pk=pk)
    d = form.cleaned_data
    try:
        if action == "in": m, mv = services.stock_in(pk, d["qty"], request.user, d["ref"], d["note"])
        else: m, mv = services.stock_adjust(pk, d["delta"], request.user, d["note"])
    except ValueError as ex: messages.error(request, str(ex)); return redirect("poli_medicine_detail", pk=pk)
    log(request, "poli", "stock_in" if action == "in" else "stock_adjust", m, {"stock": m.stock - mv.qty}, {"stock": m.stock, "note": mv.note, "ref": mv.ref})
    messages.success(request, f"Stok {m.name}: {m.stock} {m.unit}.")
    return redirect("poli_medicine_detail", pk=pk)


# ================================================================ Master diagnosa
@poli_only
def diagnosis_list(request):
    q = request.GET.get("q", "").strip()
    qs = Diagnosis.objects.annotate(n_meds=Count("medicine_links"))
    if q: qs = qs.filter(Q(code__istartswith=q) | Q(name__icontains=q))
    return render(request, "poli/diagnosis_list.html", {"page": paginate(request, qs.order_by("code")), "q": q, "qs": _qs(request, "q")})


@poli_only
def diagnosis_form(request, pk=None):
    d = get_object_or_404(Diagnosis, pk=pk) if pk else None
    before = {"code": d.code, "name": d.name, "category": d.category} if d else None  # sebelum form (lihat catatan di medicine_form)
    form = DiagnosisForm(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid():
        d = form.save()
        log(request, "poli", "diagnosis_update" if before else "diagnosis_create", d, before, {"code": d.code, "name": d.name, "category": d.category})
        messages.success(request, "Diagnosa disimpan."); return redirect("poli_diagnoses")
    return render(request, "poli/form.html", {"form": form, "title": f"Ubah diagnosa {d.code}" if d else "Diagnosa baru", "back": "/poli/diagnoses/"})


# ================================================================ Rujukan
@poli_only
def referral_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "status")}
    qs = Referral.objects.select_related("record", "record__employee", "record__employee__department")
    if f["status"] in dict(Referral.STATUSES): qs = qs.filter(status=f["status"])
    elif f["status"] == "berjalan": qs = qs.filter(status__in=("diajukan", "dirujuk"))
    if f["q"]: qs = qs.filter(Q(record__employee__name__icontains=f["q"]) | Q(record__employee__nik__startswith=f["q"]) | Q(number__icontains=f["q"]))
    return render(request, "poli/referral_list.html", {"page": paginate(request, qs.order_by("-id")), "f": f, "statuses": Referral.STATUSES, "qs": _qs(request, "q", "status")})


@poli_only
def referral_new(request, pk):
    r = get_object_or_404(MedicalRecord.objects.select_related("employee"), pk=pk)
    form = ReferralForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try: ref = services.create_referral(request.user, r, form.cleaned_data["facility"], form.cleaned_data["note"])
        except ValueError as ex: form.add_error(None, str(ex))
        else:
            log(request, "poli", "referral_create", ref, None, {"number": ref.number, "facility": ref.facility})
            messages.success(request, f"Rujukan {ref.number} dibuat."); return redirect("poli_referral_detail", pk=ref.pk)
    return render(request, "poli/form.html", {"form": form, "title": f"Rujukan baru — {r.employee.nik} {r.employee.name}", "back": f"/poli/records/{r.pk}/"})


@poli_only
def referral_detail(request, pk):
    ref = get_object_or_404(Referral.objects.select_related("record", "record__employee", "record__employee__department", "record__employee__position", "created_by"), pk=pk)
    log(request, "poli", "view_referral", ref)
    return render(request, "poli/referral_detail.html", {"ref": ref, "idn": identity_rows(ref.record.employee), "next": sorted(Referral.FLOW.get(ref.status, ()))})


@poli_only
@require_POST
def referral_action(request, pk, action):
    to = {"send": "dirujuk", "finish": "selesai", "cancel": "batal"}.get(action)
    if not to: raise Http404
    get_object_or_404(Referral, pk=pk)
    try:
        ref, before = services.referral_transition(pk, to, request.user, request.POST.get("outcome", ""))
        log(request, "poli", f"referral_{action}", ref, {"status": before}, {"status": ref.status})  # isi hasil tidak masuk audit
        messages.success(request, f"Status rujukan: {ref.get_status_display()}.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("poli_referral_detail", pk=pk)


@poli_only
def referral_letter(request, pk):
    ref = get_object_or_404(Referral.objects.select_related("record__employee__department", "record__created_by", "created_by"), pk=pk)
    if ref.status == "batal": raise Http404
    log(request, "poli", "print_referral", ref)
    resp = HttpResponse(referral_pdf(ref), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{ref.number.replace("/", "-")}.pdf"'
    return resp


# ================================================================ Tambah / kurangi obat pada rekam medis
@poli_only
@require_POST
def record_rx_add(request, pk):
    r = get_object_or_404(MedicalRecord, pk=pk)
    form = AddPrescriptionForm(request.POST)
    if not form.is_valid(): messages.error(request, _errors(form)); return redirect("poli_record_detail", pk=pk)
    d = form.cleaned_data
    try: p, m = services.add_prescription(request.user, r, d["medicine"].pk, d["qty"], d["dosage"])
    except ValueError as ex: messages.error(request, str(ex)); return redirect("poli_record_detail", pk=pk)
    log(request, "poli", "rx_add", r, None, {"medicine": m.code, "qty": p.qty, "stock_after": m.stock})
    messages.success(request, f"Obat ditambahkan: {m.name} × {p.qty}. Stok sekarang {m.stock} {m.unit}." + (" Stok minimum!" if m.stock <= m.min_stock else ""))
    return redirect("poli_record_detail", pk=pk)


@poli_only
@require_POST
def record_rx_return(request, pk):
    r = get_object_or_404(MedicalRecord, pk=pk)
    form = ReturnPrescriptionForm(request.POST)
    if not form.is_valid(): messages.error(request, _errors(form)); return redirect("poli_record_detail", pk=pk)
    d = form.cleaned_data
    if not Prescription.objects.filter(pk=d["prescription"], record=r).exists():  # baris resep harus milik rekam medis ini (ID di formulir bisa dipalsukan)
        raise Http404
    try: ret, m = services.return_prescription(request.user, d["prescription"], d["qty"], d["reason"])
    except ValueError as ex: messages.error(request, str(ex)); return redirect("poli_record_detail", pk=pk)
    log(request, "poli", "rx_return", r, None, {"medicine": m.code, "qty": ret.qty, "stock_after": m.stock})  # alasan tidak masuk audit (dapat memuat isi medis)
    messages.success(request, f"Obat dikurangi: {m.name} × {ret.qty}. Stok kembali menjadi {m.stock} {m.unit}.")
    return redirect("poli_record_detail", pk=pk)


# ================================================================ Ringkasan pasien (setelah NIK dipilih di form kunjungan)
@poli_only
def patient_summary(request):
    """JSON: identitas minimum + 8 kunjungan terakhir. Hanya karyawan aktif. Pembukaan riwayat tercatat di audit (sama seperti halaman riwayat)."""
    e = services.active_employee(nik=request.GET.get("nik", "").strip()[:20])
    if not e: return JsonResponse({"found": False}, status=404)
    log(request, "poli", "view_history", e)
    qs = e.medical_records.select_related("diagnosis").order_by("-visit_at", "-id")
    last = [{"id": r.pk, "date": timezone.localtime(r.visit_at).strftime("%d-%m-%Y"), "kind": r.get_kind_display(), "url": f"/poli/records/{r.pk}/",
             "diagnosis": f"{r.diagnosis.code} {r.diagnosis.name}" if r.diagnosis_id else "", "complaint": (r.complaint or "")[:90]} for r in qs[:8]]
    return JsonResponse({"found": True, "nik": e.nik, "name": e.name, "gender": e.get_gender_display(), "department": e.department.name,
                         "total": qs.count(), "history_url": f"/poli/employees/{e.pk}/", "visits": last})


# ================================================================ Master diagnosa ↔ obat
@poli_only
def diagnosis_medicines(request, pk):
    d = get_object_or_404(Diagnosis, pk=pk)
    form = DiagnosisMedicineForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        try: l = services.link_medicine(d, c["medicine"].pk, c["qty"], c["dosage"])
        except ValueError as ex: form.add_error(None, str(ex))
        else:
            log(request, "poli", "diagnosis_link_medicine", d, None, {"diagnosis": d.code, "medicine": l.medicine.code, "qty": l.qty})
            messages.success(request, f"{l.medicine.name} ditautkan ke diagnosa {d.code}."); return redirect("poli_diagnosis_medicines", pk=pk)
    return render(request, "poli/diagnosis_medicines.html", {"d": d, "links": d.medicine_links.select_related("medicine"), "form": form})


@poli_only
@require_POST
def diagnosis_medicine_unlink(request, pk, link_id):
    d = get_object_or_404(Diagnosis, pk=pk)
    l = DiagnosisMedicine.objects.filter(diagnosis=d, pk=link_id).select_related("medicine").first()
    if not l: raise Http404
    code = l.medicine.code
    services.unlink_medicine(d, link_id)
    log(request, "poli", "diagnosis_unlink_medicine", d, {"diagnosis": d.code, "medicine": code}, None)
    messages.success(request, "Tautan obat dihapus."); return redirect("poli_diagnosis_medicines", pk=pk)


# ================================================================ Rekap stok obat
def _csv_safe(v):
    """Sel berawalan = + - @ diberi apostrof agar tidak dibaca sebagai rumus oleh spreadsheet."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@poli_only
def stock_report(request):
    f = {k: request.GET.get(k, "").strip() for k in ("period", "date", "month", "q")}
    period = "month" if f["period"] == "month" else "day"
    today = timezone.localdate()
    if period == "month":
        try: y, m = f["month"].split("-"); d = date(int(y), int(m), 1)
        except (ValueError, AttributeError): d = today.replace(day=1)
    else: d = _date(f["date"]) or today
    if not (2000 <= d.year <= 2100): d = today  # tanggal ekstrem → bawaan (hindari OverflowError)
    data = reports.stock_report(period, d, q=f["q"], show_all=request.GET.get("all") == "1")
    if request.GET.get("format") == "csv":
        log(request, "poli", "stock_report_export", None, None, {"period": period, "from": data["start"].isoformat(), "to": data["end"].isoformat()})
        resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        resp["Content-Disposition"] = f'attachment; filename="rekap-stok-{period}-{data["start"]:%Y%m%d}.csv"'
        w = csv.writer(resp); w.writerow(["Kode", "Obat", "Satuan", "Stok awal", "Masuk", "Retur resep", "Keluar", "Penyesuaian", "Stok akhir"])
        for r in data["rows"]: w.writerow([_csv_safe(r["code"]), _csv_safe(r["name"]), _csv_safe(r["unit"]), r["opening"], r["masuk"], r["retur"], r["keluar"], r["adj"], r["closing"]])
        t = data["total"]; w.writerow(["", "TOTAL", "", t["opening"], t["masuk"], t["retur"], t["keluar"], t["adj"], t["closing"]])
        return resp
    prev, nxt = ((d - timedelta(days=1), d + timedelta(days=1)) if period == "day"
                 else ((d - timedelta(days=1)).replace(day=1), (d + timedelta(days=32)).replace(day=1)))
    key = lambda x: x.strftime("%Y-%m") if period == "month" else x.isoformat()
    paramname = "month" if period == "month" else "date"
    base = urlencode({"period": period, "q": f["q"], **({"all": "1"} if request.GET.get("all") == "1" else {})})
    return render(request, "poli/stock_report.html", {
        "data": data, "period": period, "f": f, "cur": key(d), "prev_url": f"?{base}&{paramname}={key(prev)}", "next_url": f"?{base}&{paramname}={key(nxt)}",
        "csv_url": f"?{base}&{paramname}={key(d)}&format=csv", "show_all": request.GET.get("all") == "1"})
