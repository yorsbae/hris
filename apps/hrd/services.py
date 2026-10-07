"""Aturan bisnis Operasional HRD. View hanya memanggil fungsi di sini; perubahan status selalu atomik dan divalidasi di server."""
from datetime import timedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.hr.models import ChangeRequest
from .models import Aid, BpjsMembership, BpjsStatusLog, MaternityLeave

# Bawaan cuti melahirkan: 1,5 bulan sebelum + 1,5 bulan sesudah HPL (≈ 45 + 45 hari). Dapat diubah lewat settings;
# HRD tetap bisa mengubah tanggal per kasus. Sesuaikan dengan kebijakan perusahaan.
MATERNITY_DAYS_BEFORE = getattr(settings, "MATERNITY_DAYS_BEFORE", 45)
MATERNITY_DAYS_AFTER = getattr(settings, "MATERNITY_DAYS_AFTER", 45)


# ---------------------------------------------------------------- BPJS
def set_bpjs_status(employee, scheme, status, effective_date, note, user):
    """Ubah status BPJS + tulis histori dalam satu transaksi. Mengembalikan status lama ('' bila belum pernah dicatat)."""
    try:
        with transaction.atomic():
            m = BpjsMembership.objects.select_for_update().filter(employee=employee, scheme=scheme).first()
            if m:
                if m.status == status: raise ValueError("Status sama dengan status saat ini.")
                if effective_date < m.effective_date: raise ValueError("Tanggal efektif tidak boleh lebih awal dari status saat ini.")
                old = m.status
                m.status, m.effective_date, m.note, m.updated_by = status, effective_date, note, user
                m.save()
            else:
                old = ""
                BpjsMembership.objects.create(employee=employee, scheme=scheme, status=status, effective_date=effective_date, note=note, updated_by=user)
            BpjsStatusLog.objects.create(employee=employee, scheme=scheme, old_status=old, new_status=status,
                                         effective_date=effective_date, note=note, changed_by=user)
            return old
    except IntegrityError:  # dua HRD mencatat bersamaan untuk karyawan/program yang sama
        raise ValueError("Status baru saja dicatat pengguna lain. Muat ulang halaman.")


# ---------------------------------------------------------------- Bantuan
def aid_transition(aid_id, to, user, note="", paid_at=None):
    """diajukan → disetujui/ditolak → dibayar. Baris dikunci agar dua klik/dua HRD tidak memproses dua kali."""
    with transaction.atomic():
        aid = Aid.objects.select_for_update().get(pk=aid_id)
        if to not in Aid.FLOW.get(aid.status, set()):
            raise ValueError(f"Tidak bisa dari status '{aid.get_status_display()}' ke '{to}'.")
        note = (note or "").strip()
        if to == "ditolak" and not note: raise ValueError("Alasan wajib diisi saat menolak.")
        before = aid.status
        aid.status = to
        if to in ("disetujui", "ditolak"):
            aid.decided_by, aid.decided_at, aid.decision_note = user, timezone.now(), note[:300]
        if to == "dibayar":
            aid.paid_at = paid_at or timezone.localdate()
        aid.save()
        return aid, before


# ---------------------------------------------------------------- Cuti hamil
def default_maternity_window(due_date):
    return due_date - timedelta(days=MATERNITY_DAYS_BEFORE), due_date + timedelta(days=MATERNITY_DAYS_AFTER)


def maternity_overlaps(employee, start, end, exclude_pk=None):
    qs = MaternityLeave.objects.filter(employee=employee).exclude(state="batal").filter(start_date__lte=end, end_date__gte=start)
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def overlapping_requests(employee, start, end):
    """Pengajuan izin/cuti/sakit yang masih berjalan pada rentang yang sama (untuk PERINGATAN, bukan pemblokiran)."""
    hits = []
    for r in ChangeRequest.objects.filter(employee=employee, type__in=("izin", "cuti", "sakit", "izin_khusus"), status__in=("submitted", "pending", "approved", "executed")):
        s, e = r.payload.get("start_date"), r.payload.get("end_date")
        if s and e and s <= end.isoformat() and e >= start.isoformat(): hits.append(r)
    return hits


def maternity_finish(leave_id, delivery_date):
    with transaction.atomic():
        m = MaternityLeave.objects.select_for_update().get(pk=leave_id)
        if m.state != "aktif": raise ValueError("Hanya cuti hamil berstatus aktif yang bisa diselesaikan.")
        if delivery_date and delivery_date > timezone.localdate(): raise ValueError("Tanggal lahir tidak boleh di masa depan.")
        before = {"state": m.state, "delivery_date": str(m.delivery_date) if m.delivery_date else None}
        m.state = "selesai"
        if delivery_date: m.delivery_date = delivery_date
        m.save()
        return m, before


def maternity_cancel(leave_id, reason):
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pembatalan wajib diisi.")
    with transaction.atomic():
        m = MaternityLeave.objects.select_for_update().get(pk=leave_id)
        if m.state != "aktif": raise ValueError("Hanya cuti hamil berstatus aktif yang bisa dibatalkan.")
        m.state, m.cancel_reason = "batal", reason[:300]
        m.save()
        return m


# ---------------------------------------------------------------- Katering
def catering_receive(order_id, received_large, received_small):
    from .models import CateringOrder
    with transaction.atomic():
        o = CateringOrder.objects.select_for_update().get(pk=order_id)
        if o.status != "dipesan": raise ValueError("Hanya pesanan berstatus 'Dipesan' yang bisa dikonfirmasi diterima.")
        before = {"status": o.status}
        o.status, o.received_large, o.received_small = "diterima", received_large, received_small
        o.save()
        return o, before


def catering_cancel(order_id, reason):
    from .models import CateringOrder
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pembatalan wajib diisi.")
    with transaction.atomic():
        o = CateringOrder.objects.select_for_update().get(pk=order_id)
        if o.status != "dipesan": raise ValueError("Hanya pesanan berstatus 'Dipesan' yang bisa dibatalkan.")
        o.status, o.note = "batal", f"Dibatalkan: {reason}"[:300]
        o.save()
        return o
