"""Komponen UI bersama (putaran 38): langkah alur (`flow_steps`) dan tab halaman (`page_tabs`).
Satu bahasa tampilan di semua halaman: apa langkah berikutnya, mana yang sedang dikerjakan, dan ke mana klik selanjutnya."""
from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()


@register.simple_tag
def flow_steps(*steps, now=0, note=""):
    """`{% flow_steps "Catat barang masuk|stok bertambah|#gerak" "Catat pembelian|stok berkurang" now=2 note="…" %}`.
    Tiap langkah = "Judul|keterangan|tautan" (keterangan & tautan opsional). `now` (mulai 1) = langkah yang sedang dikerjakan: sebelumnya ditandai selesai.
    Pembungkus tetap `class="hint flow"` agar gaya & tes lama berlaku."""
    items = []
    for i, raw in enumerate(steps, 1):
        parts = (str(raw).split("|") + ["", ""])[:3]
        title, desc, url = parts
        state = "now" if i == now else ("done" if now and i < now else "")
        head = format_html('<a href="{}"><b>{}</b></a>', url, title) if url else format_html("<b>{}</b>", title)
        items.append((state, i, head, desc))
    lis = format_html_join("", '<li class="{}"><i>{}</i><span>{}{}</span></li>', ((s, i, h, format_html("<small>{}</small>", d) if d else "") for s, i, h, d in items))
    tail = format_html('<p class="flownote">{}</p>', note) if note else ""
    return format_html('<div class="hint flow"><ol class="flowsteps">{}</ol>{}</div>', lis, tail)


@register.simple_tag
def page_tabs(active, *tabs):
    """`{% page_tabs tab "stok|Stok|?tab=stok|3" … %}` → baris tab. Tiap tab = "kunci|label|tautan|lencana" (lencana opsional, mis. jumlah)."""
    out = []
    for raw in tabs:
        key, label, url, badge = (str(raw).split("|") + [""])[:4]
        b = format_html(' <span class="tabn">{}</span>', badge) if badge else ""
        out.append((" on" if key == active else "", url, label, b, "page" if key == active else "false"))
    return format_html('<nav class="tabs" aria-label="Bagian halaman">{}</nav>', format_html_join("", '<a class="t{}" href="{}" aria-current="{}">{}{}</a>', ((c, u, a, l, b) for c, u, l, b, a in out)))
