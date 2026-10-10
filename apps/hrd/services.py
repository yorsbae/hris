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


# ---------------------------------------------------------------- Seragam (putaran 23, P5)
def uniform_rate_for(gender, on_date):
    """Tarif yang berlaku untuk jenis kelamin pada tanggal itu (effective_from terbaru ≤ tanggal), atau None."""
    from .models import UniformRate
    return UniformRate.objects.filter(gender=gender, effective_from__lte=on_date).order_by("-effective_from").first()


def _period_valid(v):
    return bool(v) and len(v) == 7 and v[4] == "-" and v[:4].isdigit() and v[5:].isdigit() and 2000 <= int(v[:4]) <= 2100 and 1 <= int(v[5:]) <= 12


def void_uniform_purchase(pk, reason, user):
    """Batalkan pembelian dengan alasan (baris tidak dihapus; keluar dari rekap). Ditolak bila sudah dipotong dari gaji atau sudah batal."""
    from .models import UniformPurchase
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pembatalan wajib diisi.")
    with transaction.atomic():
        p = UniformPurchase.objects.select_for_update().get(pk=pk)
        if p.voided_at: raise ValueError("Pembelian ini sudah dibatalkan.")
        if p.deduction_status == "sudah": raise ValueError("Pembelian yang sudah dipotong dari gaji tidak dapat dibatalkan.")
        p.voided_at, p.voided_by, p.void_reason = timezone.now(), user, reason[:300]; p.save(update_fields=["voided_at", "voided_by", "void_reason"])
        stock_move(p.utype_id, p.size_id, "batal", p.quantity, timezone.localdate(), user, purchase=p, note=f"Pembelian #{p.pk} dibatalkan")   # stok kembali
        return p


def mark_uniform_deducted(pk, period, user):
    """Tandai sudah dipotong pada periode gaji tertentu. Satu arah (data gaji sudah terpakai): tidak ada 'batal tandai'."""
    from .models import UniformPurchase
    period = (period or "").strip()
    if not _period_valid(period): raise ValueError("Periode harus berformat YYYY-MM.")
    with transaction.atomic():
        p = UniformPurchase.objects.select_for_update().get(pk=pk)
        if p.voided_at: raise ValueError("Pembelian yang dibatalkan tidak dapat ditandai dipotong.")
        if p.deduction_status == "sudah": raise ValueError("Sudah ditandai dipotong.")
        if period < p.purchase_date.strftime("%Y-%m"): raise ValueError("Periode potongan tidak boleh sebelum bulan pembelian.")
        p.deduction_status, p.deducted_period = "sudah", period; p.save(update_fields=["deduction_status", "deducted_period"])
        return p


# ---------------------------------------------------------------- Stok seragam (putaran 25)
STOCK_KINDS = {"masuk": 1, "batal": 1, "keluar": -1}   # tanda yang dipaksa; koreksi bebas (±, bukan 0)


def stock_move(utype_id, size_id, kind, quantity, when, user, purchase=None, note=""):
    """Satu-satunya pintu perubahan stok: kunci baris saldo, tulis baris kartu append-only dengan `balance_after`. `quantity` bertanda (keluar negatif).
    Boleh membuat saldo negatif (A63). Idempoten per (pembelian, jenis gerak) lewat constraint unik."""
    from .models import UniformStock, UniformStockMovement
    if kind not in ("masuk", "keluar", "batal", "koreksi"): raise ValueError("Jenis gerak stok tidak dikenal.")
    if not quantity: raise ValueError("Jumlah tidak boleh 0.")
    if kind in STOCK_KINDS and (quantity > 0) != (STOCK_KINDS[kind] > 0): raise ValueError("Tanda jumlah tidak sesuai jenis gerak stok.")
    with transaction.atomic():
        UniformStock.objects.get_or_create(utype_id=utype_id, size_id=size_id)
        st = UniformStock.objects.select_for_update().get(utype_id=utype_id, size_id=size_id)
        before = st.balance; st.balance += quantity; st.save(update_fields=["balance"])
        _notify_low_stock(st, before)
        return UniformStockMovement.objects.create(utype_id=utype_id, size_id=size_id, kind=kind, quantity=quantity, balance_after=st.balance,
                                                   movement_date=when, purchase=purchase, note=(note or "").strip()[:300], created_by=user)


def set_min_stock(utype_id, size_id, minimum):
    """Atur ambang minimum stok (bukan gerak stok → tidak masuk kartu; perubahan dicatat di audit oleh pemanggil). Mengembalikan (stok, nilai_lama)."""
    from .models import UniformStock
    if not isinstance(minimum, int) or not 0 <= minimum <= 100000: raise ValueError("Minimum stok harus bilangan 0–100000.")
    with transaction.atomic():
        UniformStock.objects.get_or_create(utype_id=utype_id, size_id=size_id)
        st = UniformStock.objects.select_for_update().get(utype_id=utype_id, size_id=size_id)
        old, st.min_stock = st.min_stock, minimum; st.save(update_fields=["min_stock"]); return st, old


def _notify_low_stock(st, before):
    """Notifikasi ke HRD saat saldo MENYEBERANG ke bawah minimum (sebelumnya ≥ minimum, sekarang < minimum; minimum 0 = tidak dipantau).
    Satu notifikasi belum-dibaca per jenis×ukuran: tidak menumpuk bila terus turun. Dipanggil di dalam transaksi stok_move."""
    from apps.core.models import Notification, Role, User
    if not (st.min_stock > 0 and before >= st.min_stock > st.balance): return
    title = f"Stok seragam menipis: {st.utype.name} {st.size.code} ({st.balance} pcs, minimum {st.min_stock})"
    have = set(Notification.objects.filter(kind="stock", is_read=False, title__startswith=f"Stok seragam menipis: {st.utype.name} {st.size.code} (").values_list("user_id", flat=True))
    Notification.objects.bulk_create([Notification(user=u, kind="stock", title=title, link="/hrd/uniforms/?tab=stok") for u in User.objects.filter(role=Role.HRD, is_active=True) if u.pk not in have])


# ---------------------------------------------------------------- Karyawan keluar (putaran 38)
SEPARATION_PHRASE = {"resign": "dan telah mengundurkan diri dari perusahaan", "habis_kontrak": "dan masa kontrak kerjanya telah berakhir", "phk": "dan hubungan kerjanya dengan perusahaan telah berakhir",
                     "pensiun": "dan telah memasuki masa pensiun", "meninggal": "dan telah meninggal dunia", "lainnya": "dan hubungan kerjanya dengan perusahaan telah berakhir"}


def service_period(join, last):
    """Masa kerja 'X tahun Y bulan' dari tanggal masuk sampai tanggal keluar (hari terakhir kerja dihitung)."""
    m = (last.year - join.year) * 12 + last.month - join.month - (last.day < join.day)
    y, mo = divmod(max(m, 0), 12)
    return " ".join(x for x in (f"{y} tahun" if y else "", f"{mo} bulan" if mo else "") if x) or "< 1 bulan"


def _apply_locked(s, user):
    """Nonaktifkan karyawan untuk catatan `s` (sudah dikunci pemanggil). Riwayat status ditulis (efektif = tanggal keluar). Idempoten: lewati bila sudah nonaktif."""
    from apps.hr.models import Employee, EmployeeHistory
    emp = Employee.all_objects.select_for_update().get(pk=s.employee_id)
    if emp.status != "nonaktif":
        old = emp.status; emp.status = "nonaktif"; emp.save(update_fields=["status"])
        EmployeeHistory.objects.create(employee=emp, field="status", old_value=old, new_value="nonaktif", effective_date=s.last_date, changed_by=user)
        s.status_changed, s.prev_status = True, old
    s.applied_at = timezone.now(); s.save(update_fields=["applied_at", "status_changed", "prev_status"])


def record_separation(emp, d, user):
    """Catat karyawan keluar. Identitas disalin. Bila hari keluar sudah lewat (sebelum hari ini) karyawan langsung dinonaktifkan; selain itu menunggu harinya
    (tombol 'Nonaktifkan sekarang' atau perintah `apply_separations`). Karyawan yang sudah nonaktif sebelumnya (arsip) dianggap sudah diterapkan."""
    from .models import Separation
    with transaction.atomic():
        s = Separation.objects.create(employee=emp, kind=d["kind"], request_date=d.get("request_date"), last_date=d["last_date"], reason=(d.get("reason") or "").strip(),
                                      emp_name=emp.name, department_name=emp.department.name, position_name=emp.position.name if emp.position_id else "", join_date=emp.join_date,
                                      tali_asih=d.get("tali_asih") or 0, tali_asih_note=(d.get("tali_asih_note") or "").strip(), created_by=user)
        if emp.status != "aktif": s.applied_at = timezone.now(); s.prev_status = emp.status; s.save(update_fields=["applied_at", "prev_status"])
        elif s.last_date < timezone.localdate(): _apply_locked(s, user)
        return s


def apply_separation(pk, user, force_today=True):
    """Nonaktifkan karyawan sekarang. Boleh pada hari keluar itu sendiri (force_today); sebelum hari keluar ditolak."""
    from .models import Separation
    with transaction.atomic():
        s = Separation.objects.select_for_update().get(pk=pk); today = timezone.localdate()
        if s.voided_at: raise ValueError("Catatan ini sudah dibatalkan.")
        if s.applied_at: raise ValueError("Karyawan sudah dinonaktifkan.")
        if s.last_date > today or (s.last_date == today and not force_today): raise ValueError(f"Belum waktunya: hari terakhir kerja {s.last_date:%d-%m-%Y}.")
        _apply_locked(s, user); return s


def apply_due_separations(user=None):
    """Untuk cron harian: nonaktifkan semua yang hari keluarnya sudah lewat (< hari ini). Mengembalikan jumlahnya."""
    from .models import Separation
    n = 0
    for pk in list(Separation.objects.filter(voided_at__isnull=True, applied_at__isnull=True, last_date__lt=timezone.localdate()).values_list("pk", flat=True)):
        try: apply_separation(pk, user, force_today=False); n += 1
        except ValueError: pass
    return n


def set_tali_asih(pk, amount, note, paid_on, user):
    """Tetapkan nominal tali asih (dan, bila sudah dibayar, tanggal bayarnya). Terkunci setelah ditandai dibayar. Mengembalikan (catatan, nilai_lama)."""
    from .models import Separation
    note = (note or "").strip()[:200]
    if not isinstance(amount, int) or not 0 <= amount <= 2_000_000_000: raise ValueError("Nominal tali asih harus 0 – 2.000.000.000.")
    with transaction.atomic():
        s = Separation.objects.select_for_update().get(pk=pk)
        if s.voided_at: raise ValueError("Catatan ini sudah dibatalkan.")
        if s.tali_asih_paid_on: raise ValueError("Tali asih sudah ditandai dibayar; tidak dapat diubah.")
        if paid_on:
            if amount <= 0: raise ValueError("Tali asih Rp 0 tidak perlu ditandai dibayar.")
            if paid_on > timezone.localdate(): raise ValueError("Tanggal bayar tidak boleh di masa depan.")
            if paid_on < s.join_date: raise ValueError("Tanggal bayar sebelum tanggal masuk karyawan.")
        old = {"tali_asih": s.tali_asih, "note": s.tali_asih_note}
        s.tali_asih, s.tali_asih_note, s.tali_asih_paid_on = amount, note, paid_on or None
        s.save(update_fields=["tali_asih", "tali_asih_note", "tali_asih_paid_on"]); return s, old


def issue_paklaring(pk, issue_date, user):
    """Terbitkan nomor paklaring (sekali). Syarat: karyawan sudah dinonaktifkan (sudah benar-benar keluar). Nomor = <pk 4 digit>/PAK/HRD/<bulan romawi>/<tahun terbit>."""
    from .models import Separation
    with transaction.atomic():
        s = Separation.objects.select_for_update().get(pk=pk)
        if s.voided_at: raise ValueError("Catatan ini sudah dibatalkan.")
        if s.paklaring_number: raise ValueError("Paklaring sudah diterbitkan.")
        if not s.applied_at: raise ValueError("Paklaring baru dapat diterbitkan setelah karyawan dinonaktifkan (hari terakhir kerja lewat).")
        if issue_date < s.last_date: raise ValueError("Tanggal terbit tidak boleh sebelum tanggal keluar.")
        if issue_date > timezone.localdate(): raise ValueError("Tanggal terbit tidak boleh di masa depan.")
        s.paklaring_number = f"{s.pk:04d}/PAK/HRD/{ROMAN[issue_date.month - 1]}/{issue_date.year}"; s.paklaring_date, s.paklaring_by = issue_date, user
        s.save(update_fields=["paklaring_number", "paklaring_date", "paklaring_by"]); return s


def void_separation(pk, reason, user):
    """Batalkan catatan (salah catat) dengan alasan. Ditolak bila paklaring sudah terbit atau tali asih sudah dibayar. Status karyawan yang dinonaktifkan oleh catatan ini dipulihkan."""
    from apps.hr.models import Employee, EmployeeHistory
    from .models import Separation
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pembatalan wajib diisi.")
    with transaction.atomic():
        s = Separation.objects.select_for_update().get(pk=pk)
        if s.voided_at: raise ValueError("Catatan ini sudah dibatalkan.")
        if s.paklaring_number: raise ValueError("Paklaring sudah diterbitkan; catatan tidak dapat dibatalkan.")
        if s.tali_asih_paid_on: raise ValueError("Tali asih sudah dibayar; catatan tidak dapat dibatalkan.")
        if s.status_changed:
            emp = Employee.all_objects.select_for_update().get(pk=s.employee_id)
            if emp.status == "nonaktif" and s.prev_status:
                emp.status = s.prev_status; emp.save(update_fields=["status"])
                EmployeeHistory.objects.create(employee=emp, field="status", old_value="nonaktif", new_value=s.prev_status, effective_date=timezone.localdate(), changed_by=user)
        s.voided_at, s.voided_by, s.void_reason = timezone.now(), user, reason[:300]; s.save(update_fields=["voided_at", "voided_by", "void_reason"]); return s
