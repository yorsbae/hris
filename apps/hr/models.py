from django.conf import settings
import os, uuid
from django.db import models
from django.utils import timezone
from apps.core.crypto import EncryptedTextField

class Department(models.Model):
    code = models.CharField(max_length=20, unique=True); name = models.CharField(max_length=100)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)  # struktur organisasi
    def __str__(self): return self.name
class Position(models.Model):
    name = models.CharField(max_length=100); level = models.PositiveSmallIntegerField(default=0)
class Shift(models.Model):  # master shift; jadwal per karyawan ada di ShiftAssignment
    name = models.CharField(max_length=50); start = models.TimeField(); end = models.TimeField()
    crosses_midnight = models.BooleanField(default=False)

class EmployeeManager(models.Manager):
    """Default: hanya karyawan yang belum dihapus (soft delete). Gunakan Employee.all_objects untuk semuanya."""
    def get_queryset(self): return super().get_queryset().filter(deleted_at__isnull=True)

class Employee(models.Model):
    nik = models.CharField("NIK induk kerja", max_length=20, unique=True)
    nik_ktp = EncryptedTextField(max_length=16, blank=True)  # SENSITIF: terenkripsi di DB
    name = models.CharField(max_length=150, db_index=True)
    gender = models.CharField(max_length=1, choices=[("L", "L"), ("P", "P")])
    marital_status = models.CharField(max_length=20, blank=True)
    education = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True); phone = models.CharField(max_length=30, blank=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="employees")
    position = models.ForeignKey(Position, null=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default="aktif", db_index=True)  # tetap/kontrak/..., aktif/nonaktif
    join_date = models.DateField()
    supervisor = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL)
    bpjs_kes = EncryptedTextField(max_length=20, blank=True); bpjs_tk = EncryptedTextField(max_length=20, blank=True)  # SENSITIF
    npwp = EncryptedTextField(max_length=25, blank=True)  # SENSITIF
    bank_name = models.CharField(max_length=50, blank=True); bank_account = EncryptedTextField(max_length=30, blank=True)  # SENSITIF
    # Soft delete (VISION): data tidak dihapus fisik, hanya disembunyikan; NIK tetap terpakai.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    delete_reason = models.CharField(max_length=300, blank=True)
    objects = EmployeeManager(); all_objects = models.Manager()
    class Meta:
        indexes = [models.Index(fields=["department", "status"]), models.Index(fields=["name", "nik"])]
    SENSITIVE = ("nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account", "address", "phone")
    ENCRYPTED = ("nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_account")
    def __str__(self): return f"{self.nik} {self.name}"
    def soft_delete(self, user, reason):
        self.deleted_at, self.deleted_by, self.delete_reason = timezone.now(), user, reason[:300]
        self.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])
    def restore(self):
        self.deleted_at, self.deleted_by, self.delete_reason = None, None, ""
        self.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])

def doc_path(instance, filename):
    """Nama file di disk diacak (uuid): tidak bisa ditebak dan nama asli dari pengguna tidak pernah dipakai sebagai path."""
    return f"employee_docs/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()}"

class EmployeeDocument(models.Model):
    """Dokumen karyawan (KTP, ijazah, dll). Disajikan hanya lewat view ber-permission; hapus = soft delete (file tetap ada di disk)."""
    KINDS = [("ktp", "KTP"), ("kk", "Kartu Keluarga"), ("ijazah", "Ijazah"), ("npwp", "NPWP"), ("bpjs", "BPJS"), ("kontrak", "Dokumen kontrak"),
             ("sertifikat", "Sertifikat"), ("sp", "Surat peringatan"), ("lainnya", "Lainnya")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=20, choices=KINDS)
    title = models.CharField(max_length=150)
    file = models.FileField(upload_to=doc_path)
    original_name = models.CharField(max_length=200); size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    delete_reason = models.CharField(max_length=300, blank=True)
    class Meta: ordering = ["-uploaded_at"]

class EmployeeHistory(models.Model):  # riwayat perubahan; tidak pernah di-update/hapus
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="history")
    field = models.CharField(max_length=40)  # department, position, status, shift, contract
    old_value = models.CharField(max_length=200, blank=True); new_value = models.CharField(max_length=200)
    effective_date = models.DateField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    request = models.ForeignKey("ChangeRequest", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class Contract(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="contracts")
    number = models.CharField(max_length=50, unique=True); kind = models.CharField(max_length=30)
    start = models.DateField(); end = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, default="aktif", db_index=True)
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)  # rantai perpanjangan
    def __str__(self): return f"{self.number} ({self.kind}, {self.start:%d-%m-%Y} s/d {self.end:%d-%m-%Y})" if self.end else f"{self.number} ({self.kind}, sejak {self.start:%d-%m-%Y})"
REMINDER_DAYS = (90, 60, 30, 14, 7)  # dipakai management command check_contracts (cron harian)

class ChangeRequest(models.Model):
    """Satu tabel untuk mutasi/jabatan/izin/cuti/tukar shift/dst. Payload JSON per tipe."""
    TYPES = ["mutasi_dept", "mutasi_jabatan", "promosi", "demosi", "rotasi", "status", "shift",
             "izin", "cuti", "sakit", "izin_terlambat", "izin_pulang", "izin_khusus", "tukar_shift", "tukar_libur"]
    # Draft → Submitted → Pending Approval → Approved/Rejected → Executed
    FLOW = {"draft": {"submitted"}, "submitted": {"pending"}, "pending": {"approved", "rejected"}, "approved": {"executed"}}
    type = models.CharField(max_length=30, choices=[(t, t) for t in TYPES], db_index=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="requests")
    department = models.ForeignKey(Department, on_delete=models.PROTECT)  # snapshot untuk scope cepat
    status = models.CharField(max_length=20, default="draft", db_index=True)
    payload = models.JSONField(default=dict)  # mis. {"department_id": 3, "effective_date": "2026-11-01"}
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)

class Announcement(models.Model):
    kind = models.CharField(max_length=20)  # pengumuman/peraturan/pemberitahuan
    title = models.CharField(max_length=200); body = models.TextField()
    all_departments = models.BooleanField(default=False)
    departments = models.ManyToManyField(Department, blank=True)
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")  # admin tertentu
    attachment = models.FileField(upload_to="announcements/%Y/%m/", blank=True)  # disajikan lewat view ber-permission, bukan URL media langsung
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-created_at"]
class AnnouncementRead(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = ("announcement", "user")

# ---- Struktur siap Absensi/Payroll (belum ada logikanya) ----
class AttendanceRaw(models.Model):  # log mentah mesin fingerprint; immutable
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    device_id = models.CharField(max_length=40); punched_at = models.DateTimeField()
    class Meta:
        unique_together = ("employee", "device_id", "punched_at")
        indexes = [models.Index(fields=["employee", "punched_at"])]
class AttendanceDaily(models.Model):  # hasil olah: rekap harian, terlambat, lembur, alpa
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT); date = models.DateField()
    shift = models.ForeignKey(Shift, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20)  # hadir/alpa/izin/sakit/cuti
    late_minutes = models.PositiveIntegerField(default=0); overtime_minutes = models.PositiveIntegerField(default=0)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    class Meta: unique_together = ("employee", "date")
