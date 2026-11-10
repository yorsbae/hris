"""Menu IT (putaran 66). Akses: akun IT + Superadmin. Semua perubahan tercatat di Audit Log; kunci lisensi terenkripsi & tidak tampil di daftar."""
from datetime import date, timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Count, F
from django.http import HttpResponseNotAllowed
from django.shortcuts import render, redirect, get_object_or_404
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from . import models as m

IT = (Role.IT,)


def make_form(model, fields, label=None):
    def cb(f, **kw):
        if f.__class__.__name__ == "DateField": kw["widget"] = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        return f.formfield(**kw)
    return forms.modelform_factory(model, fields=fields, formfield_callback=cb)


def pill(t, cls=""): return {"t": t, "p": {"ok": "approved", "wn": "pending", "er": "rejected"}.get(cls, "")}
FLAG = {"lewat": ("kedaluwarsa", "er"), "segera": ("≤ 30 hari", "wn")}
def flagcell(d, flag): return pill(f"{d:%d-%m-%Y}" + (f" · {FLAG[flag][0]}" if flag else ""), FLAG[flag][1] if flag else "") if d else pill("—")


class Spec:
    """Satu halaman daftar + form tambah/ubah generik."""
    def __init__(self, key, model, title, fields, head, row, search=(), filters=(), order=None, noun="data", ro=False):
        self.ro = ro; self.ubah = False; self.on_save = None
        self.key, self.model, self.title, self.head, self.row, self.search, self.filters, self.noun = key, model, title, head, row, search, filters, noun
        self.form = make_form(model, fields); self.order = order

    def qs(self, request):
        qs = self.model.objects.all()
        q = request.GET.get("q", "").strip()
        if q and self.search:
            cond = Q()
            for s in self.search: cond |= Q(**{f"{s}__icontains": q})
            qs = qs.filter(cond)
        for f, _ in self.filters:
            v = request.GET.get(f, "")
            if v: qs = qs.filter(**{f: v})
        return qs.order_by(*self.order) if self.order else qs

    def list(self, request, extra=None, ctx=None):
        qs = self.qs(request)
        if extra: qs = extra(qs)
        if request.GET.get("export"):
            log(request, "it", f"{self.key}_export", None, None, {"rows": qs.count()})
            return tabular.export_response(f"it-{self.key.replace('/', '-')}", [h for h in self.head], [[c["t"] for c in self.row(o)] for o in qs], request, sheet=self.title)
        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        flt = [(f, [("", "Semua")] + list(ch), request.GET.get(f, "")) for f, ch in self.filters]
        return render(request, "it/list.html", {"s": self, "page": page, "rows": [(o, self.row(o)) for o in page], "q": request.GET.get("q", ""), "filters": flt, "total": qs.count(), **(ctx or {})})

    def edit(self, request, pk=None):
        obj = get_object_or_404(self.model, pk=pk) if pk else None
        before = {k: str(v) for k, v in vars(obj).items() if not k.startswith("_") and k != "key"} if obj else None
        form = self.form(request.POST or None, instance=obj, initial=None if obj else {k: v for k, v in request.GET.items() if k in self.form.base_fields})
        if request.method == "POST" and form.is_valid():
            o = form.save()
            log(request, "it", f"{self.key}_{'update' if pk else 'create'}", o, before, {"str": str(o)})
            if self.on_save: self.on_save(request, o, before)
            messages.success(request, f"{self.noun.capitalize()} disimpan."); return redirect(f"/it/{self.key}/")
        return render(request, "it/form.html", {"form": form, "title": ("Ubah " if pk else "Tambah ") + self.noun, "back": f"/it/{self.key}/", "obj": obj, "delete": bool(pk)})

    def delete(self, request, pk):
        if request.method != "POST": return HttpResponseNotAllowed(["POST"])
        o = get_object_or_404(self.model, pk=pk); log(request, "it", f"{self.key}_delete", o, {"str": str(o)}, None); o.delete()
        messages.success(request, f"{self.noun.capitalize()} dihapus."); return redirect(f"/it/{self.key}/")


ASSETS = Spec("assets", m.Asset, "Aset IT", ["tag", "name", "category", "brand", "serial", "status", "department", "assignee", "location", "purchase_date", "warranty_until", "price", "note"],
              ["Kode", "Nama", "Kategori", "Status", "Pemegang", "Departemen", "Garansi"],
              lambda a: [pill(a.tag), pill(a.name + (f" — {a.brand}" if a.brand else "")), pill(a.get_category_display()), pill(a.get_status_display(), "ok" if a.status == "dipakai" else "wn" if a.status == "perbaikan" else ""),
                         pill(a.assignee or "—"), pill(a.department.name if a.department else "—"), flagcell(a.warranty_until, a.warranty_flag)],
              search=("tag", "name", "serial", "assignee", "brand"), filters=(("category", m.Asset.CATS), ("status", m.Asset.STATUS)), order=("tag",), noun="aset")
LICENSES = Spec("licenses", m.License, "Lisensi Software", ["name", "vendor", "seats", "used", "expires", "key", "cost", "note"],
                ["Software", "Vendor", "Seat", "Berlaku s/d"],
                lambda l: [pill(l.name), pill(l.vendor or "—"), pill(f"{l.used}/{l.seats}", "er" if l.used >= l.seats else ""), flagcell(l.expires, l.flag)],
                search=("name", "vendor"), order=("name",), noun="lisensi")
DOMAINS = Spec("domains", m.Domain, "Domain & SSL", ["name", "kind", "provider", "expires", "auto_renew", "note"],
               ["Nama", "Jenis", "Penyedia", "Berlaku s/d", "Otomatis"],
               lambda d: [pill(d.name), pill(d.get_kind_display()), pill(d.provider or "—"), flagcell(d.expires, d.flag), pill("ya" if d.auto_renew else "tidak")],
               search=("name", "provider"), filters=(("kind", m.Domain.KINDS),), order=("expires",), noun="domain/SSL")
TICKETS = Spec("tickets", m.Ticket, "Insiden & Tiket IT", ["title", "category", "detail", "requester", "department", "location", "contact", "priority", "status", "asset", "due"],
               ["Judul", "Pelapor", "Departemen", "Prioritas", "Status", "Target"],
               lambda t: [pill(t.title), pill(t.requester or "—"), pill(t.department.name if t.department else "—"), pill(t.get_priority_display(), "er" if t.priority in ("tinggi", "kritis") else ""), pill(t.get_status_display(), "ok" if t.status == "selesai" else "wn" if t.status == "proses" else ""),
                          pill(f"{t.due:%d-%m-%Y}" if t.due else "—", "er" if t.due and t.due < date.today() and t.status != "selesai" else "")],
               search=("title", "requester", "detail"), filters=(("status", m.Ticket.STATUS), ("priority", m.Ticket.PRIO)), order=("status", "-id"), noun="tiket")
ASSETS.ubah = True
def _ticket_saved(request, t, before):
    """Tiket dari departemen: beri tahu admin departemen saat status berubah."""
    if before and t.created_by_id and before.get("status") != t.status and t.department_id:
        from apps.core import notify
        notify.send((Role.DEPT_ADMIN, Role.POLI, Role.HRD), "it", f"Bantuan IT “{t.title[:80]}” → {t.get_status_display()}", "/it/wishlist/ajukan/", department_id=t.department_id)


TICKETS.on_save = _ticket_saved
SPECS = {s.key: s for s in (ASSETS, LICENSES, DOMAINS, TICKETS)}


@login_required
@require_roles(*IT)
def home(request):
    t = date.today(); lim = t + timedelta(days=30)
    exp = []
    for a in m.Asset.objects.exclude(status="dihapus").filter(warranty_until__lte=lim, warranty_until__isnull=False): exp.append((a.warranty_until, "Garansi aset", str(a), "/it/assets/%d/" % a.pk))
    for l in m.License.objects.filter(expires__lte=lim): exp.append((l.expires, "Lisensi", l.name, "/it/licenses/%d/" % l.pk))
    for d in m.Domain.objects.filter(expires__lte=lim): exp.append((d.expires, d.get_kind_display(), d.name, "/it/domains/%d/" % d.pk))
    exp.sort(key=lambda x: x[0])
    subs = [(s, s.ips.count(), s.capacity) for s in m.Subnet.objects.all()]
    by = dict(m.Asset.objects.values_list("status").annotate(n=Count("id")))
    ctx = {"expiring": [(d, k, n, u, "er" if d < t else "wn") for d, k, n, u in exp[:12]], "n_exp": len(exp),
           "assets": {"total": sum(by.values()), **{k: by.get(k, 0) for k, _ in m.Asset.STATUS}},
           "tickets_open": m.Ticket.objects.exclude(status="selesai").count(), "tickets_late": m.Ticket.objects.exclude(status="selesai").filter(due__lt=t).count(),
           "tickets": m.Ticket.objects.exclude(status="selesai").order_by("-priority", "-id")[:6],
           "subnets": [(s, u, c, min(100, round(u * 100 / c)) if c else 0) for s, u, c in subs],
           "low_stock": sum(1 for i in m.StockItem.objects.all() if i.low), "wish_open": m.Wish.objects.filter(status__in=("diajukan", "disetujui", "dipesan")).count(), "net_down": m.NetDevice.objects.filter(status="mati").count(), "seats_full": m.License.objects.filter(used__gte=F("seats")).count()}
    return render(request, "it/home.html", ctx)


def _spec(key):
    s = SPECS[key]
    @login_required
    @require_roles(*IT)
    def lst(request): return s.list(request)
    @login_required
    @require_roles(*IT)
    def edit(request, pk=None): return s.edit(request, pk)
    @login_required
    @require_roles(*IT)
    def delete(request, pk): return s.delete(request, pk)
    return lst, edit, delete


@login_required
@require_roles(*IT)
def asset_detail(request, pk):
    a = get_object_or_404(m.Asset.objects.select_related("department"), pk=pk)
    if request.method == "POST":
        new, note = request.POST.get("status"), request.POST.get("note", "").strip()[:300]
        if new in dict(m.Asset.STATUS):
            old = a.status; a.status = new
            if new == "stok": a.assignee = ""
            a.save(); m.AssetEvent.objects.create(asset=a, action="status", detail=f"{old} → {new}" + (f" · {note}" if note else ""), by=request.user)
            log(request, "it", "asset_status", a, {"status": old}, {"status": new}); messages.success(request, "Status aset diperbarui.")
        return redirect(request.path)
    return render(request, "it/asset_detail.html", {"a": a, "events": a.events.select_related("by")[:30], "ips": a.ips.select_related("subnet"), "tickets": a.tickets.all()[:10], "statuses": m.Asset.STATUS})


@login_required
@require_roles(*IT)
def ip_home(request):
    subs = [(s, s.ips.count(), s.capacity) for s in m.Subnet.objects.all()]
    return render(request, "it/ip.html", {"subnets": [(s, u, c, min(100, round(u * 100 / c)) if c else 0, s.next_free()) for s, u, c in subs],
                                          "conflicts": m.IPAddress.objects.values("mac").annotate(n=Count("id")).filter(n__gt=1, mac__gt="").count()})


SubnetForm = make_form(m.Subnet, ["name", "cidr", "vlan", "gateway", "note"])
IPForm = make_form(m.IPAddress, ["address", "hostname", "mac", "kind", "asset", "note"])


@login_required
@require_roles(*IT)
def subnet_edit(request, pk=None):
    obj = get_object_or_404(m.Subnet, pk=pk) if pk else None
    form = SubnetForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        o = form.save(); log(request, "it", "subnet_save", o, None, {"cidr": o.cidr}); messages.success(request, "Subnet disimpan."); return redirect(f"/it/ip/{o.pk}/")
    return render(request, "it/form.html", {"form": form, "title": "Ubah subnet" if pk else "Tambah subnet", "back": "/it/ip/", "obj": obj, "delete": False})


@login_required
@require_roles(*IT)
def subnet_detail(request, pk):
    s = get_object_or_404(m.Subnet, pk=pk)
    init = {"address": s.next_free()}
    form = IPForm(request.POST or None, initial=init)
    if request.method == "POST":
        if request.POST.get("del"):
            ip = get_object_or_404(m.IPAddress, pk=request.POST["del"], subnet=s); log(request, "it", "ip_delete", ip, {"address": ip.address}, None); ip.delete(); return redirect(request.path)
        form.instance.subnet = s
        if form.is_valid():
            o = form.save(commit=False); o.subnet = s; o.save(); log(request, "it", "ip_create", o, None, {"address": o.address}); messages.success(request, f"{o.address} dicatat."); return redirect(request.path)
    ips = sorted(s.ips.select_related("asset"), key=lambda i: __import__("ipaddress").ip_address(i.address))
    used, cap = len(ips), s.capacity
    return render(request, "it/subnet.html", {"s": s, "ips": ips, "form": form, "used": used, "cap": cap, "pct": min(100, round(used * 100 / cap)) if cap else 0, "next": s.next_free()})
