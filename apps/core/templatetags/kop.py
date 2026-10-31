"""{% kop_block "poli" %} — kop surat untuk cetakan HTML; judul mengikuti JENIS cetakan (apps.core.letterhead.heading), sama dengan kop PDF."""
from django import template
from apps.core import letterhead

register = template.Library()


@register.inclusion_tag("core/_kop.html", takes_context=True)
def kop_block(context, kind="company", print_only=False):
    req = context.get("request"); user = getattr(req, "user", None) if req else None
    name, line2, addr = letterhead.heading(kind, letterhead.info(user if getattr(user, "is_authenticated", False) else None))
    return {"kind": kind, "name": name, "line2": line2, "addr": addr, "print_only": print_only}
