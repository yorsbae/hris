"""Aturan bisnis Poliklinik. Semua perubahan atomik dan baris dikunci (select_for_update); view/API hanya memanggil fungsi di sini."""
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from apps.core.models import Notification, Role, User
from apps.hr.models import Employee
from .models import (Diagnosis, DiagnosisMedicine, LetterCounter, MedicalRecord, Medicine, Prescription, PrescriptionReturn, PatientAllergy, RecordAddendum, Referral, SickLeaveLetter, StockMovement, dispense)

MAX_LINES = 20


def active_employee(pk=None, nik=None):
    """Karyawan yang boleh diperiksa: aktif dan belum di-soft-delete (Employee.objects sudah menyaring yang terhapus)."""
    qs = Employee.objects.select_related("department").filter(status="aktif")
    try: return qs.get(pk=pk) if pk is not None else qs.get(nik=nik)
    except (Employee.DoesNotExist, ValueError, TypeError): return None


def poli_department():
    """Departemen Poli (kode `settings.POLI_DEPARTMENT_CODE`, bawaan POL) atau None bila belum dibuat."""
    from apps.hr.models import Department
    return Department.objects.filter(code=settings.POLI_DEPARTMENT_CODE).first()


def poli_staff(nik=None):
    """Karyawan AKTIF departemen Poli (calon 'Pemeriksa'); `nik` → satu orang atau None. Dokter yang bukan karyawan tidak lewat sini (hanya nama)."""
    dept = poli_department()
    qs = Employee.objects.select_related("department", "position").filter(status="aktif", department=dept) if dept else Employee.objects.none()
    if nik is None: return qs
    return qs.filter(nik=nik).first()


def _notify_low_stock(meds):
    users = list(User.objects.filter(role=Role.POLI, is_active=True))
    for m in meds:
        Notification.objects.bulk_create([Notification(user=u, kind="stock", title=f"Stok minimum: {m.name} (sisa {m.stock} {m.unit})",
                                                       link=f"/poli/medicines/{m.pk}/") for u in users])


def create_record(user, employee, kind, complaint="", exam=None, diagnosis=None, treatment="", prescriptions=(), examiner=None, doctor_name="", allergy=None):
    """Rekam medis + resep dalam SATU transaksi: stok kurang di resep mana pun → seluruh rekam medis dibatalkan.
    prescriptions: iterable (medicine_id, qty:int, dosage). allergy: (alergen, reaksi, tingkat) bila petugas mencatat alergi baru pada kunjungan ini (putaran 47);
    alergi yang sudah tercatat tidak diulang. Mengembalikan (record, [obat yang stoknya ≤ minimum])."""
    if employee is None: raise ValueError("Karyawan tidak ditemukan atau tidak aktif")
    if kind not in dict(MedicalRecord.KINDS): raise ValueError("Jenis kunjungan tidak valid")
    if kind == "kehamilan" and employee.gender != "P": raise ValueError("Pemeriksaan kehamilan hanya untuk karyawan perempuan")
    doctor_name = (doctor_name or "").strip()
    if len(doctor_name) > 100: raise ValueError("Nama dokter maksimal 100 karakter")
    if examiner is not None and (examiner.status != "aktif" or poli_staff(examiner.nik) is None): raise ValueError("Pemeriksa harus karyawan aktif departemen Poli")
    lines = list(prescriptions)
    if len(lines) > MAX_LINES: raise ValueError(f"Maksimal {MAX_LINES} obat per resep")
    ids = [l[0] for l in lines]
    if len(set(ids)) != len(ids): raise ValueError("Obat yang sama muncul lebih dari sekali dalam satu resep")
    with transaction.atomic():
        r = MedicalRecord.objects.create(employee=employee, kind=kind, visit_at=timezone.now(), complaint=complaint, exam=exam or {},
                                         diagnosis=diagnosis, treatment=treatment, created_by=user, examiner=examiner, doctor_name=doctor_name)
        low = []
        for mid, qty, dosage in lines:
            m = dispense(mid, qty, user, ref=f"MR{r.pk}")
            Prescription.objects.create(record=r, medicine=m, qty=qty, dosage=dosage or "")
            if m.stock <= m.min_stock: low.append(m)
        if low: _notify_low_stock(low)
        if allergy and (allergy[0] or "").strip(): add_allergy(user, employee, *allergy)  # duplikat/tidak valid → ValueError → seluruh rekam medis dibatalkan
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


def record_signer(record):
    """(label, nama) penanda tangan surat Poli = PEMERIKSA pada rekam medis itu (putaran 47): dokter yang diisi → pemeriksa (karyawan Poli) → petugas Poli pembuat rekam medis.
    Dokter perusahaan di .env hanya menjadi isian awal form, bukan penanda tangan tetap."""
    if record.doctor_name: return "Dokter Pemeriksa,", record.doctor_name
    if record.examiner_id: return "Pemeriksa,", record.examiner.name
    u = record.created_by
    return "Petugas Poliklinik,", ((u.get_full_name() or u.get_username()) if u is not None else "")


def create_referral(user, record, facility, note="", purpose="", incident_date=None):
    """Rujukan + data untuk cetak: jenis (kontrol/berobat/tes lab/spesialis) dan tanggal kejadian/keluhan (bawaan: tanggal kunjungan). Penanda tangan disalin dari pemeriksa."""
    facility = (facility or "").strip()
    if not facility: raise ValueError("Fasilitas tujuan wajib diisi")
    if purpose and purpose not in dict(Referral.PURPOSES): raise ValueError("Jenis rujukan tidak dikenal")
    today = timezone.localdate()
    incident_date = incident_date or timezone.localtime(record.visit_at).date()
    if incident_date > today: raise ValueError("Tanggal kejadian / keluhan tidak boleh di masa depan")
    with transaction.atomic():
        MedicalRecord.objects.select_for_update().get(pk=record.pk)  # serialisasi per rekam medis: dua petugas bersamaan tidak lolos berdua
        if Referral.objects.filter(record=record, status__in=("diajukan", "dirujuk")).exists():
            raise ValueError("Rekam medis ini masih punya rujukan yang berjalan")
        role, name = record_signer(record)
        return Referral.objects.create(number=LetterCounter.next_number("rjk", timezone.now()), record=record, facility=facility[:150], note=note or "", created_by=user,
                                       purpose=purpose or "", incident_date=incident_date, signer_role=role, signer_name=name[:100])


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


# ---------------------------------------------------------------- Surat Poli (putaran 47)
def add_months(d, n):
    """Tanggal `n` bulan setelah `d` (hari dipotong ke akhir bulan bila perlu: 31 Jan + 1 bulan = 28/29 Feb)."""
    import calendar
    from datetime import date
    y, m = divmod(d.month - 1 + n, 12); y += d.year; m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def create_letter(user, record, kind, go_home=False, start_date=None, days=None, months=None):
    """Surat keterangan menurut jenis; SATU per rekam medis per jenis (sudah ada → dikembalikan apa adanya). Mengembalikan (surat, dibuat_baru).
    istirahat: pulang (go_home → hari itu juga, 1 hari) atau istirahat/cuti `days` hari mulai `start_date`.
    izin_hamil (Surat Keterangan Cuti Hamil): hanya untuk kunjungan kehamilan; `months` bulan mulai `start_date` (bawaan 3). Penanda tangan disalin dari pemeriksa."""
    from datetime import timedelta
    if kind not in SickLeaveLetter.NEW_KINDS: raise ValueError("Jenis surat tidak dikenal")
    if kind == "izin_hamil" and record.kind != "kehamilan": raise ValueError("Surat cuti hamil hanya untuk kunjungan jenis kehamilan")
    with transaction.atomic():
        MedicalRecord.objects.select_for_update().get(pk=record.pk)
        old = SickLeaveLetter.objects.filter(record=record, kind=kind).first()
        if old: return old, False
        extra = {}
        if kind == "istirahat":
            if go_home: extra = {"go_home": True, "start_date": timezone.localdate(), "days": 1}
            else:
                if not start_date or not days: raise ValueError("Tanggal mulai dan lama istirahat wajib diisi")
                if not (1 <= days <= 30): raise ValueError("Istirahat / cuti maksimal 30 hari; lebih lama perlu rujukan/pengajuan cuti sakit")
                extra = {"start_date": start_date, "days": days}
        else:
            months = months or 3
            if not start_date: raise ValueError("Tanggal mulai cuti hamil wajib diisi")
            if not (1 <= months <= 6): raise ValueError("Lama cuti hamil 1–6 bulan")
            extra = {"start_date": start_date, "months": months, "days": (add_months(start_date, months) - start_date).days}
        role, name = record_signer(record)
        letter = SickLeaveLetter.objects.create(record=record, kind=kind, number=LetterCounter.next_number(SickLeaveLetter.COUNTER[kind], timezone.now()),
                                                signer_role=role, signer_name=name[:100], **extra)
    return letter, True


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


# ---------------------------------------------------------------- Tagihan Mitra (putaran 24, P6)
def employee_has_bpjs(employee):
    """BPJS Kesehatan karyawan: status HRD (Operasional HRD → BPJS) bila ada; bila belum pernah dicatat, nomor BPJS Kes di data karyawan (nomor terisi = punya)."""
    from apps.hrd.models import BpjsMembership
    m = BpjsMembership.objects.filter(employee=employee, scheme="kes").values_list("status", flat=True).first()
    if m is not None: return m == "aktif"
    return bool((employee.bpjs_kes or "").strip())


def split_bill(net, payer, has_bpjs, company_pct=None):
    """Pembagian beban tagihan aktif (setelah potongan) → (ditanggung perusahaan, ditanggung karyawan), jumlah keduanya SELALU = net.
    Penanggung 'perusahaan': karyawan ber-BPJS → perusahaan `company_pct`% (bawaan 75) dan karyawan sisanya (25%); tanpa BPJS → 100% perusahaan.
    Penanggung 'karyawan' → 100% karyawan. Penanggung 'bpjs' (dibayar BPJS) → tidak membebani keduanya (0/0). Sisa pembulatan jatuh ke karyawan agar total tepat."""
    from decimal import Decimal, ROUND_HALF_UP
    from django.conf import settings
    net = Decimal(net)
    if payer == "karyawan": return Decimal(0), net
    if payer == "bpjs": return Decimal(0), Decimal(0)
    if not has_bpjs: return net, Decimal(0)
    pct = Decimal(settings.POLI_BPJS_COMPANY_PCT if company_pct is None else company_pct)
    company = (net * pct / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return company, net - company


def create_partner_bill(user, partner, bill_number, bill_date, employee, service_date, service_type, complaint, diagnosis, payer, total, lines=(), discount=0):
    """Catat tagihan + rincian (opsional) + jejak awal dalam satu transaksi. `lines` = [(deskripsi, qty, harga_satuan)]. `discount` = potongan mitra (mis. potongan dokter RS):
    beban dibagi dari tagihan aktif = total − potongan (lihat split_bill); status BPJS karyawan disalin saat dicatat."""
    from decimal import Decimal
    from .models import PartnerBill, PartnerBillEvent, PartnerBillLine
    discount = Decimal(discount or 0)
    from apps.hr import periods
    if periods.is_closed(service_date): raise ValueError(f"Periode {periods.key(service_date)} sudah ditutup; tagihan tidak bisa dicatat. Minta Superadmin membuka kembali periode.")
    if discount < 0 or discount > Decimal(total): raise ValueError("Potongan tidak boleh negatif atau melebihi total tagihan.")
    with transaction.atomic():
        lines_total = sum((Decimal(q) * Decimal(p) for _, q, p in lines), Decimal(0))
        has_bpjs = employee_has_bpjs(employee); company, emp = split_bill(Decimal(total) - discount, payer, has_bpjs)
        b = PartnerBill.objects.create(partner=partner, bill_number=bill_number.strip(), bill_date=bill_date, employee=employee, service_date=service_date, service_type=service_type,
                                       complaint=(complaint or "").strip(), diagnosis=diagnosis, payer=payer, total_amount=total, lines_total=lines_total, created_by=user,
                                       discount_amount=discount, has_bpjs=has_bpjs, company_amount=company, employee_amount=emp)
        for desc, q, p in lines: PartnerBillLine.objects.create(bill=b, description=desc.strip(), qty=q, unit_price=p, amount=Decimal(q) * Decimal(p))
        PartnerBillEvent.objects.create(bill=b, from_status="", to_status="diterima", user=user)
        return b


def bill_transition(pk, to, user, note="", paid_date=None, payment_ref=""):
    """Perpindahan status di bawah kunci baris. Ditolak: alasan wajib. Dibayar: tanggal bayar (tidak di masa depan, tidak sebelum tanggal tagihan) + nomor bukti wajib.
    Pemisahan tugas (pembuat ≠ penyetuju) belum diberlakukan (A60); jejak `PartnerBillEvent` mencatat siapa melakukan apa."""
    from datetime import date as _d
    from .models import PartnerBill, PartnerBillEvent
    note = (note or "").strip()
    with transaction.atomic():
        b = PartnerBill.objects.select_for_update().get(pk=pk)
        before = b.status
        if to not in PartnerBill.FLOW.get(before, ()): raise ValueError(f"Tagihan berstatus {b.get_status_display()} tidak bisa diubah ke {dict(PartnerBill.STATUSES).get(to, to)}.")
        if to == "ditolak":
            if not note: raise ValueError("Alasan penolakan wajib diisi.")
            b.status_note = note[:300]
        if to == "dibayar":
            payment_ref = (payment_ref or "").strip()
            if not payment_ref: raise ValueError("Nomor bukti bayar wajib diisi.")
            if not paid_date: raise ValueError("Tanggal bayar wajib diisi.")
            if paid_date > _d.today(): raise ValueError("Tanggal bayar tidak boleh di masa depan.")
            if paid_date < b.bill_date: raise ValueError("Tanggal bayar tidak boleh sebelum tanggal tagihan.")
            b.paid_date, b.payment_ref = paid_date, payment_ref[:60]
        b.status = to; b.save(update_fields=["status", "status_note", "paid_date", "payment_ref"])
        PartnerBillEvent.objects.create(bill=b, from_status=before, to_status=to, note=(note or (f"bukti {b.payment_ref}" if to == "dibayar" else ""))[:300], user=user)
        return b, before


# Diagnosa cepat untuk jenis "pemeriksaan" (putaran 36): sehat / aman dengan catatan / tidak fit. Dibuat bila belum ada (tidak menimpa master yang sudah diubah Poli).
EXAM_DIAGNOSES = (("Z00.0", "Pemeriksaan kesehatan umum — sehat"), ("Z00.8", "Pemeriksaan kesehatan — aman dengan catatan"), ("Z02.7", "Surat keterangan medis — tidak fit sementara"))


def ensure_exam_diagnoses():
    for code, name in EXAM_DIAGNOSES: Diagnosis.objects.get_or_create(code=code, defaults={"name": name, "category": "Pemeriksaan"})


# ---------------------------------------------------------------- Alergi pasien (putaran 35)
def add_allergy(user, employee, substance, reaction="", severity="sedang"):
    substance = " ".join((substance or "").split())
    if not substance: raise ValueError("Alergen wajib diisi")
    if len(substance) > 100: raise ValueError("Alergen maksimal 100 karakter")
    if severity not in dict(PatientAllergy.SEVERITY): raise ValueError("Tingkat alergi tidak dikenal")
    if PatientAllergy.objects.filter(employee=employee, voided_at__isnull=True, substance__iexact=substance).exists():
        raise ValueError("Alergi ini sudah tercatat")
    return PatientAllergy.objects.create(employee=employee, substance=substance, reaction=" ".join((reaction or "").split())[:200], severity=severity, created_by=user)


def void_allergy(user, allergy):
    if allergy.voided_at: raise ValueError("Alergi sudah dinonaktifkan")
    allergy.voided_at = timezone.now(); allergy.voided_by = user; allergy.save(update_fields=["voided_at", "voided_by"])
    return allergy


def patient_overview(e):
    """Ringkasan medis untuk Poli saat NIK dipilih: alergi aktif, status + nomor BPJS K/TK, kunjungan terakhir & hitungan per jenis."""
    from django.db.models import Count
    from apps.hrd.models import BpjsMembership
    allergies = [{"id": a.pk, "substance": a.substance, "reaction": a.reaction, "severity": a.severity, "severity_label": a.get_severity_display()}
                 for a in e.allergies.filter(voided_at__isnull=True).order_by("-created_at")]
    ms = {m.scheme: m for m in BpjsMembership.objects.filter(employee=e)}
    bpjs = {}
    for k, label in (("kes", "BPJS Kesehatan (K)"), ("tk", "BPJS Ketenagakerjaan (TK)")):
        m = ms.get(k); has_no = bool(e.bpjs_kes if k == "kes" else e.bpjs_tk)
        bpjs[k] = {"label": label, "status": m.get_status_display() if m else "Belum dicatat", "active": bool(m and m.status == "aktif"), "number_on_file": has_no, "number": (e.bpjs_kes if k == "kes" else e.bpjs_tk) or ""}
    kinds = {r["kind"]: r["n"] for r in e.medical_records.values("kind").annotate(n=Count("id"))}
    return {"allergies": allergies, "bpjs": bpjs, "kinds": kinds}


def tenure_text(join_date, today=None):
    today = today or timezone.localdate(); m = (today.year - join_date.year) * 12 + today.month - join_date.month - (today.day < join_date.day)
    m = max(m, 0); y, mo = divmod(m, 12)
    return " ".join(x for x in (f"{y} tahun" if y else "", f"{mo} bulan" if mo else "") if x) or "< 1 bulan"


# ================================================================ Rekap visit pasien (putaran 46)
def create_visit(user, employee, kind, visit_date, complaint="", location="", rest_days=None, record=None):
    """Catat visit. Kunjungan rumah wajib berlokasi; jenis sakit boleh memuat hari istirahat. Karyawan harus aktif."""
    from .models import PatientVisit
    if employee.status != "aktif": raise ValueError("Karyawan tidak aktif.")
    if kind not in dict(PatientVisit.KINDS): raise ValueError("Jenis visit tidak dikenal.")
    location = " ".join((location or "").split())
    if kind == "rumah" and not location: raise ValueError("Kunjungan rumah wajib mengisi lokasi / alamat.")
    if rest_days is not None and kind != "sakit": rest_days = None
    return PatientVisit.objects.create(employee=employee, kind=kind, visit_date=visit_date, complaint=(complaint or "").strip(), location=location, rest_days=rest_days, record=record, created_by=user)


def visit_transition(pk, to, user, outcome=""):
    """berjalan → selesai (hasil wajib) / batal (alasan wajib). Baris dikunci agar dua petugas tidak menimpa satu sama lain. Mengembalikan (visit, status_lama)."""
    from .models import PatientVisit
    with transaction.atomic():
        v = PatientVisit.objects.select_for_update().get(pk=pk); before = v.status
        if to not in PatientVisit.FLOW.get(v.status, ()): raise ValueError(f"Visit berstatus {v.get_status_display()}; tidak bisa diubah ke {dict(PatientVisit.STATUSES).get(to, to)}.")
        outcome = (outcome or "").strip()
        if not outcome: raise ValueError("Hasil / tindak lanjut wajib diisi." if to == "selesai" else "Alasan pembatalan wajib diisi.")
        v.status, v.outcome = to, outcome; v.save(update_fields=["status", "outcome"])
    return v, before
