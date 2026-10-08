"""Situs admin bertema sama dengan aplikasi (sidebar navy, bilah atas, KPI) — putaran 16.
Hanya presentasi: izin admin tetap bawaan Django (is_staff + permission model); tidak ada model/URL baru."""
from django.contrib import admin

# kata kunci nama model (huruf kecil) -> ikon di templates/_icons.html
_ICONS = (("employee", "users"), ("user", "user"), ("group", "key"), ("department", "briefcase"), ("position", "user"), ("shift", "clock"),
          ("contract", "file"), ("changerequest", "clipboard"), ("announcement", "chat"), ("bpjs", "shield"), ("catering", "briefcase"),
          ("maternity", "calendar"), ("project", "briefcase"), ("aid", "plus"), ("medic", "pill"), ("obat", "pill"), ("drug", "pill"),
          ("diagnos", "file"), ("referral", "repeat"), ("record", "activity"), ("visit", "activity"), ("stock", "pill"), ("leave", "calendar"))


# Nama tampilan Indonesia (presentasi saja; tanpa mengubah model/migrasi)
_ID = {"Group": "Grup", "Announcement": "Pengumuman", "ChangeRequest": "Pengajuan", "Contract": "Kontrak", "Department": "Departemen", "Employee": "Karyawan",
       "Position": "Jabatan", "Shift": "Shift", "ShiftGroup": "Kelompok shift & rotasi", "Aid": "Bantuan", "BpjsMembership": "Keanggotaan BPJS", "BpjsStatusLog": "Log status BPJS", "CateringOrder": "Pesanan katering",
       "MaternityLeave": "Cuti hamil", "Project": "Proyek", "ProjectWork": "Pekerja harian proyek", "WarningLetter": "Surat peringatan", "Diagnosis": "Diagnosa", "Medicine": "Obat", "StockMovement": "Kartu stok obat"}


def icon_for(object_name):
    n = object_name.lower()
    return next((i for k, i in _ICONS if k in n), "db")


class HrisAdminSite(admin.AdminSite):
    def each_context(self, request):
        ctx = super().each_context(request)
        path = request.path
        apps = []
        for a in ctx.get("available_apps", []):
            models = [dict(m, name=_ID.get(m["object_name"], m["name"]), icon=icon_for(m["object_name"]),
                           active=bool(m.get("admin_url")) and path.startswith(m["admin_url"])) for m in a["models"]]
            apps.append(dict(a, models=models))
        ctx["hb_apps"] = apps
        return ctx

    def index(self, request, extra_context=None):
        from django.db.models import Q
        from apps.hr.models import ChangeRequest, Employee
        from .models import User
        kpi = {"total": Employee.objects.count(), "aktif": Employee.objects.filter(status="aktif").count(),
               "pending": ChangeRequest.objects.filter(Q(status="submitted") | Q(status="pending")).count(),
               "users": User.objects.filter(is_active=True).count()}
        return super().index(request, dict(extra_context or {}, kpi=kpi))
