"""Backend kontrol Company Profile & Rekrutmen (aplikasi internal). Hanya HRD/Superadmin (Admin Dept & Poli → 403). Semua perubahan diaudit (ringkasan nama kolom,
tanpa isi data pribadi). Konten situs tidak dihapus diam-diam: hapus butuh konfirmasi; gambar lama dibersihkan saat diganti/dihapus."""
import json
import re
from functools import wraps
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from . import careers, safe_upload
from .forms import (ArticleForm, CategoryForm, FaqForm, JobForm, LookForm, MoveForm, PageForm, ProductForm, ProductImageForm, SiteSettingForm, StoreForm)
from .models import (ACTIVE_STAGES, Application, Article, ContactMessage, Faq, Job, JobStatus, Look, MessageStatus, Page, Product, ProductCategory, ProductImage, SiteSetting,
                     Stage, StoreLocation, Status)

PER_PAGE = 30


def hrd_only(fn):
    @wraps(fn)
    @login_required
    @require_roles(Role.HRD)
    def wrap(*a, **k): return fn(*a, **k)
    return wrap


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _pill(value, label): return format_html('<span class="pill {}">{}</span>', value, label)


def _two(main, sub): return format_html("{}<small>{}</small>", main, sub or "—")


def _rupiah(v): return "—" if v is None else "Rp " + f"{int(v):,}".replace(",", ".")


# ================================================================ CRUD generik (konten situs)
class Crud:
    """Satu jenis konten = daftar + tambah + ubah + hapus. `columns` = [(judul, kelas-css, fungsi(obj)→teks/HTML)] (≤ 6 kolom)."""
    def __init__(self, key, model, form, title, plural, columns, search=(), status_choices=None, files=(), order=None, qs=None, hint="", delete=True):
        self.key, self.model, self.form, self.title, self.plural, self.columns = key, model, form, title, plural, columns
        self.search, self.status_choices, self.files, self.order, self.qs_hook, self.hint, self.can_delete = search, status_choices, files, order, qs, hint, delete
        self.base = f"/{'recruitment' if key == 'jobs' else 'company'}/{key}/"

    def queryset(self, request):
        qs = self.model.objects.all()
        if self.qs_hook: qs = self.qs_hook(qs)
        q = request.GET.get("q", "").strip()
        if q and self.search:
            cond = Q()
            for f in self.search: cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        st = request.GET.get("status", "")
        if st and self.status_choices and st in dict(self.status_choices): qs = qs.filter(**{self.status_field: st})
        return qs.order_by(*self.order) if self.order else qs

    status_field = "status"

    def list(self, request):
        f = {"q": request.GET.get("q", "").strip(), "status": request.GET.get("status", "")}
        page = Paginator(self.queryset(request), PER_PAGE).get_page(request.GET.get("page", 1))
        rows = [{"obj": o, "cells": [c[2](o) for c in self.columns], "edit": f"{self.base}{o.pk}/"} for o in page]
        return render(request, "company/list.html", {"c": self, "page": page, "rows": rows, "f": f, "qs": urlencode({k: v for k, v in f.items() if v}), "back": self.back()})

    def back(self): return "/recruitment/" if self.key == "jobs" else "/company/"

    def _save(self, request, form, creating, old_files=None):
        old_files = old_files or {}
        obj = form.save(commit=False)
        if hasattr(obj, "updated_by"): obj.updated_by = request.user
        obj.save(); form.save_m2m()
        for f, old in old_files.items():
            new = getattr(obj, f).name if getattr(obj, f) else ""
            if old and old != new: _remove_file(old)
        log(request, "company", "create" if creating else "update", obj, after={"fields": sorted(form.changed_data)} if not creating else {"title": str(obj)[:80]})
        return obj

    def edit(self, request, pk=None):
        obj = get_object_or_404(self.model, pk=pk) if pk else None
        old_files = {f: getattr(obj, f).name for f in self.files if obj is not None and getattr(obj, f)}   # sebelum form menimpa instance
        form = self.form(request.POST or None, request.FILES or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            saved = self._save(request, form, creating=obj is None, old_files=old_files)
            messages.success(request, f"{self.title} disimpan.")
            nxt = self.after_save(request, saved, obj is None)
            return redirect(nxt)
        extra = self.extra_context(request, obj)
        return render(request, "company/form.html", {"c": self, "form": form, "obj": obj, "title": (f"Ubah {self.title.lower()}" if obj else f"Tambah {self.title.lower()}"), "back": self.base, **extra})

    def after_save(self, request, obj, created):
        if "again" in request.POST: return self.base + "new/"
        return f"{self.base}{obj.pk}/" if self.key == "products" and created else self.base

    def extra_context(self, request, obj): return {}

    def delete(self, request, pk):
        obj = get_object_or_404(self.model, pk=pk)
        if not self.can_delete: raise Http404
        files = [getattr(obj, f).name for f in self.files if getattr(obj, f, None)]
        if isinstance(obj, Product): files += [i.image.name for i in obj.images.all() if i.image]
        name = str(obj)[:80]
        log(request, "company", "delete", obj, before={"title": name})
        obj.delete()
        for n in files: _remove_file(n)
        messages.success(request, f"{self.title} “{name}” dihapus.")
        return redirect(self.base)


def _remove_file(name):
    try:
        if name and name.startswith("company/"): default_storage.delete(name)
    except OSError: pass


class ProductCrud(Crud):
    def extra_context(self, request, obj):
        return {"gallery": obj.images.all() if obj else [], "gallery_form": ProductImageForm() if obj else None}


PUB = [(Status.DRAFT.value, "Draf"), (Status.PUBLISHED.value, "Terbit")]
OPEN = [(JobStatus.DRAFT.value, "Draf"), (JobStatus.OPEN.value, "Dibuka"), (JobStatus.CLOSED.value, "Ditutup")]
CRUDS = {
    "products": ProductCrud("products", Product, ProductForm, "Produk", "Produk",
        [("Produk", "fill", lambda o: _two(o.name, o.summary_id)), ("Kategori", "nw", lambda o: o.category or "—"), ("Foto model", "nw", lambda o: "Ada" if o.model_image else "—"), ("Harga", "num nw", lambda o: _rupiah(o.price) if o.show_price else "disembunyikan"),
         ("Status", "nw", lambda o: _pill(o.status, o.get_status_display() + (" ★" if o.featured else "")))], search=("name", "summary_id", "motif"), status_choices=PUB, files=("image", "model_image"), order=("order", "name")),
    "looks": Crud("looks", Look, LookForm, "Lookbook", "Lookbook",
        [("Setelan", "fill", lambda o: _two(o.title_id, o.occasion_id or o.story_id[:70])), ("Produk", "num nw", lambda o: o.n_items), ("Foto", "nw", lambda o: "Ada" if o.photo else "Belum"),
         ("Status", "nw", lambda o: _pill(o.status, o.get_status_display() + (" ★" if o.featured else "")))], search=("title_id", "title_en", "occasion_id"), status_choices=PUB,
        files=("photo",), qs=lambda q: q.annotate(n_items=Count("products")), order=("order", "-id"),
        hint="Satu setelan difoto pada model; produk yang dicentang tampil sebagai daftar detail di sebelah foto (di beranda dan halaman Lookbook)."),
    "categories": Crud("categories", ProductCategory, CategoryForm, "Kategori produk", "Kategori produk",
        [("Kategori", "fill", lambda o: _two(("— " if o.parent_id else "") + o.name_id, o.hint_id or o.name_en)), ("Kelompok", "nw", lambda o: o.parent.name_id if o.parent_id else "(kelompok)"), ("Produk", "num nw", lambda o: o.n_products), ("Urutan", "num nw", lambda o: o.order), ("Status", "nw", lambda o: _pill("aktif" if o.active else "nonaktif", "Aktif" if o.active else "Nonaktif"))],
        search=("name_id", "name_en"), qs=lambda q: q.select_related("parent").annotate(n_products=Count("products")), order=("parent__order", "order", "name_id"),
        hint="Dua tingkat: buat KELOMPOK dulu (Bawahan, Atasan, Aksesoris, …; kolom induk dikosongkan), lalu SUBKATEGORI di dalamnya (Sarung, Celana Sarung, Baju Koko, Peci, Surban, …)."),
    "articles": Crud("articles", Article, ArticleForm, "Artikel", "Artikel",
        [("Judul", "fill", lambda o: _two(o.title_id, o.excerpt_id[:90])), ("Penulis", "nw", lambda o: o.author or "—"), ("Terbit", "nw", lambda o: o.published_at.strftime("%d %b %Y") if o.published_at else "—"),
         ("Status", "nw", lambda o: _pill(o.status, o.get_status_display()))], search=("title_id", "title_en", "author"), status_choices=PUB, files=("cover",), order=("-updated_at",)),
    "pages": Crud("pages", Page, PageForm, "Halaman", "Halaman",
        [("Halaman", "fill", lambda o: _two(o.title_id, "/" + ("tentang-kami" if o.slug == "tentang-kami" else "halaman/" + o.slug) + "/")), ("Menu", "nw", lambda o: "Ya" if o.show_in_menu else "Tidak"),
         ("Urutan", "num nw", lambda o: o.order), ("Status", "nw", lambda o: _pill(o.status, o.get_status_display()))], search=("title_id", "slug"), status_choices=PUB, order=("order",),
        hint="Halaman “tentang-kami” tampil di menu Tentang Kami. Halaman lain tampil di /halaman/alamat/ dan, bila dicentang, di menu atas."),
    "stores": Crud("stores", StoreLocation, StoreForm, "Lokasi toko", "Lokasi toko",
        [("Toko", "fill", lambda o: _two(o.name, o.address[:80])), ("Kota", "nw", lambda o: o.city), ("Telepon", "nw", lambda o: o.phone or "—"), ("Status", "nw", lambda o: _pill("aktif" if o.active else "nonaktif", "Tampil" if o.active else "Disembunyikan"))],
        search=("name", "city", "address"), order=("order", "city")),
    "faqs": Crud("faqs", Faq, FaqForm, "FAQ", "FAQ",
        [("Pertanyaan", "fill", lambda o: o.question_id), ("Urutan", "num nw", lambda o: o.order), ("Status", "nw", lambda o: _pill("aktif" if o.active else "nonaktif", "Tampil" if o.active else "Disembunyikan"))],
        search=("question_id", "answer_id"), order=("order", "id")),
    "jobs": Crud("jobs", Job, JobForm, "Lowongan", "Lowongan",
        [("Posisi", "fill", lambda o: _two(o.title, " · ".join(x for x in (o.department_name, o.location) if x))), ("Jenis", "nw", lambda o: o.get_employment_type_display()),
         ("Batas", "nw", lambda o: o.closes_on.strftime("%d %b %Y") if o.closes_on else "—"), ("Pelamar", "num nw", lambda o: o.n_apps), ("Status", "nw", lambda o: _pill(o.status, o.get_status_display()))],
        search=("title", "department_name", "location"), status_choices=OPEN, order=("-created_at",),
        qs=lambda q: q.annotate(n_apps=Count("applications", filter=~Q(applications__stage="unverified") & Q(applications__anonymized_at__isnull=True)))),
}


def _crud(kind):
    c = CRUDS.get(kind)
    if not c: raise Http404
    return c


@hrd_only
def crud_list(request, kind): return _crud(kind).list(request)
@hrd_only
def crud_new(request, kind): return _crud(kind).edit(request)
@hrd_only
def crud_edit(request, kind, pk): return _crud(kind).edit(request, pk)
@hrd_only
@require_POST
def crud_delete(request, kind, pk): return _crud(kind).delete(request, pk)


# ------------------------------------------------------------------ Galeri produk
@hrd_only
@require_POST
def product_image_add(request, pk):
    p = get_object_or_404(Product, pk=pk)
    if p.images.count() >= 8:
        messages.error(request, "Maksimal 8 foto galeri per produk."); return redirect(f"/company/products/{p.pk}/")
    form = ProductImageForm(request.POST, request.FILES)
    if form.is_valid():
        img = form.save(commit=False); img.product = p; img.save()
        log(request, "company", "update", p, after={"fields": ["galeri"]}); messages.success(request, "Foto ditambahkan.")
    else:
        messages.error(request, "Foto tidak dapat disimpan: " + "; ".join(sum((list(e) for e in form.errors.values()), [])))
    return redirect(f"/company/products/{p.pk}/")


@hrd_only
@require_POST
def product_image_delete(request, pk, image_id):
    img = get_object_or_404(ProductImage, pk=image_id, product_id=pk)
    name = img.image.name; img.delete(); _remove_file(name)
    log(request, "company", "update", img.product, after={"fields": ["galeri"]}); messages.success(request, "Foto dihapus.")
    return redirect(f"/company/products/{pk}/")


# ================================================================ Hub & pengaturan
SETTING_GROUPS = [
    ("Identitas", ["brand_name", "logo", "tagline_id", "tagline_en"]),
    ("Beranda", ["hero_title_id", "hero_title_en", "hero_text_id", "hero_text_en"]),
    ("Kontak & lokasi", ["address", "phone", "whatsapp", "email", "hours_id", "hours_en", "maps_url"]),
    ("Media sosial & toko online", ["instagram", "facebook", "tiktok", "marketplace_url"]),
    ("Pita pengumuman (mis. “Website dalam pengembangan”)", ["banner_active", "banner_id", "banner_en"]),
    ("Karir, pencarian & catatan kaki", ["careers_intro_id", "careers_intro_en", "meta_description_id", "meta_description_en", "footer_note_id", "footer_note_en"]),
]


@hrd_only
def hub(request):
    new_apps = Application.objects.filter(stage=Stage.NEW, anonymized_at__isnull=True).count()
    cards = [
        ("Produk", "/company/products/", Product.objects.filter(status="published").count(), f"terbit dari {Product.objects.count()} produk"),
        ("Lookbook", "/company/looks/", Look.objects.filter(status="published").count(), f"setelan terbit dari {Look.objects.count()} (foto model + daftar produk)"),
        ("Artikel", "/company/articles/", Article.objects.filter(status="published").count(), f"terbit dari {Article.objects.count()} artikel"),
        ("Lokasi toko", "/company/stores/", StoreLocation.objects.filter(active=True).count(), "toko tampil di situs"),
        ("Pesan masuk", "/company/messages/", ContactMessage.objects.filter(status="new").count(), "pesan baru dari formulir Kontak"),
        ("Lowongan", "/recruitment/jobs/", Job.objects.filter(status="open").count(), "lowongan dibuka"),
        ("Pelamar", "/recruitment/applicants/", new_apps, "lamaran baru belum diproses"),
    ]
    return render(request, "company/hub.html", {"cards": cards, "public_url": settings.PUBLIC_BASE_URL, "preview": settings.PUBLIC_PREVIEW})


@hrd_only
def site_settings(request):
    obj = SiteSetting.get()
    form = SiteSettingForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        old = obj.logo.name if obj.logo else ""
        o = form.save(commit=False); o.updated_by = request.user; o.save()
        if old and old != (o.logo.name if o.logo else ""): _remove_file(old)
        log(request, "company", "update", o, after={"fields": sorted(form.changed_data)})
        messages.success(request, "Pengaturan situs disimpan."); return redirect("/company/settings/")
    groups = [(t, [form[n] for n in names]) for t, names in SETTING_GROUPS]
    return render(request, "company/form.html", {"form": form, "groups": groups, "obj": obj, "title": "Pengaturan situs", "back": "/company/", "c": None})


# ================================================================ Pesan masuk
@hrd_only
def message_list(request):
    f = {"status": request.GET.get("status", ""), "q": request.GET.get("q", "").strip()}
    qs = ContactMessage.objects.all()
    if f["status"] in MessageStatus.values: qs = qs.filter(status=f["status"])
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(subject__icontains=f["q"]))
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page", 1))
    return render(request, "company/messages.html", {"page": page, "f": f, "statuses": MessageStatus.choices, "qs": urlencode({k: v for k, v in f.items() if v})})


@hrd_only
def message_detail(request, pk):
    m = get_object_or_404(ContactMessage, pk=pk)
    if request.method == "POST":
        st = request.POST.get("status")
        if st in MessageStatus.values and st != m.status:
            old = m.status; m.status = st; m.handled_by = request.user; m.handled_at = timezone.now()
            m.note = request.POST.get("note", "")[:300]; m.save()
            log(request, "company", "update", m, before={"status": old}, after={"status": st}); messages.success(request, "Pesan diperbarui.")
        elif "note" in request.POST:
            m.note = request.POST.get("note", "")[:300]; m.save(update_fields=["note"])
        return redirect(f"/company/messages/{m.pk}/")
    if m.status == MessageStatus.NEW:
        m.status = MessageStatus.READ; m.handled_by = request.user; m.handled_at = timezone.now(); m.save(update_fields=["status", "handled_by", "handled_at"])
    log(request, "company", "view", m)   # isi pesan (data pribadi) tidak masuk audit; hanya fakta dibuka
    return render(request, "company/message_detail.html", {"m": m, "statuses": MessageStatus.choices})


# ================================================================ Rekrutmen
@hrd_only
def recruit_hub(request):
    cards = [("Lowongan", "/recruitment/jobs/", Job.objects.filter(status="open").count(), "lowongan dibuka"),
             ("Lamaran baru", "/recruitment/applicants/?stage=new", Application.objects.filter(stage="new", anonymized_at__isnull=True).count(), "belum diproses"),
             ("Dalam proses", "/recruitment/applicants/", Application.objects.filter(stage__in=("screening", "test", "interview", "offer"), anonymized_at__isnull=True).count(), "seleksi berjalan"),
             ("Menunggu verifikasi", "/recruitment/applicants/?stage=unverified", Application.objects.filter(stage="unverified", anonymized_at__isnull=True).count(), "pelamar belum klik tautan email")]
    return render(request, "company/hub.html", {"cards": cards, "recruit": True, "public_url": settings.PUBLIC_BASE_URL, "preview": settings.PUBLIC_PREVIEW})


def _app_filters(request):
    return {"q": request.GET.get("q", "").strip(), "job": request.GET.get("job", ""), "stage": request.GET.get("stage", "") or "active"}


def _app_qs(f):
    qs = Application.objects.select_related("job").filter(anonymized_at__isnull=True)
    if f["q"]: qs = qs.filter(Q(full_name__icontains=f["q"]) | Q(ref__icontains=f["q"]))
    if _int(f["job"]): qs = qs.filter(job_id=_int(f["job"]))
    return qs


@hrd_only
def applicants(request):
    f = _app_filters(request)
    base = _app_qs(f)
    counts = dict(base.values_list("stage").annotate(n=Count("id")).order_by())
    qs = base
    if f["stage"] == "active": qs = qs.filter(stage__in=[s.value for s in ACTIVE_STAGES])
    elif f["stage"] in Stage.values: qs = qs.filter(stage=f["stage"])
    elif f["stage"] == "all": qs = qs.exclude(stage="unverified")
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page", 1))
    tabs = [("active", "Aktif", sum(counts.get(s.value, 0) for s in ACTIVE_STAGES))] + [(s.value, s.label, counts.get(s.value, 0)) for s in (Stage.NEW, Stage.SCREENING, Stage.TEST, Stage.INTERVIEW, Stage.OFFER, Stage.HIRED, Stage.REJECTED, Stage.UNVERIFIED)]
    move_to = [(s.value, s.label) for s in Stage if s.value not in ("unverified", "withdrawn")]
    return render(request, "company/applicants.html", {"page": page, "f": f, "tabs": tabs, "jobs": Job.objects.all(), "move_to": move_to,
                  "qs": urlencode({k: v for k, v in f.items() if v and not (k == "stage" and v == "active")})})


@hrd_only
def applicant_detail(request, pk):
    a = get_object_or_404(Application, pk=pk)
    log(request, "recruitment", "view", a)   # akses data pelamar tercatat (UU PDP); isi tidak masuk audit
    return render(request, "company/applicant_detail.html", {"a": a, "answers": a.answers_list(), "events": a.events.select_related("by"), "move_form": MoveForm(), "closed": bool(a.anonymized_at)})


@hrd_only
@require_POST
def applicant_move(request, pk):
    a = get_object_or_404(Application, pk=pk)
    form = MoveForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        old = a.stage
        if careers.move(a, d["to"], request.user, d["public_message"], d["note"]):
            log(request, "recruitment", "move", a, before={"stage": old}, after={"stage": d["to"]}); messages.success(request, f"Lamaran dipindah ke “{Stage(d['to']).label}”. Pelamar diberi tahu lewat email.")
        else: messages.error(request, "Tahap tidak dapat diubah (lamaran belum terverifikasi, sudah dibatalkan, atau tahap sama).")
    else: messages.error(request, "Pilihan tahap tidak valid.")
    return redirect(f"/recruitment/applicants/{a.pk}/")


@hrd_only
@require_POST
def applicant_note(request, pk):
    a = get_object_or_404(Application, pk=pk)
    if not a.anonymized_at:
        a.internal_note = request.POST.get("note", "")[:500]; a.save(update_fields=["internal_note", "updated_at"])
        log(request, "recruitment", "note", a); messages.success(request, "Catatan disimpan.")
    return redirect(f"/recruitment/applicants/{a.pk}/")


def _safe_next(request):
    """Tujuan kembali hanya ke halaman Rekrutmen (path relatif); selain itu daftar pelamar. Mencegah open redirect."""
    nxt = request.POST.get("next", "")
    return nxt if re.fullmatch(r"/recruitment/[\w/?&=%.-]*", nxt) and "//" not in nxt else "/recruitment/applicants/"


@hrd_only
@require_POST
def applicants_bulk(request):
    ids = [i for i in (_int(x) for x in request.POST.getlist("ids")) if i][:100]
    to = request.POST.get("to", "")
    if not ids or to not in Stage.values or to in ("unverified", "withdrawn"):
        messages.error(request, "Pilih pelamar dan tahap tujuan."); return redirect(_safe_next(request))
    msg = request.POST.get("public_message", "")[:500]; n = 0
    for a in Application.objects.filter(pk__in=ids, anonymized_at__isnull=True):
        old = a.stage
        if careers.move(a, to, request.user, msg): n += 1; log(request, "recruitment", "move", a, before={"stage": old}, after={"stage": to})
    messages.success(request, f"{n} lamaran dipindah ke “{Stage(to).label}”.")
    return redirect(_safe_next(request))


@hrd_only
def applicant_cv(request, pk):
    a = get_object_or_404(Application, pk=pk)
    if a.anonymized_at or not a.cv_path: raise Http404
    try: data = safe_upload.read_private(a.cv_path)
    except FileNotFoundError: raise Http404
    careers.event(a, "download", by=request.user)
    log(request, "recruitment", "download", a)
    ext = a.cv_name.rsplit(".", 1)[-1] if "." in a.cv_name else "bin"
    r = HttpResponse(data, content_type="application/octet-stream")
    r["Content-Disposition"] = f'attachment; filename="{a.ref}-cv.{re.sub(r"[^a-z0-9]", "", ext.lower())}"'
    r["X-Content-Type-Options"] = "nosniff"; r["Cache-Control"] = "no-store"
    return r


@hrd_only
@require_POST
def applicant_anonymize(request, pk):
    a = get_object_or_404(Application, pk=pk)
    if careers.anonymize(a, by=request.user, reason=request.POST.get("reason", "")):
        log(request, "recruitment", "anonymize", a); messages.success(request, "Data pribadi pelamar dihapus. Jejak tahap tetap tersimpan.")
    return redirect(f"/recruitment/applicants/{a.pk}/")


@hrd_only
def applicant_export(request, pk):
    a = get_object_or_404(Application, pk=pk)
    if a.anonymized_at: raise Http404
    careers.event(a, "exported", by=request.user); log(request, "recruitment", "export", a)
    r = JsonResponse(careers.export_data(a), json_dumps_params={"ensure_ascii": False, "indent": 2})
    r["Content-Disposition"] = f'attachment; filename="{a.ref}.json"'; r["Cache-Control"] = "no-store"
    return r
