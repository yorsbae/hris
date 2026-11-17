"""Halaman Kontrak: pilih kontrak → susun cetakan (urutan + gelombang) → unduh berkas Depan / Belakang. Pengaturan: template, UMK, penandatangan.
Hanya HRD & Superadmin (data identitas: KTP, alamat)."""
from datetime import date, timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST
from apps.core.audit import log
from apps.core.scope import require_roles
from . import contract_print as cp, docx_html
from .models import Contract, ContractConfig, Department, Employee, MinimumWage
from apps.core.models import Role

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILTERS = [("belum", "Belum dicetak"), ("akan", "Akan berakhir"), ("aktif", "Aktif"), ("semua", "Semua")]
SOON_DAYS = 60
HRD_ROLES = (Role.HRD,)   # Superadmin lolos otomatis di require_roles
MAX_PRINT = 600   # batas wajar satu kali susun (query string / memori)


def _qs():
    return (Contract.objects.filter(employee__deleted_at__isnull=True)
            
            .select_related("employee", "employee__department", "employee__position", "employee__shift", "employee__shift_group", "previous"))


def _apply(qs, f, today):
    if f == "akan": return qs.filter(status="aktif", end__isnull=False, end__lte=today + timedelta(days=SOON_DAYS))
    if f == "belum": return qs.filter(status="aktif", printed_at__isnull=True)
    if f == "aktif": return qs.filter(status="aktif")
    return qs


@login_required
@require_roles(*HRD_ROLES)
def contracts_page(request):
    today = timezone.localdate(); f = request.GET.get("f") if request.GET.get("f") in dict(FILTERS) else "belum"
    q, dept = request.GET.get("q", "").strip(), request.GET.get("department", "")
    base = _qs()
    if q: base = base.filter(Q(employee__name__icontains=q) | Q(employee__nik__icontains=q) | Q(number__icontains=q))
    if dept.isdigit(): base = base.filter(employee__department_id=int(dept))
    counts = {k: _apply(base, k, today).count() for k, _ in FILTERS}
    rows = list(_apply(base, f, today).order_by("end", "employee__name")[:MAX_PRINT + 1])
    over = len(rows) > MAX_PRINT; rows = rows[:MAX_PRINT]
    for c in rows:
        c.left = (c.end - today).days if c.end else None
        c.late = c.left is not None and c.left < 0
        c.soon = c.left is not None and 0 <= c.left <= SOON_DAYS
    cfg = ContractConfig.get()
    setup = []
    if not cfg.signer_name: setup.append("nama penandatangan")
    if not MinimumWage.objects.exists(): setup.append("UMK")
    return render(request, "contracts.html", {"rows": rows, "f": f, "filters": [(k, l, counts[k]) for k, l in FILTERS], "q": q, "dept": dept, "over": over, "max": MAX_PRINT,
                                              "departments": Department.objects.order_by("name"), "setup": setup, "sorts": cp.SORTS})


def _chosen(request):
    ids = [int(x) for x in request.POST.getlist("ids") if x.isdigit()]
    return cp.order(list(_qs().filter(pk__in=ids)), request.POST.get("sort") or "dept")


def _plan_ctx(request, cs):
    cfg = ContractConfig.get(); sort = request.POST.get("sort") or "dept"
    raw = request.POST.get("size", ""); size = int(raw) if raw.isdigit() and 1 <= int(raw) <= 500 else cfg.batch_size
    back = request.POST.get("back") if request.POST.get("back") in dict(ContractConfig.BACK_ORDER) else cfg.back_order
    try: rapat = date.fromisoformat(request.POST.get("rapat", "")) if request.POST.get("rapat") else cfg.decision_date
    except ValueError: rapat = cfg.decision_date
    blocked, no = [], 0
    for c in cs:
        b, w = cp.problems(c, cfg); c.block, c.warn = b, w
        if b: blocked.append(c)
    groups = []
    for gi, part in enumerate(cp.batches(cs, size), 1):
        for c in part: no += 1; c.no = no
        groups.append({"n": gi, "rows": part, "first": part[0].no, "last": part[-1].no})
    return {"cs": cs, "groups": groups, "blocked": blocked, "sort": sort, "sorts": cp.SORTS, "size": size, "back": back, "back_orders": ContractConfig.BACK_ORDER,
            "rapat": rapat, "rapat_iso": rapat.isoformat() if rapat else "", "ids": [c.pk for c in cs], "signer_missing": not cfg.signer_name, "has_sig": bool(cfg.signature_png)}


@login_required
@require_roles(*HRD_ROLES)
@require_POST
def contracts_plan(request):
    cs = _chosen(request)
    if not cs: messages.error(request, "Belum ada kontrak yang dicentang."); return redirect("contracts_page")
    if len(cs) > MAX_PRINT: messages.error(request, f"Maksimal {MAX_PRINT} kontrak sekali susun."); return redirect("contracts_page")
    return render(request, "contract_plan.html", _plan_ctx(request, cs))


def _part(request):
    """Parameter tombol gelombang (dl="<gelombang>:<sisi>") → (ctx, n, side, part) atau HttpResponseBadRequest. Dipakai unduh & cetak."""
    cs = _chosen(request); batch, _, side = (request.POST.get("dl") or "").partition(":")
    if not cs or side not in ("depan", "belakang", "daftar") or not batch.isdigit(): return HttpResponseBadRequest("Parameter tidak valid.")
    ctx = _plan_ctx(request, cs)
    if ctx["blocked"]: return HttpResponseBadRequest("Ada kontrak yang belum lengkap: " + "; ".join(f"{c.employee.name} ({', '.join(c.block)})" for c in ctx["blocked"][:5]))
    n = int(batch)
    if not 1 <= n <= len(ctx["groups"]): return HttpResponseBadRequest("Gelombang tidak ada.")
    return ctx, n, side, ctx["groups"][n - 1]["rows"]


def _sheet_docx(ctx, side, part):
    cfg = ContractConfig.get(); seq = part if side == "depan" or ctx["back"] == "same" else part[::-1]
    return cp.build(cp.template_bytes(cfg), [cp.values_for(c, cfg, ctx["rapat"]) for c in seq], side, bytes(cfg.signature_png) if cfg.signature_png else None)


def _mark_printed(request, ctx, n, side, part, how):
    Contract.objects.filter(pk__in=[c.pk for c in part]).update(printed_at=timezone.now())
    if ctx.get("rapat"): ContractConfig.objects.filter(pk=ContractConfig.get().pk).update(decision_date=ctx["rapat"])   # diingat sebagai bawaan cetakan berikutnya
    log(request, "hr", "contract_print", part[0], None, {"gelombang": n, "sisi": side, "jumlah": len(part), "urut_belakang": ctx["back"], "cara": how})


@login_required
@require_roles(*HRD_ROLES)
@require_POST
def contracts_download(request):
    """Satu berkas: side = depan | belakang | daftar, untuk gelombang ke-`dl`. Urutan & pembagian dihitung ulang dari parameter yang sama dengan halaman susun."""
    got = _part(request)
    if isinstance(got, HttpResponseBadRequest): return got
    ctx, n, side, part = got
    if side == "daftar":
        data = cp.order_list_doc([(c.no, c.employee.name, c.employee.nik, c.employee.department.name, c.number) for c in part], f"Daftar urutan cetak — gelombang {n}")
        name = f"Kontrak-Gelombang{n}-Daftar-urutan.docx"
    else:
        data = _sheet_docx(ctx, side, part); name = f"Kontrak-Gelombang{n}-{'Depan' if side == 'depan' else 'Belakang'}-{len(part)}lembar.docx"
        _mark_printed(request, ctx, n, side, part, "unduh")
    r = HttpResponse(data, content_type=DOCX); r["Content-Disposition"] = f'attachment; filename="{name}"'; r["X-Content-Type-Options"] = "nosniff"
    return r


def _print_page(request, title, hint, sheets, css, rows=None):
    return render(request, "contract_print.html", {"title": title, "hint": hint, "sheets": [mark_safe(x) for x in sheets], "css": mark_safe(css), "rows": rows})


@login_required
@require_roles(*HRD_ROLES)
@require_POST
def contracts_print(request):
    """Cetak langsung dari browser (tanpa unduh Word): halaman HTML berukuran kertas yang otomatis membuka dialog cetak."""
    got = _part(request)
    if isinstance(got, HttpResponseBadRequest): return got
    ctx, n, side, part = got
    if side == "daftar":
        return _print_page(request, f"Daftar urutan cetak — gelombang {n}", "", [], "", [(c.no, c.employee.name, c.employee.nik, c.employee.department.name, c.number) for c in part])
    css, sheets = docx_html.render(_sheet_docx(ctx, side, part)); _mark_printed(request, ctx, n, side, part, "cetak")
    hint = ("Cetak semua lembar DEPAN dulu. Setelah selesai, ambil tumpukan, balik, masukkan lagi ke tray, lalu cetak lembar BELAKANG." if side == "depan"
            else "Masukkan tumpukan yang sudah tercetak depan dengan sisi kosong menghadap printer sesuai jenis printer. Urutan lembar belakang mengikuti lembar depan.")
    return _print_page(request, f"Gelombang {n} — {'Depan' if side == 'depan' else 'Belakang'} ({len(part)} lembar)", hint, sheets, css)


@login_required
@require_roles(*HRD_ROLES)
@require_GET
def contract_print_one(request, pk):
    c = get_object_or_404(_qs(), pk=pk); cfg = ContractConfig.get(); b, _ = cp.problems(c, cfg)
    if b: messages.error(request, f"{c.employee.name}: " + ", ".join(b) + "."); return redirect("contracts_page")
    css, sheets = docx_html.render(cp.build(cp.template_bytes(cfg), [cp.values_for(c, cfg)], "penuh", bytes(cfg.signature_png) if cfg.signature_png else None))
    Contract.objects.filter(pk=c.pk).update(printed_at=timezone.now()); log(request, "hr", "contract_print", c, None, {"sisi": "penuh", "cara": "cetak"})
    return _print_page(request, f"Kontrak {c.employee.name}", "Kontrak 2 halaman: pilih cetak dua sisi (duplex) bila printer mendukung.", sheets, css)


@login_required
@require_roles(*HRD_ROLES)
@require_GET
def contract_sample_print(request):
    cfg = ContractConfig.get()
    css, sheets = docx_html.render(cp.build(cp.template_bytes(cfg), [cp.sample_values(cfg)], "penuh", bytes(cfg.signature_png) if cfg.signature_png else None))
    return _print_page(request, "Contoh kontrak (data contoh)", "Pratinjau template dengan data contoh. Cek tampilan sebelum mencetak kontrak sungguhan.", sheets, css)


@login_required
@require_roles(*HRD_ROLES)
@require_POST
def contracts_checklist(request):
    """Ceklis kehadiran (.xlsx) mengikuti urutan & gelombang cetak yang sama dengan halaman susun. dl = "<gelombang>" untuk satu gelombang, kosong = semua."""
    cs = _chosen(request)
    if not cs: return HttpResponseBadRequest("Belum ada kontrak yang dipilih.")
    ctx = _plan_ctx(request, cs); only = (request.POST.get("dl") or "").partition(":")[0]
    groups = ctx["groups"] if not only.isdigit() else [g for g in ctx["groups"] if g["n"] == int(only)]
    if not groups: return HttpResponseBadRequest("Gelombang tidak ada.")
    rows = [{"no": c.no, "gelombang": g["n"], "nama": c.employee.name, "nik": c.employee.nik, "dept": c.employee.department.name, "jabatan": c.employee.position.name if c.employee.position_id else "",
             "no_kontrak": c.number, "lama_berakhir": c.previous.end if c.previous_id else None, "mulai": c.start, "selesai": c.end,
             "dicetak": timezone.localtime(c.printed_at).date() if c.printed_at else None} for g in groups for c in g["rows"]]
    title = "Ceklis kehadiran tanda tangan kontrak" + (f" — gelombang {groups[0]['n']}" if only.isdigit() else "")
    data = cp.checklist_xlsx(rows, title, ctx["rapat"])
    log(request, "hr", "contract_checklist", None, None, {"jumlah": len(rows), "gelombang": only or "semua"})
    r = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = f'attachment; filename="Ceklis-kontrak{"-G" + only if only.isdigit() else ""}-{date.today():%Y%m%d}.xlsx"'; r["X-Content-Type-Options"] = "nosniff"
    return r


@login_required
@require_roles(*HRD_ROLES)
@require_GET
def contract_docx(request, pk):
    """Satu kontrak utuh (halaman 1 lalu 2) — untuk cetak satuan / cek."""
    c = get_object_or_404(_qs(), pk=pk); cfg = ContractConfig.get(); b, _ = cp.problems(c, cfg)
    if b: messages.error(request, f"{c.employee.name}: " + ", ".join(b) + "."); return redirect("contracts_page")
    data = cp.build(cp.template_bytes(cfg), [cp.values_for(c, cfg)], "penuh", bytes(cfg.signature_png) if cfg.signature_png else None)
    Contract.objects.filter(pk=c.pk).update(printed_at=timezone.now()); log(request, "hr", "contract_print", c, None, {"sisi": "penuh"})
    r = HttpResponse(data, content_type=DOCX); r["Content-Disposition"] = f'attachment; filename="Kontrak-{c.employee.nik}-{c.pk}.docx"'; r["X-Content-Type-Options"] = "nosniff"
    return r


# ---------- pengaturan ----------
class ConfigForm(forms.ModelForm):
    class Meta:
        model = ContractConfig
        fields = ["company_name", "company_address", "city", "signer_name", "signer_title", "pay_day", "number_format", "batch_size", "back_order"]
        widgets = {"company_address": forms.TextInput(attrs={"size": 60})}

    def clean_number_format(self):
        v = self.cleaned_data["number_format"]
        try: out = cp.format_number(v, 1, date(2026, 11, 1))
        except (KeyError, IndexError, ValueError): raise forms.ValidationError("Format tidak valid. Pakai {seq:03d}, {roman}, {month}, {year}.")
        if "{seq" not in v: raise forms.ValidationError("Format harus memuat {seq} (nomor urut), kalau tidak semua nomor kembar.")
        self.example = out; return v

    def clean_pay_day(self):
        v = self.cleaned_data["pay_day"]
        if not 1 <= v <= 31: raise forms.ValidationError("Tanggal 1–31.")
        return v

    def clean_batch_size(self):
        v = self.cleaned_data["batch_size"]
        if not 1 <= v <= 500: raise forms.ValidationError("1–500 lembar.")
        return v


class WageForm(forms.ModelForm):
    amount = forms.CharField(label="UMK per bulan (Rp)", widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off", "placeholder": "2.500.000", "data-rp": "1"}))

    class Meta: model = MinimumWage; fields = ["year", "amount"]

    def validate_unique(self): pass   # simpan ulang tahun yang sama = ubah angkanya (lihat view)

    def clean_year(self):
        v = self.cleaned_data["year"]
        if not 2000 <= v <= 2100: raise forms.ValidationError("Tahun tidak wajar.")
        return v

    def clean_amount(self):
        raw = self.cleaned_data["amount"].split(",")[0]; digits = "".join(ch for ch in raw if ch.isdigit())   # "Rp 2.500.000" / "2500000" / "2.500.000,00"
        if not digits: raise forms.ValidationError("Isi angka rupiah per bulan, mis. 2.500.000.")
        v = int(digits)
        if v < 100000: raise forms.ValidationError("Angka terlalu kecil; isi rupiah per bulan, mis. 2.500.000.")
        if v > 100000000: raise forms.ValidationError("Angka terlalu besar; isi per bulan, bukan per tahun.")
        return v


@login_required
@require_roles(*HRD_ROLES)
def contract_settings(request):
    cfg = ContractConfig.get(); form, wform = ConfigForm(instance=cfg), WageForm(initial={"year": date.today().year})
    if request.method == "POST":
        act = request.POST.get("action"); done = False
        if act == "save_config":
            form = ConfigForm(request.POST, instance=cfg)
            if form.is_valid(): form.save(); log(request, "hr", "contract_config", cfg, None, {k: str(v) for k, v in form.cleaned_data.items()}); messages.success(request, "Pengaturan disimpan."); done = True
        elif act == "upload_template":
            f = request.FILES.get("file"); data = f.read() if f else b""
            if not f or not f.name.lower().endswith(".docx") or f.size > 5 * 1024 * 1024: messages.error(request, "Pilih berkas .docx (maks 5 MB).")
            else:
                err, info = cp.validate_template(data)
                if err: messages.error(request, "Template ditolak: " + err)
                else:
                    cfg.template_docx, cfg.template_name = data, f.name[:150]; cfg.save(); log(request, "hr", "contract_template_upload", cfg, None, {"file": f.name, "penanda": len(info["placeholders"])})
                    messages.success(request, f"Template “{f.name}” dipakai ({len(info['placeholders'])} penanda dikenali)."); done = True
        elif act == "reset_template":
            cfg.template_docx, cfg.template_name = None, ""; cfg.save(); log(request, "hr", "contract_template_reset", cfg); messages.success(request, "Kembali ke template bawaan."); done = True
        elif act == "upload_sig":
            f = request.FILES.get("file"); data = f.read() if f else b""
            if not data or not cp.png_ok(data): messages.error(request, "Scan harus berkas PNG (maks 1 MB). Latar transparan paling bagus.")
            else: cfg.signature_png = data; cfg.save(); log(request, "hr", "contract_signature_upload", cfg); messages.success(request, "Scan tanda tangan disimpan."); done = True
        elif act == "delete_sig":
            cfg.signature_png = None; cfg.save(); log(request, "hr", "contract_signature_delete", cfg); messages.success(request, "Scan tanda tangan dihapus."); done = True
        elif act == "save_wage":
            wform = WageForm(request.POST)
            if wform.is_valid():
                w, _ = MinimumWage.objects.update_or_create(year=wform.cleaned_data["year"], defaults={"amount": wform.cleaned_data["amount"]})
                log(request, "hr", "minimum_wage_set", w, None, {"year": w.year, "amount": str(w.amount)}); messages.success(request, f"UMK {w.year} disimpan."); done = True
        elif act == "delete_wage" and (request.POST.get("year") or "").isdigit():
            MinimumWage.objects.filter(year=int(request.POST["year"])).delete(); log(request, "hr", "minimum_wage_delete", None, None, {"year": request.POST["year"]}); messages.success(request, "UMK dihapus."); done = True
        if done: return redirect("contract_settings")
    return render(request, "contract_settings.html", {"form": form, "wform": wform, "cfg": cfg, "wages": [(w, cp.rupiah(w.amount)) for w in MinimumWage.objects.all()], "placeholders": cp.PLACEHOLDERS,
                                                      "example": cp.next_number(date.today(), cfg), "year": date.today().year})


@login_required
@require_roles(*HRD_ROLES)
@require_GET
def contract_template_download(request):
    cfg = ContractConfig.get(); r = HttpResponse(cp.template_bytes(cfg), content_type=DOCX)
    r["Content-Disposition"] = f'attachment; filename="{cfg.template_name or "template_kontrak_pkwt.docx"}"'; r["X-Content-Type-Options"] = "nosniff"; return r


@login_required
@require_roles(*HRD_ROLES)
@require_GET
def contract_sample(request):
    """Uji template: isi dengan data contoh (Depan + Belakang) — dipakai setelah mengedit template, sebelum cetak sungguhan."""
    cfg = ContractConfig.get(); r = HttpResponse(cp.build(cp.template_bytes(cfg), [cp.sample_values(cfg)], "penuh", bytes(cfg.signature_png) if cfg.signature_png else None), content_type=DOCX)
    r["Content-Disposition"] = 'attachment; filename="Contoh-kontrak.docx"'; r["X-Content-Type-Options"] = "nosniff"; return r
