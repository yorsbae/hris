"""Tag templat situs publik: bahasa (`t`), tautan berbahasa (`purl`), teks kaya aman (`rich`), alamat media."""
import re
from django import template
from django.urls import reverse
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def t(context, obj, field):
    """Nilai `field_<bahasa>`; kosong → jatuh ke `field_id`; objek tanpa varian bahasa → `field`."""
    lang = context.get("lang", "id")
    if obj is None: return ""
    if lang == "en":
        v = getattr(obj, f"{field}_en", "")
        if v: return v
    v = getattr(obj, f"{field}_id", None)
    return v if v is not None else getattr(obj, field, "")


@register.simple_tag(takes_context=True)
def purl(context, name, *args):
    return reverse(f"{context.get('lang', 'id')}:{name}", args=args)


@register.simple_tag(takes_context=True)
def ui(context, key):
    from apps.company.i18n import UI
    return UI.get(key, {}).get(context.get("lang", "id")) or UI.get(key, {}).get("id", key)


@register.filter
def media(f):
    """URL berkas publik (view media situs). Kosong bila tidak ada berkas."""
    name = getattr(f, "name", "") or ""
    return reverse("company_media", args=[name]) if name else ""


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text):
    return mark_safe(_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", str(conditional_escape(text))))


@register.filter
def rich(value):
    """Teks terstruktur → HTML aman. Semua teks di-ESCAPE dulu; yang dikenali hanya: paragraf (baris kosong), '## judul', daftar '- butir', dan **tebal**.
    Tag HTML apa pun dari editor/pelamar tampil sebagai teks biasa."""
    out, items = [], []

    def flush():
        if items: out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>"); items.clear()
    para = []
    def flush_para():
        if para: out.append("<p>" + "<br>".join(str(_inline(p)) for p in para) + "</p>"); para.clear()
    for line in (value or "").replace("\r", "").split("\n"):
        s = line.strip()
        if not s: flush(); flush_para(); continue
        if s.startswith("- ") or s.startswith("* "): flush_para(); items.append(s[2:].strip()); continue
        if s.startswith("## "): flush(); flush_para(); out.append(f"<h3>{_inline(s[3:].strip())}</h3>"); continue
        flush(); para.append(s)
    flush(); flush_para()
    return mark_safe("".join(out))


@register.filter
def wa_link(number):
    n = re.sub(r"\D", "", number or "")
    return f"https://wa.me/{n}" if n else ""


@register.filter
def rp(value):
    try: return "Rp " + f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError): return ""


@register.filter
def stage_label(value, lang="id"):
    from apps.company.careers import STAGE_EN
    from apps.company.models import Stage
    return STAGE_EN.get(value, value) if lang == "en" else dict(Stage.choices).get(value, value)


# ------------------------------------------------------------------ Ikon & motif (putaran 53): semua SVG dibuat di server dari data statis → aman untuk CSP tanpa inline style.
_ICONS = {
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3.2 3 3.2 15 0 18"/><path d="M12 3c-3.2 3-3.2 15 0 18"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5L16 9.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "growth": '<path d="M4 19h16"/><path d="M6 15l4-4 3 3 5-6"/><path d="M15 8h3v3"/>',
    "users": '<circle cx="9" cy="8" r="3"/><path d="M3 19c0-3 3-5 6-5s6 2 6 5"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14c3 0 5 2 5 5"/>',
    "award": '<circle cx="12" cy="9" r="5"/><path d="M8.5 13.5L7 21l5-3 5 3-1.5-7.5"/>',
    "heart": '<path d="M12 20s-8-5-8-11a4.5 4.5 0 0 1 8-2.5A4.5 4.5 0 0 1 20 9c0 6-8 11-8 11z"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "pin": '<path d="M12 21s-7-6-7-11a7 7 0 0 1 14 0c0 5-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "briefcase": '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/><path d="M3 13h18"/>',
    "leaf": '<path d="M5 19c0-8 5-14 15-14 0 10-6 15-14 15"/><path d="M5 19l8-8"/>',
    "star": '<path d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 2-2s-1-1.5-1-2.5 1-1.5 2-1.5h3a3 3 0 0 0 3-3c0-5-4-9-9-9z"/><circle cx="8" cy="11" r="1"/><circle cx="12" cy="7.5" r="1"/><circle cx="16" cy="11" r="1"/>',
    "loom": '<path d="M4 4v16M20 4v16M4 6h16M4 18h16"/><path d="M8 6v12M12 6v12M16 6v12"/>',
}


@register.simple_tag
def icon(name, size=24):
    body = _ICONS.get(name)
    if not body: return ""
    return mark_safe(f'<svg class="ico" width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
                     f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>')


# palet sarung: (dasar, petak/gelap, garis/emas)
_PAL = (("#7a1f2b", "#5a1420", "#e8c66a"), ("#1f3a5f", "#142843", "#e8c66a"), ("#2f5d3a", "#1f4228", "#e8c66a"),
        ("#3b2a0a", "#241804", "#e0b93f"), ("#8a5a14", "#6b4510", "#f3dc8a"), ("#5b2a6b", "#3d1b49", "#ecd77a"))
_PAT = {  # id → isi pola 40×40 (a=dasar, b=gelap, c=emas)
    0: '<rect width="40" height="40" fill="{a}"/><rect width="20" height="20" fill="{b}"/><rect x="20" y="20" width="20" height="20" fill="{b}"/><path d="M0 20h40M20 0v40" stroke="{c}" stroke-width="1.2" opacity=".7"/>',
    1: '<rect width="40" height="40" fill="{a}"/><path d="M20 4l12 16-12 16-12-16z" fill="none" stroke="{c}" stroke-width="2"/><path d="M20 12l5 8-5 8-5-8z" fill="{c}"/><circle cx="0" cy="0" r="2.5" fill="{c}"/><circle cx="40" cy="0" r="2.5" fill="{c}"/><circle cx="0" cy="40" r="2.5" fill="{c}"/><circle cx="40" cy="40" r="2.5" fill="{c}"/>',
    2: '<rect width="40" height="40" fill="{a}"/><rect x="6" width="5" height="40" fill="{b}"/><rect x="24" width="2" height="40" fill="{c}"/><rect x="32" width="5" height="40" fill="{b}"/><path d="M0 20h40" stroke="{c}" stroke-width=".8" opacity=".6"/>',
    3: '<rect width="40" height="40" fill="{a}"/><path d="M0 40L20 6l20 34z" fill="{b}"/><path d="M10 40l10-18 10 18z" fill="{c}" opacity=".85"/>',
}


def _shape_of(obj):
    """Bentuk placeholder menurut kategori produk: celana, baju koko/atasan, peci, surban; selainnya selembar kain sarung."""
    cat = getattr(obj, "category", None)
    if cat is None: return "sarung"
    slug = cat.slug or ""; grp = cat.parent.slug if cat.parent_id else slug
    if "celana" in slug: return "pants"
    if "peci" in slug or "kopiah" in slug or "songkok" in slug: return "peci"
    if "surban" in slug or "serban" in slug: return "surban"
    if grp == "atasan" or any(k in slug for k in ("koko", "kemeja", "baju")): return "koko"
    return "sarung"


# bentuk: (path siluet, hiasan tambahan memakai a/b/c)
_SHAPES = {
    "koko": ("M118 26L96 34 54 62 78 106 102 92V214H218V92L242 106 266 62 224 34 202 26Q160 54 118 26Z",
             '<path d="M118 26Q160 54 202 26" fill="none" stroke="{c}" stroke-width="3"/><path d="M160 46V212" stroke="{c}" stroke-width="2"/>'
             '<circle cx="160" cy="76" r="3" fill="{c}"/><circle cx="160" cy="106" r="3" fill="{c}"/><circle cx="160" cy="136" r="3" fill="{c}"/><circle cx="160" cy="166" r="3" fill="{c}"/>'
             '<path d="M102 92V214M218 92V214" stroke="{b}" stroke-width="1.5" opacity=".5"/>'),
    "pants": ("M108 22H212L226 216H174L160 100 146 216H94Z", '<path d="M108 40H212" stroke="{c}" stroke-width="5"/><path d="M160 100V40" stroke="{b}" stroke-width="2" opacity=".6"/>'),
    "peci": ("M84 156L100 76Q160 56 220 76L236 156Q160 176 84 156Z", '<path d="M86 146Q160 168 234 146" fill="none" stroke="{c}" stroke-width="9"/><path d="M104 84Q160 66 216 84" fill="none" stroke="#fff" stroke-width="3" opacity=".3"/>'),
    "surban": ("M64 158Q58 80 160 62Q262 80 256 158Q160 192 64 158Z",
               '<path d="M70 132Q160 164 250 130M66 106Q160 140 254 104M82 84Q160 114 238 82" fill="none" stroke="{b}" stroke-width="3.5" opacity=".55"/><path d="M64 158Q160 192 256 158" fill="none" stroke="{c}" stroke-width="4"/>'),
}


@register.simple_tag(takes_context=True)
def motif(context, obj, kind=""):
    """Placeholder bergambar (produk/artikel tanpa foto): kain bermotif tenun, atau siluet baju koko / celana / peci / surban menurut kategori. Pola & warna stabil dari nama + kata kunci."""
    name = (getattr(obj, "name", None) or getattr(obj, "title_id", None) or "") if obj is not None else ""
    key = f"{getattr(obj, 'motif', '')} {getattr(obj, 'colors', '')}".lower()
    h = sum(ord(ch) for ch in name) + int(getattr(obj, "pk", 0) or 0)
    pat = 1 if "songket" in key else 2 if "garis" in key or "stripe" in key else 3 if "tumpal" in key else 0 if "kotak" in key or "check" in key else h % 4
    pal = _PAL[h % len(_PAL)]
    for i, w in enumerate(("merah", "biru", "hijau", "cokelat", "emas", "ungu")):
        if w in key: pal = _PAL[i]; break
    a, b, c = pal
    uid = f"m{abs(h) % 100000}{pat}"
    label = conditional_escape(name)
    shape = _shape_of(obj) if kind != "cloth" else "sarung"
    head = (f'<svg class="motif {shape}" viewBox="0 0 320 240" preserveAspectRatio="xMidYMid slice" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg">'
            f'<defs><pattern id="{uid}" width="40" height="40" patternUnits="userSpaceOnUse">{_PAT[pat].format(a=a, b=b, c=c)}</pattern>'
            f'<linearGradient id="{uid}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".28"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".25"/></linearGradient>')
    if shape == "sarung":
        return mark_safe(head + f'</defs><rect width="320" height="240" fill="#f6ecb8"/><rect x="24" y="20" width="272" height="200" rx="6" fill="url(#{uid})"/>'
                         f'<rect x="24" y="20" width="272" height="22" fill="{c}" opacity=".95"/><rect x="24" y="198" width="272" height="22" fill="{c}" opacity=".95"/>'
                         f'<path d="M24 31h272M24 209h272" stroke="{b}" stroke-width="2"/><rect x="24" y="20" width="272" height="200" rx="6" fill="url(#{uid}g)"/></svg>')
    d, extra = _SHAPES[shape]
    return mark_safe(head + f'<clipPath id="{uid}c"><path d="{d}"/></clipPath></defs><rect width="320" height="240" fill="#f6ecb8"/>'
                     f'<ellipse cx="160" cy="226" rx="86" ry="7" fill="#000" opacity=".12"/>'
                     f'<g clip-path="url(#{uid}c)"><rect width="320" height="240" fill="url(#{uid})"/><rect width="320" height="240" fill="url(#{uid}g)"/></g>'
                     f'<path d="{d}" fill="none" stroke="{b}" stroke-width="2.5" stroke-linejoin="round"/>{extra.format(a=a, b=b, c=c)}</svg>')


@register.simple_tag(takes_context=True)
def model_art(context, look):
    """Ilustrasi model memakai setelan (peci, baju koko, sarung) untuk setelan yang belum punya foto asli. Tanpa wajah; warna sarung mengikuti produk pertama dalam setelan."""
    prods = list(look.products.all()) if look is not None and look.pk else []
    ref = next((p for p in prods if _shape_of(p) == "sarung"), prods[0] if prods else None)
    key = f"{getattr(ref, 'motif', '')} {getattr(ref, 'colors', '')}".lower() if ref else ""
    h = sum(ord(ch) for ch in (look.title_id if look else "")) + int(getattr(look, "pk", 0) or 0)
    pal = _PAL[h % len(_PAL)]
    for i, w in enumerate(("merah", "biru", "hijau", "cokelat", "emas", "ungu")):
        if w in key: pal = _PAL[i]; break
    a, b, c = pal
    pat = 1 if "songket" in key else 2 if "garis" in key else 3 if "tumpal" in key else 0
    u = f"ma{abs(h) % 100000}"
    skin, shirt = "#c69a6d", "#f7f2e4"
    label = conditional_escape(look.title_id if look else "")
    return mark_safe(
        f'<svg class="motif model" viewBox="0 0 400 500" preserveAspectRatio="xMidYMax slice" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><pattern id="{u}" width="40" height="40" patternUnits="userSpaceOnUse">{_PAT[pat].format(a=a, b=b, c=c)}</pattern>'
        f'<pattern id="{u}l" width="56" height="56" patternUnits="userSpaceOnUse"><path d="M28 0L56 28 28 56 0 28Z" fill="none" stroke="{c}" stroke-width="1" opacity=".35"/></pattern>'
        f'<linearGradient id="{u}b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fbf3d4"/><stop offset="1" stop-color="#ecd88c"/></linearGradient>'
        f'<linearGradient id="{u}s" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#000" stop-opacity=".22"/><stop offset=".5" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".22"/></linearGradient></defs>'
        f'<rect width="400" height="500" fill="url(#{u}b)"/><rect width="400" height="500" fill="url(#{u}l)"/>'
        f'<ellipse cx="200" cy="474" rx="120" ry="12" fill="#000" opacity=".16"/>'
        # kaki + sarung
        f'<ellipse cx="172" cy="466" rx="20" ry="8" fill="{skin}"/><ellipse cx="228" cy="466" rx="20" ry="8" fill="{skin}"/>'
        f'<path d="M138 292H262L274 458H126Z" fill="url(#{u})"/><path d="M138 292H262L274 458H126Z" fill="url(#{u}s)"/>'
        f'<path d="M127 444H273" stroke="{c}" stroke-width="12"/><path d="M200 300V458" stroke="{b}" stroke-width="2" opacity=".45"/>'
        f'<path d="M136 286H264V310H136Z" fill="{c}"/><path d="M136 298H264" stroke="{b}" stroke-width="2" opacity=".5"/>'
        # baju koko
        f'<path d="M110 232L118 300 144 292 152 206Z" fill="{shirt}" stroke="#d8cfb2" stroke-width="2"/><path d="M290 232L282 300 256 292 248 206Z" fill="{shirt}" stroke="#d8cfb2" stroke-width="2"/>'
        f'<circle cx="124" cy="308" r="11" fill="{skin}"/><circle cx="276" cy="308" r="11" fill="{skin}"/>'
        f'<path d="M144 168Q200 148 256 168L264 304H136Z" fill="{shirt}" stroke="#d8cfb2" stroke-width="2"/>'
        f'<path d="M144 168L112 232 130 244 152 206" fill="{shirt}" stroke="#d8cfb2" stroke-width="2"/><path d="M256 168L288 232 270 244 248 206" fill="{shirt}" stroke="#d8cfb2" stroke-width="2"/>'
        f'<path d="M180 160Q200 182 220 160" fill="none" stroke="{c}" stroke-width="3"/><path d="M200 172V304" stroke="{c}" stroke-width="2"/>'
        f'<circle cx="200" cy="198" r="3" fill="{c}"/><circle cx="200" cy="226" r="3" fill="{c}"/><circle cx="200" cy="254" r="3" fill="{c}"/><circle cx="200" cy="282" r="3" fill="{c}"/>'
        f'<path d="M222 210H248V238H222Z" fill="none" stroke="#d8cfb2" stroke-width="1.5"/>'
        # leher, kepala, peci
        f'<rect x="188" y="144" width="24" height="28" rx="8" fill="{skin}"/><circle cx="200" cy="112" r="34" fill="{skin}"/>'
        f'<path d="M164 100L170 66Q200 54 230 66L236 100Q200 110 164 100Z" fill="#161616"/><path d="M165 96Q200 106 235 96" fill="none" stroke="{c}" stroke-width="5"/></svg>')


@register.simple_tag(takes_context=True)
def gmap(context, address="", city="", url=""):
    """URL peta Google tersemat (iframe) untuk sebuah alamat — tanpa kunci API. Bila `url` sudah berupa URL embed Google, dipakai apa adanya.
    Hanya host google.com yang pernah dihasilkan (PUBLIC_CSP `frame-src` mengizinkan host itu saja)."""
    from urllib.parse import urlencode
    if url and url.startswith("https://www.google.com/maps/embed"): return url
    q = ", ".join(x.strip() for x in (str(address or "").replace("\r", "").replace("\n", ", "), str(city or "")) if x and x.strip())
    if not q: return ""
    return "https://www.google.com/maps?" + urlencode({"q": q[:300], "output": "embed", "hl": context.get("lang", "id")})
