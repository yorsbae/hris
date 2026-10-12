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
