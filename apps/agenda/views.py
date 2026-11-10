"""Halaman Kalender (putaran 66): bulan (Default) + Heat map, mini-kalender, filter lapisan, detail hari, ekspor .ics, agenda rapat/event."""
import calendar as pycal
from datetime import date, datetime, timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import DEPT_ACTORS
from apps.hr.models import Department
from . import feed
from .models import AgendaEvent

MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
MAXPILL = 3


def _month(s):
    try: y, m = (int(x) for x in s.split("-")); return date(y, m, 1)
    except (ValueError, AttributeError): return date.today().replace(day=1)


def _shift(d, n):
    i = d.year * 12 + d.month - 1 + n; return date(i // 12, i % 12 + 1, 1)


def _weeks(first):
    cal = pycal.Calendar(firstweekday=0)
    ds = list(cal.itermonthdates(first.year, first.month))
    return [ds[i:i + 7] for i in range(0, len(ds), 7)]


@login_required
def index(request):
    u = request.user; first = _month(request.GET.get("m", ""))
    allowed = feed.allowed_layers(u)
    picked = [k for k in request.GET.getlist("l") if k in allowed] if request.GET.get("go") else list(allowed)
    dept = request.GET.get("dept", "").isdigit() and int(request.GET["dept"]) or None
    mode = "heat" if request.GET.get("v") == "heat" else "default"
    weeks = _weeks(first); lo, hi = weeks[0][0], weeks[-1][-1]
    entries = feed.build(u, lo, hi, picked, dept)
    heat = feed.heat(entries) if mode == "heat" else {}
    peak = max(heat.values(), default=0)
    per = {}
    for e in entries: per.setdefault(e["date"], []).append(e)
    today = date.today()
    try: sel = date.fromisoformat(request.GET.get("day", ""))
    except ValueError: sel = today if today.month == first.month and today.year == first.year else first
    rows = []
    for w in weeks:
        cells = []
        for d in w:
            es = sorted(per.get(d, []), key=lambda x: (x["layer"] != "libur", x["layer"], x["title"]))
            h = heat.get(d, 0)
            cells.append({"d": d, "in": d.month == first.month, "today": d == today, "sel": d == sel, "es": es[:MAXPILL], "more": max(0, len(es) - MAXPILL), "n": len(es),
                          "heat": h, "lvl": 1 + min(3, (h - 1) * 4 // max(peak, 1)) if h else 0})
        rows.append({"wk": w[0].isocalendar()[1], "cells": cells})
    mini = [{"wk": w[0].isocalendar()[1], "cells": [{"d": d, "in": d.month == first.month, "today": d == today, "sel": d == sel, "has": d in per} for d in w]} for w in weeks]
    qs = "".join(f"&l={k}" for k in picked) + ("&go=1" if request.GET.get("go") else "") + (f"&dept={dept}" if dept else "") + ("&v=heat" if mode == "heat" else "")
    day_entries = sorted(per.get(sel, []), key=lambda x: (x["layer"], x["title"]))
    ctx = {"first": first, "label": f"{MONTHS[first.month - 1]} {first.year}", "rows": rows, "mini": mini, "mode": mode, "picked": picked, "dept": dept, "qs": qs, "sel": sel, "day_entries": day_entries,
           "layers": [(k, l, c, k in picked) for k, l, c in feed.LAYERS if k in allowed], "prev": _shift(first, -1), "next": _shift(first, 1), "peak": peak,
           "months": [(i + 1, n) for i, n in enumerate(MONTHS)], "years": range(today.year - 2, today.year + 3), "depts": Department.objects.order_by("name") if feed.can_filter_dept(u) else [],
           "names": feed.can_see_names(u), "counts": {k: sum(1 for e in entries if e["layer"] == k) for k in allowed}}
    return render(request, "agenda/calendar.html", ctx)


@login_required
def export_ics(request):
    u = request.user; first = _month(request.GET.get("m", "")); lo, hi = first, _shift(first, 1) - timedelta(days=1)
    es = feed.build(u, lo, hi, [k for k in request.GET.getlist("l") if k in feed.allowed_layers(u)] or None)
    esc = lambda s: str(s).replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\n", "\\n")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HRIS//Kalender//ID", "CALSCALE:GREGORIAN"]
    for i, e in enumerate(es):
        d = e["date"]; lines += ["BEGIN:VEVENT", f"UID:{d:%Y%m%d}-{i}-{abs(hash(e['title'])) % 10**8}@hris", f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}", f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                                  f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}", f"SUMMARY:{esc(e['title'])}", f"CATEGORIES:{esc(feed.LABEL[e['layer']])}"] + ([f"LOCATION:{esc(e['sub'])}"] if e["sub"] else []) + ["END:VEVENT"]
    lines.append("END:VCALENDAR"); log(request, "agenda", "ics_export", None, None, {"month": f"{first:%Y-%m}", "n": len(es)})
    r = HttpResponse("\r\n".join(lines) + "\r\n", content_type="text/calendar; charset=utf-8"); r["Content-Disposition"] = f'attachment; filename="kalender-{first:%Y-%m}.ics"'; return r


class EventForm(forms.ModelForm):
    class Meta:
        model = AgendaEvent; fields = ["title", "kind", "start_date", "end_date", "start_time", "end_time", "location", "audience", "department", "audience_role", "description"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), "description": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *a, user=None, **k):
        super().__init__(*a, **k); self.user = user
        if user.role not in (Role.SUPERADMIN, Role.HRD):   # pembuat non-HRD: hanya untuk departemen/peran sendiri
            for f in ("audience", "department", "audience_role"): self.fields.pop(f)
    def clean(self):
        c = super().clean(); a, b = c.get("start_date"), c.get("end_date")
        if a and b and b < a: self.add_error("end_date", "Tanggal selesai sebelum tanggal mulai.")
        st, et = c.get("start_time"), c.get("end_time")
        if st and et and (not b or b == a) and et <= st: self.add_error("end_time", "Jam selesai harus setelah jam mulai.")
        if "audience" in c:
            if c["audience"] == "dept" and not c.get("department"): self.add_error("department", "Pilih departemen.")
            if c["audience"] == "role" and c.get("audience_role") not in dict(Role.choices): self.add_error("audience_role", "Isi peran akun yang valid (mis. hrd, dept_admin, it).")
        return c
    def save(self, commit=True):
        o = super().save(commit=False); u = self.user
        if "audience" not in self.fields:
            if u.role in DEPT_ACTORS and u.department_id: o.audience, o.department_id, o.audience_role = "dept", u.department_id, ""
            else: o.audience, o.department, o.audience_role = "role", None, u.role
        elif o.audience != "dept": o.department = None
        if o.audience != "role": o.audience_role = ""
        if commit: o.save()
        return o


def _can_edit(u, e): return u.role in (Role.SUPERADMIN, Role.HRD) or e.created_by_id == u.pk


@login_required
def event_edit(request, pk=None):
    e = get_object_or_404(feed.visible_events(request.user, AgendaEvent.objects.all()), pk=pk) if pk else None
    if e and not _can_edit(request.user, e): return HttpResponse("Tidak berwenang mengubah agenda ini.", status=403)
    form = EventForm(request.POST or None, instance=e, user=request.user, initial={"start_date": request.GET.get("day")} if not e and request.GET.get("day") else None)
    if request.method == "POST" and form.is_valid():
        o = form.save(commit=False)
        if not e: o.created_by = request.user
        o.save(); log(request, "agenda", "event_save", o, None, {"title": o.title, "kind": o.kind, "start": str(o.start_date)})
        messages.success(request, "Agenda disimpan."); return redirect(f"/calendar/?m={o.start_date:%Y-%m}&day={o.start_date}")
    return render(request, "agenda/event_form.html", {"form": form, "e": e, "title": "Ubah agenda" if e else "Agenda baru"})


@login_required
def event_detail(request, pk):
    e = get_object_or_404(feed.visible_events(request.user, AgendaEvent.objects.select_related("department", "created_by")), pk=pk)
    return render(request, "agenda/event_detail.html", {"e": e, "can_edit": _can_edit(request.user, e)})


@login_required
def event_delete(request, pk):
    if request.method != "POST": return HttpResponseNotAllowed(["POST"])
    e = get_object_or_404(feed.visible_events(request.user, AgendaEvent.objects.all()), pk=pk)
    if not _can_edit(request.user, e): return HttpResponse("Tidak berwenang menghapus agenda ini.", status=403)
    log(request, "agenda", "event_delete", e, {"title": e.title}, None); e.delete(); messages.success(request, "Agenda dihapus."); return redirect("/calendar/")
