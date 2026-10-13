"""Situs publik: profil perusahaan, produk, artikel, lokasi toko, FAQ, kontak, karir + lamar kerja (putaran 48, Tahap 11).
Dipakai dalam dua cara: (1) mode `public` (config/urls_public.py, host terpisah) dan (2) pratinjau di aplikasi internal `/situs/` (hanya HRD/Superadmin, draf ikut tampil).
Tanpa sesi/login pelamar. Header: CSP ketat (tanpa inline), Referrer-Policy; halaman statis di-cache publik 60 dtk, halaman berformulir tidak pernah di-cache."""
import json
import mimetypes
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import TruncMonth
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from apps.core.middleware import hit
from apps.core.models import Notification, Role, User
from apps.core.net import client_ip
from . import careers, safe_upload
from .forms import ApplyForm, ContactForm, LookupForm, stamp
from .i18n import CONSENT_EN, CONSENT_ID, UI
from .models import (Application, Article, ContactMessage, Faq, Job, JobStatus, Look, Page, Product, ProductCategory, SiteSetting, StoreLocation, Stage, Status,
                     hash_ip, STAGE_PUBLIC)

MONTHS = {"id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]}


def lang_of(request):
    m = getattr(request, "resolver_match", None)
    return "en" if m and m.namespace == "en" else "id"


def is_preview(request):
    """Pratinjau draf: hanya di aplikasi internal, hanya HRD/Superadmin yang login. Mode public tidak pernah menampilkan draf."""
    u = getattr(request, "user", None)
    return settings.SITE_MODE != "public" and u is not None and u.is_authenticated and u.role in (Role.HRD, Role.SUPERADMIN)


def pub(qs, request, field="status", value=Status.PUBLISHED):
    return qs if is_preview(request) else qs.filter(**{field: value})


def _alt_url(request, lang):
    idh, enh = reverse("id:home"), reverse("en:home")
    path = request.path
    if lang == "id" and path.startswith(enh): return idh + path[len(enh):]
    if lang == "id": return enh + path[len(idh):]
    return idh + path[len(enh):] if path.startswith(enh) else path


def ui(lang, key): return UI.get(key, {}).get(lang) or UI.get(key, {}).get("id", key)


def ctx(request, **extra):
    lang = lang_of(request); s = SiteSetting.get()
    menu = list(pub(Page.objects.filter(show_in_menu=True), request))
    base = {"lang": lang, "S": s, "menu_pages": menu, "alt_url": _alt_url(request, lang), "alt_lang": "id" if lang == "en" else "en", "preview": is_preview(request),
            "open_jobs": Job.objects.filter(status=JobStatus.OPEN).count() if not is_preview(request) else Job.objects.exclude(status=JobStatus.CLOSED).count(),
            "title": "", "description": s.meta_description_en if lang == "en" and s.meta_description_en else s.meta_description_id,
            "base": settings.PUBLIC_BASE_URL.rstrip("/"), "page_path": request.path, "now": timezone.now(), "month_names": MONTHS[lang]}
    base.update(extra)
    return base


def respond(request, template, context, cache=True, status=200, noindex=False):
    r = render(request, template, context, status=status)
    r["Content-Security-Policy"] = settings.PUBLIC_CSP
    r["Referrer-Policy"] = "same-origin"
    r["X-Content-Type-Options"] = "nosniff"
    if noindex or context.get("preview"): r["X-Robots-Tag"] = "noindex, nofollow"
    if cache and not context.get("preview") and request.method in ("GET", "HEAD") and status == 200: patch_cache_control(r, public=True, max_age=60)
    else: patch_cache_control(r, private=True, no_store=True)
    return r


def too_many(request):
    return respond(request, "site/message.html", ctx(request, title=ui(lang_of(request), "too_many"), heading=ui(lang_of(request), "too_many"), text=""), cache=False, status=429, noindex=True)


def throttled(request, zone):
    cfg = settings.PUBLIC_THROTTLE.get(zone)
    if not cfg: return False
    over, _ = hit(f"pub:{zone}:{client_ip(request)}", cfg[0], cfg[1])
    return over


def notfound(request, *a, **k):
    return respond(request, "site/message.html", ctx(request, title=ui(lang_of(request), "not_found"), heading=ui(lang_of(request), "not_found"), text=""), cache=False, status=404, noindex=True)


# ------------------------------------------------------------------ Halaman konten
def marquee(prods):
    """Daftar produk untuk pita geser otomatis: diulang sampai cukup panjang agar putarannya mulus; kelas kecepatan menurut jumlah kartu."""
    prods = list(prods)
    if not prods: return [], ""
    items = prods * max(1, -(-8 // len(prods)))
    return items, ("m40" if len(items) <= 8 else "m70" if len(items) <= 16 else "m100")


def home(request):
    prods = list(pub(Product.objects.filter(featured=True), request).select_related("category__parent")[:12])
    if len(prods) < 4: prods = list(pub(Product.objects.all(), request).select_related("category__parent")[:12])  # belum cukup "unggulan": tampilkan produk terbaru
    mq, mqc = marquee(prods)
    arts = pub(Article.objects.all(), request)[:3]
    stores = StoreLocation.objects.filter(active=True)[:3]
    jobs = pub(Job.objects.all(), request, "status", JobStatus.OPEN)[:3]
    looks = list(look_qs(request).filter(featured=True)[:1]) or list(look_qs(request)[:1])
    return respond(request, "site/home.html", ctx(request, featured=prods, mq=mq, mqc=mqc, articles=arts, stores=stores, jobs=jobs, looks=looks))


def look_qs(request):
    """Setelan terbit (draf ikut tampil di pratinjau) beserta produknya, siap dirender tanpa query tambahan per baris."""
    prod = pub(Product.objects.select_related("category__parent"), request)
    return pub(Look.objects.all(), request).prefetch_related(Prefetch("products", queryset=prod))


def _tree(request):
    """Kelompok kategori aktif (+ subkategori aktif) untuk filter & bagian per kelompok."""
    subs = ProductCategory.objects.filter(active=True, parent__isnull=False)
    return list(ProductCategory.objects.filter(active=True, parent__isnull=True).prefetch_related(Prefetch("children", queryset=subs)))


def products(request):
    lang = lang_of(request)
    groups = _tree(request)
    qs = pub(Product.objects.select_related("category__parent"), request)
    cur = request.GET.get("c", "")
    cur_cat = next((c for g in groups for c in [g, *g.children.all()] if c.slug == cur), None)
    cur_group = cur_cat.group if cur_cat else None
    page = sections = None
    if cur_cat:
        qs = qs.filter(category__slug__in=cur_cat.tree_slugs()) if cur_cat.parent_id is None else qs.filter(category=cur_cat)
        page = Paginator(qs, 12).get_page(request.GET.get("page", 1))
        mq, mqc = [], ""
    else:
        if cur: qs = qs.none()   # ?c= tak dikenal → kosong (bukan semua), agar tautan lama tidak menyesatkan
        allp = list(qs)
        mq, mqc = marquee(allp[:24])
        if cur: sections = []
        else:   # “Semua” = per kelompok: beberapa produk tiap kelompok + tautan ke kelompoknya
            sections = []
            for g in groups:
                ids = {g.pk, *(c.pk for c in g.children.all())}
                items = [p for p in allp if p.category_id in ids]
                if items: sections.append({"g": g, "items": items[:4], "n": len(items)})
            rest = [p for p in allp if p.category_id is None or p.category_id not in {c.pk for g in groups for c in [g, *g.children.all()]}]
            if rest: sections.append({"g": None, "items": rest[:4], "n": len(rest)})
    return respond(request, "site/products.html", ctx(request, groups=groups, page=page, sections=sections, cur=cur if cur_cat else "", cur_cat=cur_cat, cur_group=cur_group,
                                                      mq=mq, mqc=mqc, unknown_cat=bool(cur and not cur_cat), title=(cur_cat.name_en if lang == "en" and cur_cat and cur_cat.name_en else cur_cat.name_id) if cur_cat else ui(lang, "products")))


def product(request, slug):
    p = get_object_or_404(pub(Product.objects.select_related("category__parent"), request), slug=slug)
    rel = []
    if p.category_id:
        rel = list(pub(Product.objects.exclude(pk=p.pk).filter(category=p.category), request).select_related("category__parent")[:3])
        if len(rel) < 3 and p.category.parent_id:   # kurang? lengkapi dari kelompok yang sama
            more = pub(Product.objects.exclude(pk__in=[p.pk, *(r.pk for r in rel)]).filter(category__parent_id=p.category.parent_id), request).select_related("category__parent")
            rel += list(more[:3 - len(rel)])
    looks = list(look_qs(request).filter(products=p)[:3])
    return respond(request, "site/product.html", ctx(request, p=p, related=rel, gallery=p.images.all(), looks=looks, title=p.name))


def lookbook(request):
    lang = lang_of(request)
    return respond(request, "site/looks.html", ctx(request, looks=list(look_qs(request)), title=ui(lang, "lookbook")))


def page(request, slug):
    pg = get_object_or_404(pub(Page.objects.all(), request), slug=slug)
    return respond(request, "site/page.html", ctx(request, pg=pg))


def about(request):
    return page(request, "tentang-kami")


def stores(request):
    return respond(request, "site/stores.html", ctx(request, stores=StoreLocation.objects.filter(active=True), title=ui(lang_of(request), "stores")))


def faq(request):
    return respond(request, "site/faq.html", ctx(request, faqs=Faq.objects.filter(active=True), title=ui(lang_of(request), "faq")))


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def articles(request):
    lang = lang_of(request)
    qs = pub(Article.objects.all(), request)
    y, m = _int(request.GET.get("y")), _int(request.GET.get("m"))
    label = ""
    if y and m and 1 <= m <= 12:
        qs = qs.filter(published_at__year=y, published_at__month=m); label = f"{MONTHS[lang][m - 1]} {y}"
    page = Paginator(qs, 9).get_page(request.GET.get("page", 1))
    return respond(request, "site/articles.html", ctx(request, page=page, label=label, title=ui(lang, "articles")))


def article(request, slug):
    a = get_object_or_404(pub(Article.objects.all(), request), slug=slug)
    return respond(request, "site/article.html", ctx(request, a=a))


def archive(request):
    lang = lang_of(request)
    rows = (pub(Article.objects.exclude(published_at=None), request).annotate(mo=TruncMonth("published_at")).values("mo").annotate(n=Count("id")).order_by("-mo"))
    groups = OrderedDict()
    for r in rows: groups.setdefault(r["mo"].year, []).append({"y": r["mo"].year, "m": r["mo"].month, "name": MONTHS[lang][r["mo"].month - 1], "n": r["n"]})
    return respond(request, "site/archive.html", ctx(request, groups=groups, title=ui(lang, "archive")))


def search(request):
    lang = lang_of(request); q = request.GET.get("q", "").strip()[:80]
    prods, arts = [], []
    if q:
        if throttled(request, "search"): return too_many(request)
        f = Q(name__icontains=q) | Q(summary_id__icontains=q) | Q(summary_en__icontains=q) | Q(motif__icontains=q)
        prods = list(pub(Product.objects.filter(f | Q(category__name_id__icontains=q) | Q(category__name_en__icontains=q)).select_related("category__parent"), request)[:12])
        arts = list(pub(Article.objects.filter(Q(title_id__icontains=q) | Q(title_en__icontains=q) | Q(excerpt_id__icontains=q) | Q(excerpt_en__icontains=q)), request)[:12])
    return respond(request, "site/search.html", ctx(request, q=q, prods=prods, arts=arts, title=ui(lang, "search")), cache=False, noindex=True)


@require_http_methods(["GET", "POST"])
def contact(request):
    lang = lang_of(request)
    if request.method == "POST":
        if throttled(request, "contact"): return too_many(request)
        form = ContactForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            ContactMessage.objects.create(name=d["name"], email=d["email"], phone=d["phone"], subject=d["subject"], message=d["message"], lang=lang, ip_hash=hash_ip(client_ip(request)))
            Notification.objects.bulk_create([Notification(user=u, kind="info", title=f"Pesan baru dari situs: {d['name']}"[:200], link="/company/messages/")
                                              for u in User.objects.filter(is_active=True, role__in=(Role.HRD, Role.SUPERADMIN))])
            return redirect(reverse(f"{lang}:contact_done"))
    else:
        form = ContactForm(initial={"ts": stamp()})
    return respond(request, "site/contact.html", ctx(request, form=form, title=ui(lang, "contact")), cache=False)


def contact_done(request):
    lang = lang_of(request)
    return respond(request, "site/message.html", ctx(request, title=ui(lang, "sent_title"), heading=ui(lang, "sent_title"), text=ui(lang, "sent_text")), cache=False)


# ------------------------------------------------------------------ Karir
def careers_list(request):
    lang = lang_of(request)
    jobs = pub(Job.objects.all(), request, "status", JobStatus.OPEN)
    if not is_preview(request): jobs = [j for j in jobs if j.is_open]
    return respond(request, "site/careers.html", ctx(request, jobs=jobs, title=ui(lang, "careers")))


def _job(request, slug):
    j = get_object_or_404(Job, slug=slug)
    if j.status == JobStatus.DRAFT and not is_preview(request): raise Http404
    return j


def job(request, slug):
    j = _job(request, slug)
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": j.title, "description": (j.summary or j.title), "datePosted": j.created_at.date().isoformat(),
          "employmentType": {"tetap": "FULL_TIME", "kontrak": "CONTRACT", "magang": "INTERN", "harian": "TEMPORARY"}.get(j.employment_type, "OTHER"),
          "hiringOrganization": {"@type": "Organization", "name": SiteSetting.get().brand_name}}
    if j.closes_on: ld["validThrough"] = j.closes_on.isoformat()
    if j.location: ld["jobLocation"] = {"@type": "Place", "address": j.location}
    jsonld = json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c") if j.is_open else ""
    return respond(request, "site/job.html", ctx(request, j=j, jsonld=jsonld, title=j.title))


@require_http_methods(["GET", "POST"])
def apply(request, slug):
    lang = lang_of(request)
    j = _job(request, slug)
    if not j.is_open and not (is_preview(request) and j.status != JobStatus.CLOSED): return redirect(reverse(f"{lang}:job", args=[j.slug]))
    if request.method == "POST":
        if is_preview(request): return redirect(reverse(f"{lang}:job", args=[j.slug]))   # pratinjau tidak membuat lamaran
        if throttled(request, "apply"): return too_many(request)
        form = ApplyForm(request.POST, request.FILES, job=j)
        if form.is_valid():
            d = dict(form.cleaned_data); d["answers"] = form.answers()
            from .models import hash_email
            from datetime import timedelta
            if Application.objects.filter(email_hash=hash_email(d["email"]), created_at__gte=timezone.now() - timedelta(days=1)).exclude(idem_key=d["idem"]).count() >= 3:
                form.add_error("email", "Terlalu banyak lamaran dari email ini hari ini. Coba lagi besok." if lang == "id" else "Too many applications from this email today. Please try again tomorrow.")
            else:
                try:
                    careers.create_application(j, d, form.cleaned_data["cv"], client_ip(request), lang)
                    return redirect(reverse(f"{lang}:apply_done"))
                except ValidationError as e: form.add_error("cv", e)
                except safe_upload.ScanUnavailable:
                    form.add_error("cv", "Pemeriksaan keamanan berkas sedang tidak tersedia. Coba lagi beberapa menit lagi." if lang == "id" else "File security check is temporarily unavailable. Please try again in a few minutes.")
    else:
        form = ApplyForm(job=j)
    return respond(request, "site/apply.html", ctx(request, j=j, form=form, consent=CONSENT_EN if lang == "en" else CONSENT_ID, title=ui(lang, "apply") + " — " + j.title), cache=False, noindex=True)


def apply_done(request):
    lang = lang_of(request)
    return respond(request, "site/message.html", ctx(request, title=ui(lang, "app_done"), heading=ui(lang, "app_done"), text=ui(lang, "app_done_text")), cache=False, noindex=True)


@require_http_methods(["GET", "POST"])
def lookup(request):
    lang = lang_of(request); done = False
    if request.method == "POST":
        if throttled(request, "lookup"): return too_many(request)
        form = LookupForm(request.POST)
        if form.is_valid() and not form.cleaned_data["website"]:
            careers.resend_link(form.cleaned_data["email"], lang); done = True
    else: form = LookupForm()
    return respond(request, "site/lookup.html", ctx(request, form=form, done=done, title=ui(lang, "forgot_link")), cache=False, noindex=True)


def _app(token):
    app = careers.find_by_token(token)
    if not app: raise Http404
    return app


@require_http_methods(["GET", "POST"])
def status(request, token):
    lang = lang_of(request)
    app = _app(token)
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "withdraw": careers.withdraw(app)
        elif act == "delete" and request.POST.get("confirm"):
            careers.anonymize(app, reason="atas permintaan pelamar")
            return respond(request, "site/message.html", ctx(request, title=ui(lang, "delete_data"), heading=ui(lang, "delete_data"),
                           text="Data Anda sudah dihapus." if lang == "id" else "Your data has been deleted."), cache=False, noindex=True)
        return redirect(request.path)
    careers.verify(app)
    app.refresh_from_db()
    events = [e for e in app.events.all() if e.kind in ("submitted", "verified", "stage")]
    r = respond(request, "site/status.html", ctx(request, app=app, events=events, token=token, title=ui(lang, "track"),
                                                 stage_text=(STAGE_PUBLIC[Stage(app.stage)] if lang == "id" else EN_TEXT[app.stage]), STAGE_EN=careers.STAGE_EN), cache=False, noindex=True)
    r["Referrer-Policy"] = "no-referrer"
    return r


EN_TEXT = {"unverified": "We are waiting for you to open the verification link in your email. After that your application goes to our HR team.",
           "new": "We have received your application. Our HR team will review your documents.", "screening": "Your documents are being reviewed. If they match, we will contact you for the next step.",
           "test": "You passed the document review and moved to the assessment stage. We will tell you the schedule by email or phone.",
           "interview": "You are at the interview stage. Our team will contact you to arrange a time.", "offer": "We are preparing or have sent a job offer. Please check your email and phone.",
           "hired": "Congratulations, you are hired. Our HR team will contact you about the next administrative steps.",
           "rejected": "Thank you for applying. For now we are unable to move your application forward.", "withdrawn": "You withdrew this application."}


def status_data(request, token):
    app = _app(token)
    r = JsonResponse(careers.export_data(app), json_dumps_params={"ensure_ascii": False, "indent": 2})
    r["Content-Disposition"] = f'attachment; filename="{app.ref}.json"'
    r["Cache-Control"] = "no-store"; r["Referrer-Policy"] = "no-referrer"; r["X-Content-Type-Options"] = "nosniff"
    return r


# ------------------------------------------------------------------ Teknis
def media_file(request, name):
    """Gambar situs (publik). Hanya di bawah company/ pada MEDIA_ROOT; ekstensi dibatasi; tidak ada penelusuran direktori."""
    root = (Path(settings.MEDIA_ROOT) / "company").resolve()
    p = (Path(settings.MEDIA_ROOT) / name).resolve()
    ext = p.suffix.lower().lstrip(".")
    if not name.startswith("company/") or root not in p.parents or ext not in ("jpg", "jpeg", "png", "webp") or not p.is_file(): raise Http404
    r = FileResponse(open(p, "rb"), content_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")
    r["X-Content-Type-Options"] = "nosniff"; r["Cache-Control"] = "public, max-age=86400"
    r["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return r


def robots(request):
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    body = f"User-agent: *\nDisallow: /karir/status/\nDisallow: /cari/\nAllow: /\n" + (f"Sitemap: {base}/sitemap.xml\n" if base else "")
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def sitemap(request):
    base = settings.PUBLIC_BASE_URL.rstrip("/") or f"{request.scheme}://{request.get_host()}"
    urls = ["/", "/produk/", "/lookbook/", "/tentang-kami/", "/lokasi-toko/", "/artikel/", "/arsip/", "/kontak/", "/faq/", "/karir/"]
    urls += [f"/produk/{p.slug}/" for p in Product.objects.filter(status=Status.PUBLISHED)]
    urls += [f"/artikel/{a.slug}/" for a in Article.objects.filter(status=Status.PUBLISHED)]
    urls += [f"/karir/{j.slug}/" for j in Job.objects.filter(status=JobStatus.OPEN) if j.is_open]
    urls += [f"/halaman/{p.slug}/" for p in Page.objects.filter(status=Status.PUBLISHED).exclude(slug="tentang-kami")]
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(
        f"<url><loc>{base}{u}</loc></url><url><loc>{base}/en{u}</loc></url>" for u in urls) + "</urlset>"
    return HttpResponse(xml, content_type="application/xml")


def healthz(request):
    from django.db import connection
    with connection.cursor() as c: c.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


def favicon(request):
    """/favicon.ico di akar host (peramban memintanya otomatis); berkas yang sama dengan <link rel=icon>."""
    r = FileResponse(open(Path(__file__).parent / "static" / "company" / "favicon.ico", "rb"), content_type="image/x-icon")
    patch_cache_control(r, public=True, max_age=86400)
    return r
