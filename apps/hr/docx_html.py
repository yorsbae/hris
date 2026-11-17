"""Render .docx hasil `contract_print.build` menjadi HTML siap cetak dari browser (tanpa Word / unduhan).
Hanya subset yang dipakai template kontrak: paragraf (rata, indent, spasi, tebal/miring/garis bawah/ukuran), tab, baris baru, pindah halaman,
tabel (lebar kolom, tinggi baris, rata vertikal, gridSpan, garis), gambar sebaris. Selebihnya diabaikan, bukan error."""
import base64
from html import escape
from io import BytesIO
from docx import Document
from docx.oxml.ns import qn

PT = 1 / 20          # twip → pt
_JC = {"left": "left", "start": "left", "center": "center", "right": "right", "end": "right", "both": "justify", "distribute": "justify"}


def _val(el, tag, attr="w:val"):
    if el is None: return None
    c = el.find(qn(tag))
    return None if c is None else c.get(qn(attr))


def _on(rpr, tag):   # <w:b/> aktif, <w:b w:val="0"/> mati, tidak ada = None (warisi)
    if rpr is None: return None
    c = rpr.find(qn(tag))
    if c is None: return None
    return c.get(qn("w:val")) not in ("0", "false", "off")


def _style(d):
    return ";".join(f"{k}:{v}" for k, v in d.items() if v not in (None, ""))


class _Ctx:
    def __init__(self, doc):
        self.doc = doc; n = doc.styles["Normal"]
        import re
        self.font = re.sub(r"[^\w .-]", "", n.font.name or "") or "Times New Roman"; self.size = n.font.size.pt if n.font.size else 11
        dd = doc.styles.element.find(qn("w:docDefaults"))
        sp = dd.find(".//" + qn("w:spacing")) if dd is not None else None
        self.after = int(sp.get(qn("w:after"), 0)) * PT if sp is not None else 0
        self.line = int(sp.get(qn("w:line"), 240)) / 240 if sp is not None else 1.0

    def image(self, drawing):
        blip = drawing.find(".//" + qn("a:blip")); ext = drawing.find(".//" + qn("wp:extent"))
        if blip is None or ext is None: return ""
        try: part = self.doc.part.related_parts[blip.get(qn("r:embed"))]
        except KeyError: return ""
        w, h = int(ext.get("cx")) / 12700, int(ext.get("cy")) / 12700
        return f'<img src="data:{part.content_type};base64,{base64.b64encode(part.blob).decode()}" style="width:{w:.1f}pt;height:{h:.1f}pt;vertical-align:bottom" alt="">'


def _runs(p, cx, hang_pt=0):
    """hang_pt > 0 (paragraf menggantung): semua yang ada sebelum tab pertama (nomor "1.") dibungkus kotak selebar gantungan,
    sehingga teks mulai tepat di indent kiri dan baris lanjutan sejajar dengan baris pertama."""
    out = []; hang = [hang_pt]
    for r in p.iter(qn("w:r")):
        rpr = r.find(qn("w:rPr")); st = {}
        if _on(rpr, "w:b"): st["font-weight"] = "bold"
        if _on(rpr, "w:i"): st["font-style"] = "italic"
        u = _val(rpr, "w:u")
        if u and u != "none": st["text-decoration"] = "underline"
        sz = _val(rpr, "w:sz")
        if sz: st["font-size"] = f"{int(sz) / 2:g}pt"
        parts = []
        def flush():
            if parts: out.append(f'<span style="{_style(st)}">{"".join(parts)}</span>' if st else "".join(parts))
            parts.clear()
        for c in r:
            if c.tag == qn("w:t"): parts.append(escape(c.text or ""))
            elif c.tag == qn("w:tab"):
                if hang[0]:
                    flush(); out[:] = [f'<span style="display:inline-block;width:{hang[0]:g}pt;text-indent:0">{"".join(out)}</span>']; hang[0] = 0
                else: parts.append('<span class="tab"></span>')
            elif c.tag == qn("w:br") and c.get(qn("w:type")) != "page": parts.append("<br>")
            elif c.tag == qn("w:drawing"): parts.append(cx.image(c))
        flush()
    return "".join(out)


def _has_page_break(p):
    return any(b.get(qn("w:type")) == "page" for b in p.iter(qn("w:br")))


def _para(p, cx, in_cell=False):
    ppr = p.find(qn("w:pPr")); st = {}
    jc = _val(ppr, "w:jc")
    if jc in _JC: st["text-align"] = _JC[jc]
    sp = ppr.find(qn("w:spacing")) if ppr is not None else None
    g = (lambda k: sp.get(qn("w:" + k)) if sp is not None else None)
    st["margin"] = f"{int(g('before') or 0) * PT:g}pt 0 {int(g('after')) * PT if g('after') is not None else cx.after:g}pt 0"
    ln = g("line")
    if ln and g("lineRule") in (None, "auto"): st["line-height"] = f"{int(ln) / 240:.3f}"
    elif ln: st["line-height"] = f"{int(ln) * PT:g}pt"
    else: st["line-height"] = f"{cx.line:.3f}"
    ind = ppr.find(qn("w:ind")) if ppr is not None else None
    if ind is not None:
        left, first, hang = (int(ind.get(qn("w:" + k), 0) or 0) for k in ("left", "firstLine", "hanging"))
        st["padding-left"] = f"{left * PT:g}pt"
        if first: st["text-indent"] = f"{first * PT:g}pt"
        if hang: st["text-indent"] = f"-{hang * PT:g}pt"
    else: hang = 0
    html = _runs(p, cx, hang * PT if hang else 0)
    return f'<p style="{_style(st)}">{html or "&nbsp;"}</p>'


def _borders(el):
    if el is None: return {}
    b = el.find(qn("w:tblBorders")) if el.tag == qn("w:tblPr") else el.find(qn("w:tcBorders"))
    if b is None: return {}
    out = {}
    for side in ("top", "left", "bottom", "right"):
        s = b.find(qn("w:" + side))
        if s is not None: out[side] = s.get(qn("w:val")) not in ("nil", "none")
    for side in ("insideH", "insideV"):
        s = b.find(qn("w:" + side))
        if s is not None: out[side] = s.get(qn("w:val")) not in ("nil", "none")
    return out


def _table(t, cx):
    pr = t.find(qn("w:tblPr")); grid = [int(g.get(qn("w:w"))) * PT for g in t.find(qn("w:tblGrid"))]
    tb = _borders(pr); cells_css = []
    if tb.get("insideH") or tb.get("insideV") or any(tb.get(k) for k in ("top", "left", "bottom", "right")): cells_css.append("border:.5pt solid #000")
    cols = "".join(f'<col style="width:{w:g}pt">' for w in grid)
    rows = []
    for tr in t.iterchildren(qn("w:tr")):
        trpr = tr.find(qn("w:trPr")); h = trpr.find(qn("w:trHeight")) if trpr is not None else None
        hs = f"height:{int(h.get(qn('w:val'))) * PT:g}pt" if h is not None else ""
        tds = []
        for tc in tr.iterchildren(qn("w:tc")):
            tcpr = tc.find(qn("w:tcPr")); span = _val(tcpr, "w:gridSpan"); va = _val(tcpr, "w:vAlign")
            st = list(cells_css) + ([f"vertical-align:{'middle' if va == 'center' else va}"] if va in ("top", "center", "bottom") else [])
            cb = _borders(tcpr)
            for side, on in cb.items():
                if side in ("top", "left", "bottom", "right"): st.append(f"border-{side}:{'.5pt solid #000' if on else 'none'}")
            inner = "".join(_para(p, cx, True) for p in tc.iterchildren(qn("w:p")))
            tds.append(f'<td{f" colspan={span}" if span else ""} style="{";".join(st)}">{inner}</td>')
        rows.append(f'<tr style="{hs}">{"".join(tds)}</tr>')
    return f'<table class="dt"><colgroup>{cols}</colgroup>{"".join(rows)}</table>'


def render(data):
    """.docx (bytes) → (css_halaman, [html_lembar, ...]). Lembar baru di tiap pindah halaman / pageBreakBefore."""
    doc = Document(BytesIO(data)); cx = _Ctx(doc); body = doc.element.body
    sect = body.find(qn("w:sectPr")); pg = sect.find(qn("w:pgSz")); mar = sect.find(qn("w:pgMar"))
    w, h = int(pg.get(qn("w:w"))) * PT, int(pg.get(qn("w:h"))) * PT
    m = {k: int(mar.get(qn("w:" + k))) * PT for k in ("top", "right", "bottom", "left")}
    sheets, cur = [], []
    def flush():
        nonlocal cur
        if cur: sheets.append("".join(cur)); cur = []
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            ppr = el.find(qn("w:pPr")); pbb = ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None
            if pbb: flush()
            if _has_page_break(el):   # pemisah halaman: isi sebelum <w:br> tetap di lembar ini, sisanya (jarang) di lembar berikut
                html = _runs(el, cx)
                if html.strip(): cur.append(_para(el, cx))
                flush()
            else: cur.append(_para(el, cx))
        elif el.tag == qn("w:tbl"): cur.append(_table(el, cx))
    flush()
    sheets = [s for s in sheets if "<img" in s or any(ch not in " \n" for ch in _text_only(s))]   # buang lembar kosong (paragraf penutup 1pt)
    css = (f"@page{{size:{w:g}pt {h:g}pt;margin:0}}"
           f".sheet{{box-sizing:border-box;width:{w:g}pt;min-height:{h:g}pt;padding:{m['top']:g}pt {m['right']:g}pt {m['bottom']:g}pt {m['left']:g}pt;"
           f"font-family:'{cx.font}','Times New Roman',serif;font-size:{cx.size:g}pt;color:#000;background:#fff}}"
           ".sheet p{margin:0;overflow-wrap:anywhere}.sheet table.dt{border-collapse:collapse;table-layout:fixed;width:100%}.sheet td{padding:0 5.4pt;vertical-align:top}"
           ".sheet .tab{display:inline-block;width:1.25cm}")
    return css, sheets


def _text_only(html):
    import re
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", "")
