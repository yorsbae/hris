from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from apps.hr.models import Employee

class Diagnosis(models.Model):
    code = models.CharField(max_length=10, unique=True); name = models.CharField(max_length=200); category = models.CharField(max_length=60, blank=True)
    def __str__(self): return f"{self.code} {self.name}"
class Medicine(models.Model):
    code = models.CharField(max_length=20, unique=True); name = models.CharField(max_length=150, db_index=True)
    unit = models.CharField(max_length=20); stock = models.IntegerField(default=0); min_stock = models.IntegerField(default=0)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(stock__gte=0), name="medicine_stock_gte_0"),
                       models.CheckConstraint(condition=models.Q(min_stock__gte=0), name="medicine_min_stock_gte_0")]
    def __str__(self): return f"{self.code} {self.name}"
class DiagnosisMedicine(models.Model):
    """Master: obat yang LAZIM untuk sebuah diagnosa. Hanya saran: form kunjungan mengisi resep otomatis, petugas tetap boleh mengubah/menghapus."""
    diagnosis = models.ForeignKey(Diagnosis, on_delete=models.CASCADE, related_name="medicine_links")
    medicine = models.ForeignKey("Medicine", on_delete=models.CASCADE, related_name="diagnosis_links")
    qty = models.PositiveIntegerField("Jumlah bawaan", default=1)
    dosage = models.CharField("Aturan pakai bawaan", max_length=100, blank=True)
    position = models.PositiveSmallIntegerField("Urutan", default=0)
    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["diagnosis", "medicine"], name="uniq_diagnosis_medicine"),
                       models.CheckConstraint(name="diagnosis_medicine_qty_gt_0", condition=models.Q(qty__gt=0))]
class StockMovement(models.Model):  # kartu stok: stok = hasil agregasi, setiap perubahan tercatat
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="movements")
    qty = models.IntegerField()  # + masuk, - keluar
    balance_after = models.IntegerField()
    reason = models.CharField(max_length=30)  # purchase, prescription, return (retur resep), adjustment
    ref = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=300, blank=True)  # wajib untuk penyesuaian (alasan)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-id"]
    def save(self, *a, **k):  # kartu stok append-only (VISION: obat memakai kartu stok)
        if self.pk: raise PermissionError("Kartu stok tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Kartu stok tidak boleh dihapus")

class MedicalRecord(models.Model):
    KINDS = [("berobat", "Berobat"), ("kecelakaan_kerja", "Kecelakaan kerja"), ("pemeriksaan", "Pemeriksaan"), ("kehamilan", "Pemeriksaan kehamilan")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="medical_records")
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    visit_at = models.DateTimeField(db_index=True)
    complaint = models.TextField(blank=True); exam = models.JSONField(default=dict)  # tensi, suhu, dll
    diagnosis = models.ForeignKey(Diagnosis, null=True, on_delete=models.PROTECT)
    treatment = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    # Pemeriksa (putaran 34): karyawan departemen Poli (perawat/bidan/dst, dipilih lewat NIK) dan/atau dokter yang BUKAN karyawan (nama saja, tanpa NIK). Keduanya opsional.
    examiner = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="examined_records")
    doctor_name = models.CharField("Dokter", max_length=100, blank=True)
class RecordAddendum(models.Model):
    """Koreksi/tambahan atas rekam medis. Rekam medis tidak diubah/dihapus (histori); koreksi = catatan tambahan append-only."""
    record = models.ForeignKey(MedicalRecord, on_delete=models.PROTECT, related_name="addenda")
    note = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["created_at", "id"]
    def save(self, *a, **k):
        if self.pk: raise PermissionError("Catatan tambahan tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Catatan tambahan tidak boleh dihapus")

class Prescription(models.Model):
    """Baris resep. Tidak diubah: obat TAMBAHAN = baris baru (added_at terisi); obat DIKURANGI = PrescriptionReturn (retur, stok kembali)."""
    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="prescriptions")
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT); qty = models.PositiveIntegerField(); dosage = models.CharField(max_length=100, blank=True)
    added_at = models.DateTimeField(null=True, blank=True)  # terisi bila baris ditambahkan SESUDAH rekam medis dibuat
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

class PrescriptionReturn(models.Model):
    """Pengurangan obat pada resep (pasien tidak jadi memakai/salah beri). Append-only; stok kembali lewat kartu stok (reason=return)."""
    prescription = models.ForeignKey(Prescription, on_delete=models.PROTECT, related_name="returns")
    qty = models.PositiveIntegerField(); reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"); created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.CheckConstraint(name="prescription_return_qty_gt_0", condition=models.Q(qty__gt=0))]
    def save(self, *a, **k):
        if self.pk: raise PermissionError("Retur resep tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Retur resep tidak boleh dihapus")

class Referral(models.Model):
    number = models.CharField(max_length=30, unique=True)
    record = models.ForeignKey(MedicalRecord, on_delete=models.PROTECT)
    facility = models.CharField(max_length=150); note = models.TextField(blank=True)
    # diajukan → dirujuk (sudah dikirim ke fasilitas) → selesai (hasil wajib); diajukan/dirujuk → batal (alasan wajib di `outcome`)
    STATUSES = [("diajukan", "Diajukan"), ("dirujuk", "Dirujuk"), ("selesai", "Selesai"), ("batal", "Dibatalkan")]
    FLOW = {"diajukan": {"dirujuk", "batal"}, "dirujuk": {"selesai", "batal"}}
    status = models.CharField(max_length=20, choices=STATUSES, default="diajukan", db_index=True); outcome = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, null=True)

class LetterCounter(models.Model):  # nomor surat otomatis, per jenis per bulan
    kind = models.CharField(max_length=20); period = models.CharField(max_length=6); last = models.PositiveIntegerField(default=0)
    class Meta: unique_together = ("kind", "period")
    @classmethod
    def next_number(cls, kind, today):
        with transaction.atomic():
            c, _ = cls.objects.select_for_update().get_or_create(kind=kind, period=today.strftime("%Y%m"))
            c.last += 1; c.save()
            return f"{kind.upper()}/{c.period}/{c.last:04d}"
class SickLeaveLetter(models.Model):
    """Surat dari Poli. Satu per rekam medis per JENIS: izin pulang, izin libur (istirahat beberapa hari), izin hamil. Tanpa diagnosa di surat."""
    KINDS = [("izin_pulang", "Surat izin pulang"), ("izin_libur", "Surat izin libur / istirahat"), ("izin_hamil", "Surat izin hamil")]
    PURPOSES = [("kontrol", "Kontrol kehamilan"), ("ringan", "Keringanan tugas"), ("cuti_melahirkan", "Rekomendasi cuti melahirkan")]
    COUNTER = {"izin_pulang": "sip", "izin_libur": "sil", "izin_hamil": "sih"}
    number = models.CharField(max_length=30, unique=True)
    record = models.ForeignKey(MedicalRecord, on_delete=models.PROTECT); issued_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=20, choices=KINDS, default="izin_pulang")
    start_date = models.DateField(null=True, blank=True); days = models.PositiveSmallIntegerField(null=True, blank=True)  # izin_libur / izin_hamil
    purpose = models.CharField(max_length=20, blank=True, choices=PURPOSES)  # izin_hamil
    class Meta:
        constraints = [models.UniqueConstraint(fields=["record", "kind"], name="uniq_letter_record_kind"),
                       models.CheckConstraint(name="letter_days_range", condition=models.Q(days__isnull=True) | models.Q(days__gte=1, days__lte=365))]
    @property
    def end_date(self):
        from datetime import timedelta
        return self.start_date + timedelta(days=self.days - 1) if self.start_date and self.days else None

def dispense(medicine_id, qty, user, ref=""):
    """Obat keluar atomik; menolak qty ≤ 0 (qty negatif akan MENAMBAH stok) dan stok negatif."""
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0: raise ValueError("Jumlah obat harus bilangan bulat positif")
    with transaction.atomic():
        try: m = Medicine.objects.select_for_update().get(pk=medicine_id)
        except Medicine.DoesNotExist: raise ValueError("Obat tidak ditemukan")
        if m.stock < qty: raise ValueError(f"Stok {m.name} tidak cukup ({m.stock})")
        m.stock = F("stock") - qty; m.save(); m.refresh_from_db()
        StockMovement.objects.create(medicine=m, qty=-qty, balance_after=m.stock, reason="prescription", ref=ref, created_by=user)
        return m


# ---------------------------------------------------------------- Tagihan Mitra (putaran 24, P6)
# DATA MEDIS: tagihan memuat keluhan & diagnosa → hanya Poli/Superadmin (seperti seluruh /poli/). Keluhan TERENKRIPSI sejak awal (A42/A12);
# keluhan/diagnosa tidak pernah masuk audit (hanya penanda). Nominal tidak dicampur ke tabel karyawan/medis lain.
from apps.core.crypto import EncryptedTextField  # noqa: E402


class Partner(models.Model):
    """Mitra rekanan yang menagih perusahaan (RS, klinik, lab, apotek, optik). Dinonaktifkan, tidak dihapus."""
    KINDS = [("rs", "Rumah sakit"), ("klinik", "Klinik"), ("lab", "Laboratorium"), ("apotek", "Apotek"), ("optik", "Optik"), ("lainnya", "Lainnya")]
    name = models.CharField(max_length=150, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="klinik")
    contact = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta: ordering = ["name"]

    def __str__(self): return self.name


class PartnerBill(models.Model):
    """Tagihan satu mitra untuk satu pelayanan karyawan. Alur: diterima → diverifikasi → disetujui → dibayar; ditolak dari diterima/diverifikasi/disetujui (alasan wajib).
    Tidak ada ubah/hapus isi tagihan: salah catat = tolak (alasan) lalu catat ulang (nomor tagihan yang ditolak boleh dipakai lagi). Perpindahan status tercatat di `PartnerBillEvent`."""
    SERVICES = [("rawat_jalan", "Rawat jalan"), ("rawat_inap", "Rawat inap"), ("lab", "Laboratorium"), ("obat", "Obat / apotek"), ("kacamata", "Kacamata / optik"), ("lainnya", "Lainnya")]
    PAYERS = [("perusahaan", "Perusahaan"), ("bpjs", "BPJS"), ("karyawan", "Karyawan")]
    STATUSES = [("diterima", "Diterima"), ("diverifikasi", "Diverifikasi"), ("disetujui", "Disetujui bayar"), ("dibayar", "Dibayar"), ("ditolak", "Ditolak")]
    FLOW = {"diterima": {"diverifikasi", "ditolak"}, "diverifikasi": {"disetujui", "ditolak"}, "disetujui": {"dibayar", "ditolak"}}
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="bills")
    bill_number = models.CharField(max_length=40)
    bill_date = models.DateField()
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="partner_bills")
    service_date = models.DateField()
    service_type = models.CharField(max_length=12, choices=SERVICES, default="rawat_jalan")
    complaint = EncryptedTextField(blank=True)                                   # MEDIS, terenkripsi
    diagnosis = models.ForeignKey(Diagnosis, null=True, blank=True, on_delete=models.PROTECT, related_name="+")  # MEDIS
    payer = models.CharField(max_length=10, choices=PAYERS, default="perusahaan")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)          # sesuai tagihan mitra
    lines_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # jumlah rincian (0 bila tanpa rincian)
    status = models.CharField(max_length=12, choices=STATUSES, default="diterima", db_index=True)
    status_note = models.CharField(max_length=300, blank=True)                   # alasan ditolak
    paid_date = models.DateField(null=True, blank=True)
    payment_ref = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["partner", "bill_number"], condition=~models.Q(status="ditolak"), name="uniq_partner_bill_number_live"),
                       models.CheckConstraint(condition=models.Q(total_amount__gt=0, lines_total__gte=0), name="partner_bill_amounts")]
        indexes = [models.Index(fields=["service_date"]), models.Index(fields=["partner", "status"])]
        ordering = ["-service_date", "-id"]

    @property
    def mismatch(self): return bool(self.lines_total) and self.lines_total != self.total_amount

    def delete(self, *a, **k): raise PermissionError("Tagihan tidak boleh dihapus; tolak dengan alasan.")


class PartnerBillLine(models.Model):
    """Rincian biaya (opsional). Dibuat bersama tagihan; tidak diubah/dihapus."""
    bill = models.ForeignKey(PartnerBill, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=200)
    qty = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)               # = qty × unit_price (disalin)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=models.Q(qty__gte=1, unit_price__gte=0, amount__gte=0), name="partner_bill_line_valid")]


class PartnerBillEvent(models.Model):
    """Jejak perpindahan status tagihan. Append-only."""
    bill = models.ForeignKey(PartnerBill, on_delete=models.PROTECT, related_name="events")
    from_status = models.CharField(max_length=12, blank=True)
    to_status = models.CharField(max_length=12)
    note = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: ordering = ["id"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Jejak tagihan tidak boleh diubah")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Jejak tagihan tidak boleh dihapus")
