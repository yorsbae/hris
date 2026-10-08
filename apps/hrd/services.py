"""Aturan bisnis Operasional HRD. View hanya memanggil fungsi di sini; perubahan status selalu atomik dan divalidasi di server."""
from datetime import timedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.hr.models import ChangeRequest
from .models import BpjsMembership, BpjsStatusLog, MaternityLeave

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


# ---------------------------------------------------------------- Surat Peringatan
def add_months(d, n):
    """Tambah n bulan; tanggal 31 dipotong ke akhir bulan tujuan."""
    import calendar
    m = d.month - 1 + n; y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def issue_warning(employee, level, issue_date, valid_until, violation, description, user):
    """Terbitkan SP; nomor = <pk 4 digit>/SP-<tingkat>/HRD/<bulan romawi>/<tahun> (pk menjamin unik tanpa penghitung terpisah)."""
    from .models import WarningLetter
    with transaction.atomic():
        w = WarningLetter.objects.create(employee=employee, level=level, issue_date=issue_date, valid_until=valid_until, violation=violation.strip(),
                                         description=description.strip(), created_by=user, number=f"TMP-{timezone.now().timestamp()}")
        w.number = f"{w.pk:04d}/SP-{level}/HRD/{ROMAN[issue_date.month - 1]}/{issue_date.year}"; w.save(update_fields=["number"])
        return w


def revoke_warning(pk, reason, user):
    from .models import WarningLetter
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pencabutan wajib diisi.")
    with transaction.atomic():
        w = WarningLetter.objects.select_for_update().get(pk=pk)
        if w.revoked_at: raise ValueError("SP ini sudah dicabut.")
        w.revoked_at, w.revoked_by, w.revoke_reason = timezone.now(), user, reason[:300]; w.save()
        return w
