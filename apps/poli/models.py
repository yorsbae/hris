from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from apps.hr.models import Employee

class Diagnosis(models.Model):
    code = models.CharField(max_length=10, unique=True); name = models.CharField(max_length=200); category = models.CharField(max_length=60, blank=True)
class Medicine(models.Model):
    code = models.CharField(max_length=20, unique=True); name = models.CharField(max_length=150, db_index=True)
    unit = models.CharField(max_length=20); stock = models.IntegerField(default=0); min_stock = models.IntegerField(default=0)
class StockMovement(models.Model):  # kartu stok: stok = hasil agregasi, setiap perubahan tercatat
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="movements")
    qty = models.IntegerField()  # + masuk, - keluar
    balance_after = models.IntegerField()
    reason = models.CharField(max_length=30)  # purchase, prescription, adjustment
    ref = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class MedicalRecord(models.Model):
    KINDS = [("berobat", "Berobat"), ("kecelakaan_kerja", "Kecelakaan kerja"), ("pemeriksaan", "Pemeriksaan"), ("kehamilan", "Pemeriksaan kehamilan")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="medical_records")
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    visit_at = models.DateTimeField(db_index=True)
    complaint = models.TextField(blank=True); exam = models.JSONField(default=dict)  # tensi, suhu, dll
    diagnosis = models.ForeignKey(Diagnosis, null=True, on_delete=models.PROTECT)
    treatment = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
class Prescription(models.Model):
    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="prescriptions")
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT); qty = models.PositiveIntegerField(); dosage = models.CharField(max_length=100, blank=True)

class Referral(models.Model):
    number = models.CharField(max_length=30, unique=True)
    record = models.ForeignKey(MedicalRecord, on_delete=models.PROTECT)
    facility = models.CharField(max_length=150); note = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="diajukan"); outcome = models.TextField(blank=True)

class LetterCounter(models.Model):  # nomor surat otomatis, per jenis per bulan
    kind = models.CharField(max_length=20); period = models.CharField(max_length=6); last = models.PositiveIntegerField(default=0)
    class Meta: unique_together = ("kind", "period")
    @classmethod
    def next_number(cls, kind, today):
        with transaction.atomic():
            c, _ = cls.objects.select_for_update().get_or_create(kind=kind, period=today.strftime("%Y%m"))
            c.last += 1; c.save()
            return f"{kind.upper()}/{c.period}/{c.last:04d}"
class SickLeaveLetter(models.Model):  # surat izin pulang
    number = models.CharField(max_length=30, unique=True)
    record = models.ForeignKey(MedicalRecord, on_delete=models.PROTECT); issued_at = models.DateTimeField(auto_now_add=True)

def dispense(medicine_id, qty, user, ref=""):
    """Obat keluar atomik; menolak stok negatif."""
    with transaction.atomic():
        m = Medicine.objects.select_for_update().get(pk=medicine_id)
        if m.stock < qty: raise ValueError(f"Stok {m.name} tidak cukup ({m.stock})")
        m.stock = F("stock") - qty; m.save(); m.refresh_from_db()
        StockMovement.objects.create(medicine=m, qty=-qty, balance_after=m.stock, reason="prescription", ref=ref, created_by=user)
        return m
