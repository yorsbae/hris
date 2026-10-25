"""Kirim notifikasi menurut TUPOKSI akun (putaran 62): satu pintu supaya penerima konsisten — hanya akun AKTIF, hanya peran yang memang berwenang membuka tautannya.
Peran → tugas: HRD (pengajuan, kontrak, stok seragam, validasi), Payroll (periode dari HRD, karyawan keluar → gaji akhir), Poli (stok obat), Superadmin (situs & rekrutmen), Admin Dept (hasil pengajuan)."""
from .models import Notification, User


def send(roles, kind, title, link="", *, department_id=None, exclude=None, skip_if_unread_title=None):
    """→ jumlah notifikasi dibuat. `skip_if_unread_title`: lewati penerima yang sudah punya notifikasi belum dibaca berjudul sama (tidak menumpuk)."""
    qs = User.objects.filter(role__in=list(roles), is_active=True)
    if department_id is not None: qs = qs.filter(department_id=department_id)
    if exclude is not None: qs = qs.exclude(pk=getattr(exclude, "pk", exclude))
    title = title[:200]
    if skip_if_unread_title: qs = qs.exclude(pk__in=Notification.objects.filter(kind=kind, is_read=False, title=title).values("user_id"))
    objs = [Notification(user=u, kind=kind, title=title, link=link) for u in qs]
    Notification.objects.bulk_create(objs); return len(objs)
