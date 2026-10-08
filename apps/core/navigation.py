"""Menu samping per peran (VISION → "Tampilan menyesuaikan peran"). Hanya halaman yang SUDAH ADA yang ditampilkan;
menyembunyikan menu hanya kenyamanan, izin tetap diperiksa di server (require_roles / scope)."""
from .models import Role

# (label, url, ikon). Kelompok = (judul, [butir]).
_DASH = ("Dashboard", "/", "home")
_EMP = ("Data Karyawan", "/employees/", "users")
_INFO = ("Informasi", [("Pengumuman", "/announcements/", "chat")])

def _groups(role):
    if role == Role.SUPERADMIN:
        return [("Utama", [_DASH, _EMP, ("Pengajuan", "/requests/", "clipboard"), ("Cuti & Libur", "/leave/", "calendar"),
                           ("Jadwal Shift", "/schedule/", "clock"), ("Operasional HRD", "/hrd/", "briefcase")]),
                ("Poliklinik", _poli(True)),
                ("Pengaturan", [("Master Data", "/master/department/", "sliders"), ("Pengguna", "/users/", "shield"),
                                ("Audit Log", "/audit/", "file")]), _INFO]
    if role == Role.HRD:
        return [("HRD", [_DASH, _EMP, ("Mutasi & Pengajuan", "/requests/", "clipboard"), ("Cuti & Libur", "/leave/", "calendar"),
                        ("Jadwal Shift", "/schedule/", "clock"), ("Operasional HRD", "/hrd/", "briefcase"), ("Master Data", "/master/department/", "sliders")]), _INFO]
    if role == Role.DEPT_ADMIN:
        return [("Admin Departemen", [_DASH, ("Data Karyawan Departemen", "/employees/", "users"),
                                      ("Pengajuan & Monitoring", "/requests/", "clipboard"), ("Jadwal Shift", "/schedule/", "clock")]), _INFO]
    if role == Role.POLI:
        return [("Poli", [_DASH, ("Data Pasien/Karyawan", "/employees/", "users")] + _poli(True)), _INFO]
    return []

def _poli(with_hub):
    items = [("Poliklinik", "/poli/", "plus")] if with_hub else []
    return items + [("Pemeriksaan & Rekam Medis", "/poli/records/", "activity"), ("Obat & Stok", "/poli/medicines/", "pill"), ("Rekap Stok Obat", "/poli/reports/stock/", "file"),
                    ("Rujukan", "/poli/referrals/", "repeat"), ("Master Diagnosa", "/poli/diagnoses/", "file")]

def build(user, path):
    """Kembalikan (kelompok, breadcrumb). Satu butir aktif: yang awalan URL-nya paling panjang cocok ("/" hanya untuk beranda)."""
    groups = _groups(user.role)
    best, best_len = None, 0
    for _, items in groups:
        for it in items:
            url = it[1]
            prefix = "/master/" if url.startswith("/master/") else url  # Master: semua jenis master tetap satu butir
            m = (path == "/") if url == "/" else path.startswith(prefix)
            if m and len(prefix) > best_len: best, best_len = it, len(prefix)
    out = [{"title": t, "items": [{"label": i[0], "url": i[1], "icon": i[2], "active": i is best} for i in items]} for t, items in groups]
    return out, (best[0] if best and best[1] != "/" else "")
