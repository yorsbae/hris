"""Pengajuan Stand By (kerja saat waktu istirahat) & Lembur: Admin Departemen (mis. Admin Produksi) → HRD (putaran 30).

Satu form untuk BANYAK karyawan sekaligus (satu regu/shift), tetapi disimpan sebagai satu ChangeRequest per karyawan (jejak,
scope, dan persetujuan tetap per orang). Pengajuan satu kiriman berbagi `batch` agar HRD dapat melihat/memutuskannya sekaligus.
Disetujui HRD = final dan tercatat (services.AUTO_EXEC); tidak mengubah master karyawan. Batas menit & jendela tanggal adalah
KONFIGURASI (settings/.env), bukan kode — angka awal = usulan, lihat PROGRESS A77–A80."""
import re, uuid
from datetime import datetime, timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Notification, Role, User
from apps.core.scope import require_roles
from . import services
from .forms import ACTIVE, RANGE_TYPES
from .models import ChangeRequest, Department, Employee

KIND_HELP = {"lembur": "Kerja di luar jam shift (setelah pulang / sebelum masuk / hari libur).",
             "standby": "Tetap bekerja atau siaga di mesin/area saat waktu istirahat."}
MAX_PEOPLE = 100
NIK_SPLIT = re.compile(r"[\s,;]+")

def limit(kind):
    """Batas durasi per pengajuan per karyawan per tanggal, menit (dapat disetel di settings/.env)."""
    return int(getattr(settings, "STANDBY_MAX_MENIT" if kind == "standby" else "LEMBUR_MAX_MENIT", 120 if kind == "standby" else 240))
def window():
    """(hari ke belakang, hari ke depan) tanggal yang boleh diajukan."""
    return int(getattr(settings, "EXTRA_WORK_PAST_DAYS", 7)), int(getattr(settings, "EXTRA_WORK_FUTURE_DAYS", 14))

def minutes_between(start, end):
    """Durasi menit; selesai <= mulai dianggap melewati tengah malam (+1 hari). 0 = tidak valid."""
    m = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return m + 1440 if m <= 0 else m

def _span(p):
    s = datetime.strptime(p["time"], "%H:%M").hour * 60 + int(p["time"][3:5])
    return s, s + int(p["minutes"])

def conflict(emp, kind, date, start, minutes):
    """Pesan galat bila karyawan sudah izin/cuti/sakit, jamnya bertumpuk, atau total harian melampaui batas; selain itu None."""
    iso = date.isoformat()
    for r in ChangeRequest.objects.filter(employee=emp, type__in=RANGE_TYPES, status__in=ACTIVE):
        a, b = r.payload.get("start_date"), r.payload.get("end_date")
        if a and b and a <= iso <= b: return f"{emp.name} sedang izin/cuti/sakit pada {iso}."
    s0 = start.hour * 60 + start.minute
    total = 0
    for r in ChangeRequest.objects.filter(employee=emp, type__in=("standby", "lembur"), status__in=ACTIVE, payload__date=iso):
        a, b = _span(r.payload)
        if s0 < b and a < s0 + minutes: return f"{emp.name} sudah punya pengajuan {r.type} yang jamnya bertumpuk pada {iso} (#{r.pk})."
        if r.type == kind: total += int(r.payload["minutes"])
    if total + minutes > limit(kind):
        return f"{emp.name}: total {kind} pada {iso} menjadi {total + minutes} menit, batas {limit(kind)} menit per hari."
    return None


class ExtraWorkForm(forms.Form):
    kind = forms.ChoiceField(label="Jenis", choices=[("lembur", "Lembur"), ("standby", "Stand By (kerja saat istirahat)")], widget=forms.RadioSelect)
    date = forms.DateField(label="Tanggal kerja", widget=forms.DateInput(attrs={"type": "date"}))
    start = forms.TimeField(label="Jam mulai", widget=forms.TimeInput(attrs={"type": "time"}))
    end = forms.TimeField(label="Jam selesai", widget=forms.TimeInput(attrs={"type": "time"}))
    reason = forms.CharField(label="Alasan / pekerjaan yang dikerjakan", max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
    extra_niks = forms.CharField(label="NIK tambahan", required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Opsional: tempel NIK lain, pisahkan dengan baris baru atau koma"}))

    def __init__(self, *a, user, **k):
        super().__init__(*a, **k)
        self.user, self.people = user, []

    def clean_date(self):
        d, today = self.cleaned_data["date"], timezone.localdate()
        past, fut = window()
        if d < today - timedelta(days=past): raise forms.ValidationError(f"Terlalu lampau: paling lama {past} hari ke belakang.")
        if d > today + timedelta(days=fut): raise forms.ValidationError(f"Terlalu jauh: paling lama {fut} hari ke depan.")
        return d

    def clean(self):
        d = super().clean()
        niks = list(dict.fromkeys([n.strip() for n in self.data.getlist("niks") if n.strip()] + [n for n in NIK_SPLIT.split(d.get("extra_niks") or "") if n]))
        if not niks: raise forms.ValidationError("Pilih minimal satu karyawan.")
        if len(niks) > MAX_PEOPLE: raise forms.ValidationError(f"Maksimal {MAX_PEOPLE} karyawan per pengajuan.")
        qs = Employee.objects.select_related("department").filter(nik__in=niks, status="aktif")
        if self.user.role == Role.DEPT_ADMIN: qs = qs.filter(department_id=self.user.department_id)  # di luar scope = "tidak ditemukan"
        found = {e.nik: e for e in qs}
        missing = [n for n in niks if n not in found]
        if missing: raise forms.ValidationError("NIK tidak ditemukan / bukan karyawan aktif di departemen Anda: " + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
        self.people = [found[n] for n in niks]
        kind, date, st, en = d.get("kind"), d.get("date"), d.get("start"), d.get("end")
        if not (kind and date and st and en): return d
        self.minutes = minutes_between(st, en)
        if self.minutes > limit(kind): raise forms.ValidationError(f"Durasi {self.minutes} menit melebihi batas {limit(kind)} menit untuk {kind}.")
        errs = [m for e in self.people if (m := conflict(e, kind, date, st, self.minutes))]
        if errs: raise forms.ValidationError(errs[:8] + ([f"… dan {len(errs) - 8} masalah lain"] if len(errs) > 8 else []))
        return d


def roster_department(request):
    """Departemen yang daftar karyawannya ditampilkan: Admin Dept = departemennya sendiri; HRD/Superadmin memilih lewat ?department=."""
    if request.user.role == Role.DEPT_ADMIN: return request.user.department
    return Department.objects.filter(pk=request.GET.get("department") or 0).first()


@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN)
def extra_work_new(request):
    dept = roster_department(request)
    form = ExtraWorkForm(request.POST or None, user=request.user, initial={"kind": request.GET.get("kind") or "lembur", "date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        d, batch = form.cleaned_data, uuid.uuid4().hex
        payload = {"date": d["date"].isoformat(), "time": d["start"].strftime("%H:%M"), "time_to": d["end"].strftime("%H:%M"),
                   "minutes": form.minutes, "reason": d["reason"], "batch": batch}
        with transaction.atomic():
            for e in form.people:
                req = ChangeRequest.objects.create(type=d["kind"], employee=e, department=e.department, payload=payload, requested_by=request.user)
                services.submit(req, request.user, notify=False)
            depts = ", ".join(sorted({e.department.name for e in form.people}))
            Notification.objects.bulk_create([Notification(user=u, kind="request", link=f"/requests/g/lembur/?batch={batch}",
                title=f"{len(form.people)} pengajuan {d['kind']} {payload['date']} dari {depts} menunggu keputusan") for u in User.objects.filter(role=Role.HRD, is_active=True)])
            log(request, "hr", "extra_work_create", None, None, {"batch": batch, "type": d["kind"], "people": len(form.people), "date": payload["date"], "minutes": form.minutes})
        messages.success(request, f"{len(form.people)} pengajuan {d['kind']} terkirim ke HRD. Pantau statusnya di halaman ini.")
        return redirect(f"/requests/g/lembur/?batch={batch}")
    roster = Employee.objects.filter(department=dept, status="aktif").select_related("position").order_by("name") if dept else []
    picked = set(request.POST.getlist("niks")) if request.method == "POST" else set()
    past, fut = window()
    return render(request, "extra_work_form.html", {"form": form, "dept": dept, "roster": roster, "picked": picked, "depts": Department.objects.order_by("name") if request.user.role != Role.DEPT_ADMIN else [],
                                                    "limits": {"lembur": limit("lembur"), "standby": limit("standby")}, "past": past, "fut": fut, "help": KIND_HELP})


@require_POST
@login_required
@require_roles(Role.HRD)
def batch_action(request, batch, action):
    """HRD memutuskan seluruh pengajuan satu kiriman yang masih menunggu (setujui / tolak). Tiap baris tetap lewat services.transition."""
    if action not in ("approved", "rejected") or not re.fullmatch(r"[0-9a-f]{32}", batch): return redirect("/requests/g/lembur/")
    note = request.POST.get("note", "").strip()
    if action == "rejected" and not note:
        messages.error(request, "Alasan penolakan wajib diisi."); return redirect(f"/requests/g/lembur/?batch={batch}")
    n, owners = 0, {}
    with transaction.atomic():
        for r in ChangeRequest.objects.filter(type__in=("standby", "lembur"), status="pending", payload__batch=batch):
            services.transition(r, action, request.user, note, notify=False); n += 1; owners[r.requested_by_id] = r
        for r in owners.values():
            word = "disetujui" if action == "approved" else "ditolak"
            Notification.objects.create(user_id=r.requested_by_id, kind="approval", link=f"/requests/g/lembur/?batch={batch}",
                                        title=f"Pengajuan {r.type} {r.payload.get('date')} ({n} orang) {word} HRD" + (f": {note[:80]}" if note else ""))
        log(request, "hr", f"extra_work_batch_{action}", None, {"status": "pending"}, {"batch": batch, "rows": n})
    messages.success(request, f"{n} pengajuan {'disetujui' if action == 'approved' else 'ditolak'}." if n else "Tidak ada pengajuan yang masih menunggu pada kiriman ini.")
    return redirect(f"/requests/g/lembur/?batch={batch}")
