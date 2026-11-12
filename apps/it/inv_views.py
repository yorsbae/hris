"""Inventori stok IT, wishlist/order dari departemen, dan perangkat jaringan (putaran 67)."""
from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from apps.core import notify
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import DEPT_ACTORS, require_roles
from apps.hr.models import Department
from . import models as m
from .views import Spec, pill, IT, make_form, tell_requester

# ---------- perangkat jaringan (submenu: router, wifi, fingerprint, cctv, lain)
NET = Spec("net", m.NetDevice, "Perangkat jaringan", ["name", "kind", "brand", "serial", "ip", "location", "status", "firmware", "last_checked", "asset", "note"],
           ["Nama", "Jenis", "IP", "Lokasi", "Status", "Dicek"],
           lambda d: [pill(d.name + (f" — {d.brand}" if d.brand else "")), pill(d.get_kind_display()), pill(d.ip.address if d.ip else "—"), pill(d.location or "—"),
                      pill(d.get_status_display(), "ok" if d.status == "aktif" else "er" if d.status == "mati" else "wn" if d.status == "perbaikan" else ""), pill(f"{d.last_checked:%d-%m-%Y}" if d.last_checked else "—")],
           search=("name", "brand", "location", "serial", "ip__address"), filters=(("status", m.NetDevice.STATUS),), order=("kind", "name"), noun="perangkat")


@login_required
@require_roles(*IT)
def net_list(request, group=None):
    if group and group not in m.NetDevice.GROUPS: group = None
    ex = (lambda qs: qs.select_related("ip").filter(kind__in=m.NetDevice.GROUPS[group][1])) if group else (lambda qs: qs.select_related("ip"))
    counts = {g: m.NetDevice.objects.filter(kind__in=ks).count() for g, (_, ks) in m.NetDevice.GROUPS.items()}
    subtabs = [("", "Semua", sum(counts.values()))] + [(g, t, counts[g]) for g, (t, _) in m.NetDevice.GROUPS.items()]
    title = m.NetDevice.GROUPS[group][0] if group else "Perangkat jaringan"
    kind0 = m.NetDevice.GROUPS[group][1][0] if group else "router"
    return NET.list(request, extra=ex, ctx={"subtabs": subtabs, "group": group or "", "page_title": title, "new_url": f"/it/net/new/?kind={kind0}", "tab": "ip"})


@login_required
@require_roles(*IT)
def net_edit(request, pk=None): return NET.edit(request, pk)


@login_required
@require_roles(*IT)
def net_delete(request, pk): return NET.delete(request, pk)


# ---------- inventori stok
STOCK = Spec("stock", m.StockItem, "Inventori Stok IT", ["sku", "name", "category", "unit", "min_stock", "location", "note"],
             ["Kode", "Barang", "Kategori", "Stok", "Minimum", "Lokasi"],
             lambda i: [pill(i.sku), pill(i.name), pill(i.get_category_display()), pill(f"{i.qty} {i.unit}", "er" if i.qty == 0 else "wn" if i.low else "ok"), pill(str(i.min_stock)), pill(i.location or "—")],
             search=("sku", "name", "location"), filters=(("category", m.StockItem.CATS),), order=("name",), noun="barang")
STOCK.ubah = True
MOVES = Spec("stock/moves", m.StockMovement, "Barang Masuk & Keluar", ["item"],
             ["Tanggal", "Barang", "Arah", "Jumlah", "Pemasok / penerima", "Departemen", "Keperluan"],
             lambda v: [pill(f"{v.date:%d-%m-%Y}"), pill(v.item.name), pill(v.get_direction_display(), "ok" if v.direction == "masuk" else "wn"), pill(f"{v.qty} {v.item.unit}"), pill(v.party),
                        pill(v.department.name if v.department else "—"), pill(v.note or "—")],
             search=("item__name", "item__sku", "party", "note"), filters=(("direction", m.StockMovement.DIR),), order=("-date", "-id"), noun="mutasi", ro=True)


@login_required
@require_roles(*IT)
def stock_list(request):
    ex = (lambda qs: qs.filter(qty__lte=__import__("django.db.models", fromlist=["F"]).F("min_stock"))) if request.GET.get("low") else None
    return STOCK.list(request, extra=ex, ctx={"tab": "stock", "low": request.GET.get("low", ""), "show_low": True})


@login_required
@require_roles(*IT)
def stock_edit(request, pk=None): return STOCK.edit(request, pk)


@login_required
@require_roles(*IT)
def stock_delete(request, pk):
    it = get_object_or_404(m.StockItem, pk=pk)
    if it.moves.exists():
        messages.error(request, "Barang yang punya riwayat mutasi tidak bisa dihapus (riwayat dijaga)."); return redirect(f"/it/stock/{pk}/")
    return STOCK.delete(request, pk)


@login_required
@require_roles(*IT)
def moves_list(request):
    return MOVES.list(request, extra=lambda qs: qs.select_related("item", "department"), ctx={"tab": "stock", "page_title": "Barang Masuk & Keluar"})


class MoveForm(forms.ModelForm):
    class Meta:
        model = m.StockMovement; fields = ["direction", "qty", "date", "party", "department", "asset", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
    def clean(self):
        c = super().clean()
        if c.get("direction") == "keluar" and not c.get("department"): self.add_error("department", "Isi departemen penerima untuk barang keluar.")
        return c


@login_required
@require_roles(*IT)
def stock_detail(request, pk):
    it = get_object_or_404(m.StockItem, pk=pk)
    form = MoveForm(request.POST or None, initial={"direction": request.GET.get("arah", "keluar")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            mv = m.StockMovement.record(it.pk, d["direction"], d["qty"], d["party"], by=request.user, date=d["date"], department=d["department"], asset=d["asset"], note=d["note"])
        except ValidationError as e: form.add_error("qty", e.messages[0])
        else:
            log(request, "it", f"stock_{mv.direction}", it, None, {"qty": mv.qty, "party": mv.party, "dept": mv.department_id})
            messages.success(request, f"Barang {mv.direction} dicatat."); return redirect(request.path)
    it.refresh_from_db()
    return render(request, "it/stock_detail.html", {"i": it, "form": form, "moves": it.moves.select_related("department", "by")[:50], "wishes": it.wishes.exclude(status__in=("diterima", "ditolak"))})


# ---------- wishlist / order
WISH = Spec("wishlist", m.Wish, "Wishlist / Order IT", ["title", "qty", "item", "department", "requester", "reason", "priority", "est_price", "decision_note"],
            ["Barang / kebutuhan", "Jml", "Departemen", "Alasan", "Prioritas", "Status"],
            lambda w: [pill(w.title), pill(str(w.qty)), pill(w.department.name), pill((w.reason[:70] + "…") if len(w.reason) > 70 else w.reason),
                       pill(w.get_priority_display(), "er" if w.priority in ("tinggi", "kritis") else ""), pill(w.get_status_display(), "ok" if w.status in ("diterima", "disetujui") else "er" if w.status == "ditolak" else "wn")],
            search=("title", "requester", "reason", "department__name"), filters=(), order=("-id",), noun="permintaan")
WISH.filters = (("status", m.Wish.STATUS), ("priority", m.Wish.PRIO)); WISH.ubah = True


@login_required
@require_roles(*IT)
def wish_list(request):
    return WISH.list(request, extra=lambda qs: qs.select_related("department"), ctx={"tab": "wishlist", "detail_link": True})


@login_required
@require_roles(*IT)
def wish_edit(request, pk=None): return WISH.edit(request, pk)


@login_required
@require_roles(*IT)
def wish_delete(request, pk): return WISH.delete(request, pk)


@login_required
@require_roles(*IT)
def wish_detail(request, pk):
    w = get_object_or_404(m.Wish.objects.select_related("department", "item", "created_by"), pk=pk)
    if request.method == "POST":
        to, note = request.POST.get("to"), request.POST.get("note", "").strip()[:300]
        if to in m.Wish.FLOW[w.status]:
            if to == "ditolak" and not note:
                messages.error(request, "Isi alasan penolakan di catatan.")
                return redirect(request.path)
            old = w.status; w.status = to; w.decision_note = note or w.decision_note
            if to == "diterima" and w.item_id and not w.received_logged:
                m.StockMovement.record(w.item_id, "masuk", w.qty, f"Pesanan {w.department.name}", by=request.user, note=f"Wishlist #{w.pk}: {w.title}"); w.received_logged = True
            w.save(); log(request, "it", "wish_status", w, {"status": old}, {"status": to})
            tell_requester(w, f"Permintaan IT “{w.title[:80]}” → {w.get_status_display()}" + (f": {w.decision_note[:90]}" if to == "ditolak" and w.decision_note else ""))
            messages.success(request, f"Status → {w.get_status_display()}.")
        return redirect(request.path)
    return render(request, "it/wish_detail.html", {"w": w, "nexts": [(s, dict(m.Wish.STATUS)[s]) for s in m.Wish.FLOW[w.status]]})


class _DeptMixin:
    """Admin Dept/Poli: departemen otomatis (kolom disembunyikan); HRD/Payroll memilih departemen."""
    def _dept(self, user):
        self.user = user
        if user.role in DEPT_ACTORS: self.fields.pop("department")
        else: self.fields["department"].empty_label = "— pilih departemen —"


class WishSubmitForm(_DeptMixin, forms.ModelForm):
    class Meta:
        model = m.Wish; fields = ["department", "title", "qty", "priority", "est_price", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 4, "placeholder": "Mengapa barang ini dibutuhkan? Apa dampaknya bila tidak ada?"}), "title": forms.TextInput(attrs={"placeholder": "mis. Laptop pengganti untuk staf HRD"}),
                   "est_price": forms.NumberInput(attrs={"placeholder": "opsional"}), "qty": forms.NumberInput(attrs={"min": 1, "max": 999})}
        help_texts = {"reason": "Wajib — dipakai IT untuk menentukan prioritas persetujuan.", "est_price": "Boleh dikosongkan; IT akan mengisi dari penawaran pemasok."}
    def __init__(self, *a, user=None, **k): super().__init__(*a, **k); self._dept(user)
    def clean_reason(self):
        r = self.cleaned_data["reason"].strip()
        if len(r) < 10: raise forms.ValidationError("Jelaskan alasannya (minimal 10 karakter).")
        return r
    def clean_qty(self):
        q = self.cleaned_data["qty"]
        if not 1 <= q <= 999: raise forms.ValidationError("Jumlah 1–999.")
        return q


class HelpSubmitForm(_DeptMixin, forms.ModelForm):
    """Permintaan JASA / bantuan teknis → masuk sebagai tiket IT berlabel departemen."""
    class Meta:
        model = m.Ticket; fields = ["department", "category", "title", "detail", "location", "contact", "priority", "due"]
        widgets = {"detail": forms.Textarea(attrs={"rows": 4, "placeholder": "Apa yang terjadi / apa yang dibutuhkan? Sejak kapan? Sudah dicoba apa?"}), "title": forms.TextInput(attrs={"placeholder": "mis. Printer ruang HRD tidak bisa mencetak"}),
                   "location": forms.TextInput(attrs={"placeholder": "mis. Gedung A lantai 2, ruang HRD"}), "contact": forms.TextInput(attrs={"placeholder": "no. HP / ext."}), "due": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        help_texts = {"detail": "Wajib.", "due": "Opsional — isi bila ada tenggat (mis. sebelum rapat/audit)."}
    def __init__(self, *a, user=None, **k): super().__init__(*a, **k); self._dept(user); self.fields["due"].label = "Dibutuhkan paling lambat"; self.fields["category"].label = "Jenis bantuan"
    def clean_detail(self):
        r = self.cleaned_data["detail"].strip()
        if len(r) < 10: raise forms.ValidationError("Jelaskan kebutuhan/masalahnya (minimal 10 karakter).")
        return r
    def clean_due(self):
        d = self.cleaned_data.get("due")
        if d and d < date.today(): raise forms.ValidationError("Tanggal sudah lewat.")
        return d


@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI, Role.PAYROLL, Role.IT)
def wish_submit(request):
    """Satu halaman 'Permintaan IT' untuk akun departemen: pilih BARANG (→ wishlist/order) atau JASA/bantuan (→ tiket IT). Admin Dept/Poli otomatis terikat ke departemennya."""
    u = request.user
    if u.role in DEPT_ACTORS and not u.department_id: return render(request, "it/wish_submit.html", {"blocked": True})
    jenis = request.POST.get("jenis") if request.POST.get("jenis") in ("barang", "jasa") else request.GET.get("jenis", "barang")
    fb, fj = WishSubmitForm(user=u), HelpSubmitForm(user=u)
    if request.method == "POST":
        form = (HelpSubmitForm if jenis == "jasa" else WishSubmitForm)(request.POST, user=u)
        if form.is_valid():
            o = form.save(commit=False)
            if u.role in DEPT_ACTORS: o.department_id = u.department_id
            o.requester = u.get_full_name() or u.username; o.created_by = u; o.save()
            if jenis == "jasa":
                log(request, "it", "help_create", o, None, {"title": o.title, "dept": o.department_id})
                notify.send((Role.IT,), "it", f"Permintaan bantuan IT: {o.title[:80]} ({o.department.name})", "/it/tickets/%d/" % o.pk)
            else:
                log(request, "it", "wish_create", o, None, {"title": o.title, "dept": o.department_id})
                notify.send((Role.IT,), "it", f"Permintaan barang IT: {o.title[:80]} ({o.department.name})", f"/it/wishlist/{o.pk}/")
            messages.success(request, "Permintaan dikirim ke tim IT."); return redirect(f"{request.path}?jenis={jenis}")
        if jenis == "jasa": fj = form
        else: fb = form
    wq, tq = m.Wish.objects.select_related("department"), m.Ticket.objects.select_related("department")
    if u.role in DEPT_ACTORS: wq, tq = wq.filter(department_id=u.department_id), tq.filter(department_id=u.department_id, created_by__isnull=False)
    elif u.role != Role.IT: wq, tq = wq.filter(created_by=u), tq.filter(created_by=u)
    mine = [{"at": w.created_at, "kind": "Barang", "title": w.title, "sub": w.reason, "dept": w.department, "qty": w.qty, "status": w.get_status_display(), "tone": "rejected" if w.status == "ditolak" else "approved" if w.status in ("diterima", "disetujui") else "pending", "note": w.decision_note} for w in wq[:30]]
    mine += [{"at": t.created_at, "kind": "Jasa", "title": t.title, "sub": t.detail, "dept": t.department, "qty": "", "status": t.get_status_display(), "tone": "rejected" if t.status == "ditolak" else "approved" if t.status == "selesai" else "pending", "note": t.decision_note or t.get_category_display()} for t in tq[:30]]
    mine.sort(key=lambda x: x["at"], reverse=True)
    return render(request, "it/wish_submit.html", {"fb": fb, "fj": fj, "jenis": jenis, "mine": mine[:30]})
