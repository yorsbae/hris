"""Aturan bisnis Poliklinik. Semua perubahan atomik dan baris dikunci (select_for_update); view/API hanya memanggil fungsi di sini."""
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from apps.core.models import Notification, Role, User
from apps.hr.models import Employee
from .models import (Diagnosis, DiagnosisMedicine, LetterCounter, MedicalRecord, Medicine, Prescription, PrescriptionReturn, RecordAddendum, Referral, StockMovement, dispense)

MAX_LINES = 20


def active_employee(pk=None, nik=None):
    """Karyawan yang boleh diperiksa: aktif dan belum di-soft-delete (Employee.objects sudah menyaring yang terhapus)."""
    qs = Employee.objects.select_related("department").filter(status="aktif")
    try: return qs.get(pk=pk) if pk is not None else qs.get(nik=nik)
    except (Employee.DoesNotExist, ValueError, TypeError): return None


def _notify_low_stock(meds):
    users = list(User.objects.filter(role=Role.POLI, is_active=True))
    for m in meds:
        Notification.objects.bulk_create([Notification(user=u, kind="stock", title=f"Stok minimum: {m.name} (sisa {m.stock} {m.unit})",
                                                       link=f"/poli/medicines/{m.pk}/") for u in users])


def create_record(user, employee, kind, complaint="", exam=None, diagnosis=None, treatment="", prescriptions=()):
    """Rekam medis + resep dalam SATU transaksi: stok kurang di resep mana pun → seluruh rekam medis dibatalkan.
    prescriptions: iterable (medicine_id, qty:int, dosage). Mengembalikan (record, [obat yang stoknya ≤ minimum])."""
    if employee is None: raise ValueError("Karyawan tidak ditemukan atau tidak aktif")
    if kind not in dict(MedicalRecord.KINDS): raise ValueError("Jenis kunjungan tidak valid")
    if kind == "kehamilan" and employee.gender != "P": raise ValueError("Pemeriksaan kehamilan hanya untuk karyawan perempuan")
    lines = list(prescriptions)
    if len(lines) > MAX_LINES: raise ValueError(f"Maksimal {MAX_LINES} obat per resep")
    ids = [l[0] for l in lines]
    if len(set(ids)) != len(ids): raise ValueError("Obat yang sama muncul lebih dari sekali dalam satu resep")
    with transaction.atomic():
        r = MedicalRecord.objects.create(employee=employee, kind=kind, visit_at=timezone.now(), complaint=complaint, exam=exam or {},
                                         diagnosis=diagnosis, treatment=treatment, created_by=user)
        low = []
        for mid, qty, dosage in lines:
            m = dispense(mid, qty, user, ref=f"MR{r.pk}")
            Prescription.objects.create(record=r, medicine=m, qty=qty, dosage=dosage or "")
            if m.stock <= m.min_stock: low.append(m)
        if low: _notify_low_stock(low)
    return r, low


def _move(med_id, delta, user, reason, ref="", note=""):
    with transaction.atomic():
        try: m = Medicine.objects.select_for_update().get(pk=med_id)
        except Medicine.DoesNotExist: raise ValueError("Obat tidak ditemukan")
        if m.stock + delta < 0: raise ValueError(f"Stok {m.name} tidak boleh negatif (stok saat ini {m.stock})")
        m.stock = F("stock") + delta; m.save(update_fields=["stock"]); m.refresh_from_db()
        mv = StockMovement.objects.create(medicine=m, qty=delta, balance_after=m.stock, reason=reason, ref=ref[:40], note=note[:300], created_by=user)
    return m, mv


def stock_in(med_id, qty, user, ref="", note=""):
    """Penerimaan obat (pembelian/hibah). Satu-satunya cara menambah stok selain penyesuaian."""
    if not isinstance(qty, int) or qty <= 0: raise ValueError("Jumlah masuk harus bilangan bulat positif")
    return _move(med_id, qty, user, "purchase", ref, note)


def stock_adjust(med_id, delta, user, note):
    """Koreksi stok (hasil opname, rusak, kedaluwarsa): ± , alasan wajib; saldo tidak boleh negatif."""
    if not isinstance(delta, int) or delta == 0: raise ValueError("Penyesuaian tidak boleh 0")
    if not (note or "").strip(): raise ValueError("Alasan penyesuaian wajib diisi")
    return _move(med_id, delta, user, "adjustment", "", note.strip())


def add_addendum(user, record, note):
    note = (note or "").strip()
    if not note: raise ValueError("Catatan tambahan tidak boleh kosong")
    return RecordAddendum.objects.create(record=record, note=note, created_by=user)


def create_referral(user, record, facility, note=""):
    facility = (facility or "").strip()
    if not facility: raise ValueError("Fasilitas tujuan wajib diisi")
    with transaction.atomic():
        MedicalRecord.objects.select_for_update().get(pk=record.pk)  # serialisasi per rekam medis: dua petugas bersamaan tidak lolos berdua
        if Referral.objects.filter(record=record, status__in=("diajukan", "dirujuk")).exists():
            raise ValueError("Rekam medis ini masih punya rujukan yang berjalan")
        return Referral.objects.create(number=LetterCounter.next_number("rjk", timezone.now()), record=record, facility=facility[:150],
                                       note=note or "", created_by=user)


def referral_transition(pk, to, user, outcome=""):
    """Transisi divalidasi terhadap Referral.FLOW di bawah kunci baris (klik ganda/dua petugas tidak memproses dua kali)."""
    with transaction.atomic():
        ref = Referral.objects.select_for_update().get(pk=pk)
        before = ref.status
        if to not in Referral.FLOW.get(before, ()): raise ValueError(f"Rujukan berstatus {ref.get_status_display()} tidak bisa diubah ke {dict(Referral.STATUSES).get(to, to)}")
        outcome = (outcome or "").strip()
        if to in ("selesai", "batal") and not outcome:
            raise ValueError("Hasil rujukan wajib diisi" if to == "selesai" else "Alasan pembatalan wajib diisi")
        ref.status = to
        if outcome: ref.outcome = outcome
        ref.save(update_fields=["status", "outcome"])
    return ref, before


# ---------------------------------------------------------------- Tambah / kurangi obat pada rekam medis (rekam medis tetap tidak diubah)
def add_prescription(user, record, medicine_id, qty, dosage=""):
    """Obat TAMBAHAN pada rekam medis yang sudah ada: baris resep baru (added_at terisi) + stok keluar, satu transaksi.
    Rekam medis dikunci dulu agar dua petugas tidak menambah bersamaan melewati batas baris."""
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0: raise ValueError("Jumlah obat harus bilangan bulat positif")
    with transaction.atomic():
        MedicalRecord.objects.select_for_update().get(pk=record.pk)
        if Prescription.objects.filter(record=record).count() >= MAX_LINES: raise ValueError(f"Maksimal {MAX_LINES} baris obat per rekam medis")
        m = dispense(medicine_id, qty, user, ref=f"MR{record.pk}")
        p = Prescription.objects.create(record=record, medicine=m, qty=qty, dosage=(dosage or "")[:100], added_at=timezone.now(), added_by=user)
        if m.stock <= m.min_stock: _notify_low_stock([m])
    return p, m


def return_prescription(user, prescription_id, qty, reason):
    """Kurangi obat (retur): jumlah ≤ sisa pada baris itu, alasan wajib; stok kembali lewat kartu stok (reason=return). Append-only."""
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0: raise ValueError("Jumlah retur harus bilangan bulat positif")
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pengurangan obat wajib diisi")
    with transaction.atomic():
        try: p = Prescription.objects.select_for_update().select_related("medicine").get(pk=prescription_id)
        except Prescription.DoesNotExist: raise ValueError("Baris resep tidak ditemukan")
        done = sum(r.qty for r in p.returns.all())
        left = p.qty - done
        if qty > left: raise ValueError(f"Retur {qty} melebihi sisa {left} {p.medicine.unit} pada baris resep ini")
        m, _ = _move(p.medicine_id, qty, user, "return", ref=f"MR{p.record_id}", note=reason[:300])
        ret = PrescriptionReturn.objects.create(prescription=p, qty=qty, reason=reason[:300], created_by=user)
    return ret, m


# ---------------------------------------------------------------- Master: obat lazim per diagnosa
def link_medicine(diagnosis, medicine_id, qty=1, dosage=""):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0: raise ValueError("Jumlah bawaan harus bilangan bulat positif")
    with transaction.atomic():
        d = Diagnosis.objects.select_for_update().get(pk=diagnosis.pk)  # serialisasi per diagnosa (batas jumlah + duplikat)
        try: m = Medicine.objects.get(pk=medicine_id)
        except Medicine.DoesNotExist: raise ValueError("Obat tidak ditemukan")
        n = d.medicine_links.count()
        if n >= 10: raise ValueError("Maksimal 10 obat per diagnosa")
        if d.medicine_links.filter(medicine=m).exists(): raise ValueError(f"{m.name} sudah ditautkan ke diagnosa ini")
        return DiagnosisMedicine.objects.create(diagnosis=d, medicine=m, qty=qty, dosage=(dosage or "")[:100], position=n)


def unlink_medicine(diagnosis, link_id):
    n, _ = DiagnosisMedicine.objects.filter(diagnosis=diagnosis, pk=link_id).delete()
    if not n: raise ValueError("Tautan tidak ditemukan")
