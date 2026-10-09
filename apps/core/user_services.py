"""Aturan bisnis manajemen user. Dipanggil DI DALAM transaksi (transaction.atomic) oleh view.

Invarian utama: selalu ada minimal satu Superadmin aktif. Pemeriksaan sederhana tidak cukup — dua Superadmin yang saling
menurunkan/menonaktifkan pada saat yang sama masing-masing melihat yang lain masih Superadmin lalu keduanya berhasil (nol Superadmin).
Karena itu semua Superadmin aktif dikunci (urut pk, agar tidak deadlock), keadaan diperiksa ulang dari DB, dan pelakunya sendiri
diverifikasi ulang (ia bisa saja baru diturunkan oleh transaksi lain sesudah lolos pemeriksaan role di awal request).
"""
from django.core.exceptions import PermissionDenied
from .models import Role, User


def dept_admin_map():
    """{department_id: [nama Admin Departemen aktif]}. Tiap departemen wajib punya admin sendiri (minimal satu aktif; A74) —
    dipakai form Validasi Kehadiran, daftar Pengguna, dan Master Departemen."""
    out = {}
    for u in User.objects.filter(role=Role.DEPT_ADMIN, is_active=True, department__isnull=False).order_by("username"):
        out.setdefault(u.department_id, []).append(u.get_full_name() or u.get_username())
    return out


def departments_without_admin():
    from apps.hr.models import Department
    m = dept_admin_map()
    return [d for d in Department.objects.order_by("name") if d.pk not in m]


class UserRuleError(Exception):
    """Pelanggaran aturan bisnis; pesan aman ditampilkan ke pengguna."""


def guard(actor_pk, target_pk, new_role, new_active):
    """Kunci + validasi perubahan role/status aktif `target`. Mengembalikan baris target (keadaan DB saat ini, terkunci)."""
    list(User.objects.select_for_update().filter(role=Role.SUPERADMIN, is_active=True).order_by("pk").only("pk"))
    if not User.objects.filter(pk=actor_pk, role=Role.SUPERADMIN, is_active=True).exists():
        raise PermissionDenied  # pelaku sudah bukan Superadmin aktif (diturunkan/dinonaktifkan oleh proses lain)
    cur = User.objects.select_for_update().get(pk=target_pk)
    losing = cur.role == Role.SUPERADMIN and cur.is_active and (new_role != Role.SUPERADMIN or not new_active)
    # Invarian terkuat diperiksa lebih dulu: jangan pernah menyisakan nol Superadmin aktif (juga melindungi pemanggil lain di masa depan).
    if losing and not User.objects.filter(role=Role.SUPERADMIN, is_active=True).exclude(pk=cur.pk).exists():
        raise UserRuleError("Tidak boleh menghilangkan Superadmin aktif yang terakhir.")
    if cur.pk == actor_pk and (new_role != cur.role or not new_active):
        raise UserRuleError("Tidak bisa mengubah role atau menonaktifkan akun Anda sendiri.")
    return cur
