"""Menu samping per peran (VISION → "Tampilan menyesuaikan peran"). Hanya halaman yang SUDAH ADA yang ditampilkan;
menyembunyikan menu hanya kenyamanan, izin tetap diperiksa di server (require_roles / scope)."""
from .models import Role

# (label, url, ikon). Kelompok = (judul, [butir]).
_DASH = ("Dashboard", "/", "home")
_EMP = ("Data Karyawan", "/employees/", "users")
def _req(all_label, status):
    """Butir Pengajuan + submenu per fungsi (putaran 20). Admin Dept tidak melihat Perubahan Status (hanya HRD)."""
    items = [(all_label, "/requests/", "clipboard"), ("Izin & Cuti", "/requests/g/izin/", "clipboard", True), ("Mutasi & Promosi", "/requests/g/mutasi/", "clipboard", True)]
    if status: items.append(("Perubahan Status", "/requests/g/status/", "clipboard", True))
    return items + [("Shift & Tukar Jadwal", "/requests/g/jadwal/", "clipboard", True)]

def _bpjs():
    """Butir BPJS + submenu Kesehatan / Ketenagakerjaan / Status (putaran 23, P4). Hanya HRD & Superadmin (halaman /hrd/ lain tetap lewat Operasional HRD)."""
    return [("BPJS", "/hrd/bpjs/", "shield"), ("Kesehatan (K)", "/hrd/bpjs/deductions/kes/", "shield", True),
            ("Ketenagakerjaan (TK)", "/hrd/bpjs/deductions/tk/", "shield", True), ("Semua Potongan", "/hrd/bpjs/deductions/", "shield", True)]

def _validasi(): return [("Validasi Kehadiran", "/validasi/", "clock")]  # putaran 26, P7 (HRD, Admin Dept, Superadmin)

def _ophrd():
    """Operasional HRD = menu + submenu (putaran 28). Induk membuka ringkasan /hrd/; tiap fungsi punya butir sendiri. Seragam (P5) pindah ke sini.
    BPJS tetap menu tersendiri (sudah bersubmenu sejak putaran 23). Hanya HRD & Superadmin."""
    return [("Operasional HRD", "/hrd/", "briefcase"), ("Bantuan (rekap)", "/hrd/aids/", "clipboard", True), ("Cuti Hamil", "/hrd/maternity/", "calendar", True),
            ("Pekerja Harian Proyek", "/hrd/projects/", "briefcase", True), ("Katering (rekap)", "/hrd/catering/", "clipboard", True),
            ("Surat Peringatan", "/hrd/warnings/", "file", True), ("Seragam", "/hrd/uniforms/", "clipboard", True)]

_INFO = ("Informasi", [("Pengumuman", "/announcements/", "chat")])

def _groups(role):
    if role == Role.SUPERADMIN:
        return [("Utama", [_DASH, _EMP, *_req("Semua Pengajuan", True), ("Cuti & Libur", "/leave/", "calendar"),
                           *_ophrd(), *_bpjs(), *_validasi()]),
                ("Poliklinik", _poli(True)),
                ("Pengaturan", [("Master Data", "/master/department/", "sliders"), ("Pengguna", "/users/", "shield"),
                                ("Audit Log", "/audit/", "file")]), _INFO]
    if role == Role.HRD:
        return [("HRD", [_DASH, _EMP, *_req("Semua Pengajuan", True), ("Cuti & Libur", "/leave/", "calendar"),
                        *_ophrd(), *_bpjs(), *_validasi(), ("Master Data", "/master/department/", "sliders")]), _INFO]
    if role == Role.DEPT_ADMIN:
        return [("Admin Departemen", [_DASH, ("Data Karyawan Departemen", "/employees/", "users"),
                                      *_req("Pengajuan & Monitoring", False), *_validasi()]), _INFO]
    if role == Role.POLI:
        return [("Poli", [_DASH, ("Data Pasien/Karyawan", "/employees/", "users")] + _poli(True)), _INFO]
    return []

def _poli(with_hub):
    items = [("Poliklinik", "/poli/", "plus")] if with_hub else []
    return items + [("Pemeriksaan & Rekam Medis", "/poli/records/", "activity"), ("Obat & Stok", "/poli/medicines/", "pill"), ("Rekap Stok Obat", "/poli/reports/stock/", "file"),
                    ("Rujukan", "/poli/referrals/", "repeat"), ("Tagihan Mitra", "/poli/billing/", "file"), ("Master Diagnosa", "/poli/diagnoses/", "file")]

# Catatan: halaman jadwal mingguan (/schedule/) tidak punya menu sendiri (putaran 18); dibuka dari Data Karyawan dan membuat butir itu aktif; sejak putaran 25 tombolnya dihapus dari Data Karyawan (URL tetap ada).
def _tree(items, best):
    """Kelompokkan butir datar menjadi pohon: butir bertanda sub (elemen ke-4) menjadi anak butir induk di atasnya.
    Induk berstatus 'open' bila dirinya atau salah satu anaknya aktif (halaman aktif tidak pernah tersembunyi di menu tertutup)."""
    out = []
    for it in items:
        node = {"label": it[0], "url": it[1], "icon": it[2], "active": it is best, "children": []}
        if len(it) > 3 and out: out[-1]["children"].append(node)
        else: out.append(node)
    for n in out:
        n["open"] = n["active"] or any(c["active"] for c in n["children"])
        n["key"] = "".join(ch if ch.isalnum() else "-" for ch in n["label"].lower())
    return out


def build(user, path):
    """Kembalikan (kelompok, breadcrumb). Satu butir aktif: yang awalan URL-nya paling panjang cocok ("/" hanya untuk beranda).
    Tiap kelompok: items (datar; sub=True untuk anak) dan tree (induk + children untuk dropdown)."""
    groups = _groups(user.role)
    best, best_len = None, 0
    for _, items in groups:
        for it in items:
            url = it[1]
            prefix = "/master/" if url.startswith("/master/") else url  # Master: semua jenis master tetap satu butir
            m = (path == "/") if url == "/" else path.startswith(prefix) or (url == "/employees/" and path.startswith("/schedule/"))
            if m and len(prefix) > best_len: best, best_len = it, len(prefix)
    out = [{"title": t, "tree": _tree(items, best), "items": [{"label": i[0], "url": i[1], "icon": i[2], "active": i is best, "sub": len(i) > 3} for i in items]} for t, items in groups]
    return out, (best[0] if best and best[1] != "/" else "")
