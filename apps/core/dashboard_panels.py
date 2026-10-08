"""Panel dashboard (grafik, aktivitas, menunggu persetujuan) per peran. Semua dihitung di server dengan agregasi
(tidak memuat ribuan baris) dan mengikuti scope peran. VISION → "Rujukan UI/UX → Aturan penerapan":
- aktivitas medis (nama pasien) hanya untuk Poli; Superadmin hanya agregat; HRD & Admin Dept tanpa data poli
- tidak ada nilai sensitif (NIK KTP, rekening, BPJS, isi medis) di aktivitas
- pembanding hanya bila dapat dihitung andal (kini: kunjungan poli hari ini vs kemarin)
- kehadiran belum ada (Tahap 6): dikirim sebagai keadaan kosong jujur, bukan angka karangan."""
from datetime import date, timedelta
from django.db.models import Count, F
from django.db.models.functions import TruncDate, TruncMonth
from django.http import JsonResponse
from django.utils import timezone
from .models import Role
from .scope import require_roles

MON = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"]
WAITING, APPROVED, REJECTED = ("submitted", "pending"), ("approved", "executed"), ("rejected",)
KIND = {"berobat": "Berobat", "kecelakaan_kerja": "Kecelakaan kerja", "pemeriksaan": "Pemeriksaan", "kehamilan": "Pemeriksaan kehamilan"}

def _dm(d): return f"{d.day} {MON[d.month - 1]}"
def _when(dt):
    dt = timezone.localtime(dt)
    return dt.strftime("%H:%M") if dt.date() == timezone.localdate() else _dm(dt.date())
def _pill(status): return ("pending", "Menunggu") if status in WAITING else ("approved", "Disetujui") if status in APPROVED else ("rejected", "Ditolak") if status == "rejected" else (status, status.capitalize())

def leave_range(kind, today):
    if kind == "prev":
        end = today.replace(day=1) - timedelta(days=1); return end.replace(day=1), end, "Bulan lalu"
    if kind == "year": return today.replace(month=1, day=1), today, "Tahun ini"
    return today.replace(day=1), today, "Bulan ini"

def visits_by_day(days, today):
    from apps.poli.models import MedicalRecord
    start = today - timedelta(days=days - 1)
    got = {r["d"]: r["n"] for r in MedicalRecord.objects.filter(visit_at__date__gte=start, visit_at__date__lte=today)
           .annotate(d=TruncDate("visit_at")).values("d").annotate(n=Count("id")).values("d", "n")}
    ds = [start + timedelta(days=i) for i in range(days)]
    return {"labels": [_dm(d) for d in ds], "values": [got.get(d, 0) for d in ds]}

def visits_by_month(today):
    from apps.poli.models import MedicalRecord
    ms, y, m = [], today.year, today.month
    for _ in range(6):
        ms.append((y, m)); m -= 1
        if m == 0: y, m = y - 1, 12
    ms.reverse()
    first = date(ms[0][0], ms[0][1], 1)
    got = {(r["m"].year, r["m"].month): r["n"] for r in MedicalRecord.objects.filter(visit_at__date__gte=first)
           .annotate(m=TruncMonth("visit_at")).values("m").annotate(n=Count("id")).values("m", "n")}
    return {"labels": [MON[b - 1] for _, b in ms], "values": [got.get(k, 0) for k in ms]}

@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def panels(request):
    from apps.hr.forms import LABELS
    from apps.hr.models import ChangeRequest
    from apps.poli.models import MedicalRecord, Medicine
    u, today = request.user, timezone.localdate()
    out = {"role": u.role}
    hr_side = u.role in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN)
    poli_agg = u.role in (Role.SUPERADMIN, Role.POLI)
    reqs = ChangeRequest.objects.all()
    if u.role == Role.DEPT_ADMIN: reqs = reqs.filter(department=u.department_id)
    if hr_side:
        out["attendance"] = None  # Tahap 6 belum ada → keadaan kosong jujur
        a, b, label = leave_range(request.GET.get("leave", "month"), today)
        by = {r["status"]: r["n"] for r in reqs.filter(type="cuti", created_at__date__gte=a, created_at__date__lte=b).values("status").annotate(n=Count("id"))}
        items = [("Disetujui", "ok", sum(by.get(s, 0) for s in APPROVED)), ("Menunggu", "wn", sum(by.get(s, 0) for s in WAITING)),
                 ("Ditolak", "er", sum(by.get(s, 0) for s in REJECTED))]
        out["leave"] = {"period": label, "total": sum(i[2] for i in items), "items": [{"label": l, "tone": t, "n": n} for l, t, n in items]}
        rows = reqs.select_related("employee", "department", "requested_by").order_by("-created_at")[:6]
        out["activity"] = [{"time": _when(r.created_at), "title": "Pengajuan " + LABELS.get(r.type, r.type).lower(), "by": f"{r.employee.name} ({r.department.name})",
                            "note": f"Diajukan oleh {r.requested_by.get_username()}", "pill": _pill(r.status)[0], "status": _pill(r.status)[1], "url": f"/requests/{r.pk}/"} for r in rows]
        pend = reqs.filter(status__in=WAITING).select_related("employee", "department").order_by("-created_at")[:4]
        out["side"] = {"title": "Pending Approval" if u.role != Role.DEPT_ADMIN else "Pengajuan Menunggu", "link": "/requests/?status=pending",
                       "empty": "Tidak ada pengajuan yang menunggu.",
                       "items": [{"title": LABELS.get(r.type, r.type), "sub": f"{r.employee.name} · {r.department.name}", "at": "Diajukan " + _dm(timezone.localtime(r.created_at).date()) + " " + timezone.localtime(r.created_at).strftime("%H:%M"),
                                  "url": f"/requests/{r.pk}/"} for r in pend]}
    if poli_agg:
        try: days = {"7": 7, "30": 30}[request.GET.get("visits", "7")]
        except KeyError: days = 7
        out["visits"] = {"days": days, **visits_by_day(days, today)}
        out["visits6m"] = visits_by_month(today)
        y = MedicalRecord.objects.filter(visit_at__date=today - timedelta(days=1)).count()
        t = MedicalRecord.objects.filter(visit_at__date=today).count()
        out["deltas"] = {"visits_today": {"diff": t - y, "vs": "kemarin"}}
    if u.role == Role.POLI:  # satu-satunya yang melihat nama pasien (VISION)
        rows = MedicalRecord.objects.select_related("employee__department").order_by("-visit_at")[:6]
        out["activity"] = [{"time": _when(r.visit_at), "title": "Pemeriksaan poliklinik", "by": f"{r.employee.name} ({r.employee.department.name})",
                            "note": KIND.get(r.kind, r.kind), "pill": "selesai", "status": "Selesai", "url": f"/poli/records/{r.pk}/"} for r in rows]
        low = Medicine.objects.filter(stock__lte=F("min_stock")).order_by("stock")[:4]
        out["side"] = {"title": "Stok Obat Menipis", "link": "/poli/medicines/", "empty": "Semua stok di atas minimum.",
                       "items": [{"title": m.name, "sub": f"Stok {m.stock} {m.unit} · minimum {m.min_stock}", "at": "", "url": f"/poli/medicines/{m.pk}/"} for m in low]}
    return JsonResponse(out)
