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
    def __str__(self): return self.name
class Shift(models.Model):  # master shift; penyesuaian per tanggal ada di ShiftAssignment
    name = models.CharField(max_length=50); start = models.TimeField(); end = models.TimeField()
    crosses_midnight = models.BooleanField(default=False)
    # Aturan Pengaturan Jadwal Shift 2026: kode (PAGI/SIANG/MALAM/GS-12/GS-14/GS-16), GS = general shift (tidak ikut rotasi kelompok), nonaktif = tidak ditawarkan lagi
    code = models.CharField("Kode", max_length=10, blank=True, default="")
    is_gs = models.BooleanField("General shift (GS)", default=False)
    active = models.BooleanField("Aktif", default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["code"], condition=~models.Q(code=""), name="uniq_shift_code_when_set")]
    def __str__(self): return self.name


class ShiftGroup(models.Model):
    """Kelompok rotasi (docs/jadwal_shift_2026.md). Pola 2 shift (Pagi–Siang) berkode A–G; pola 3 shift/PACK (Pagi–Siang–Malam) berkode A_pack–G_pack.
    Kedua pola adalah kelompok yang berbeda walau hurufnya sama."""
    P2, P3 = "2_SHIFT", "3_SHIFT"
    PATTERNS = [(P2, "2 shift (Pagi–Siang)"), (P3, "3 shift / PACK (Pagi–Siang–Malam)")]
    code = models.CharField("Kode kelompok", max_length=12, unique=True)
    pattern = models.CharField("Pola", max_length=8, choices=PATTERNS)
    class Meta:
        ordering = ["pattern", "code"]
        constraints = [models.CheckConstraint(name="shift_group_pack_suffix_matches_pattern",
                                              condition=(models.Q(pattern="3_SHIFT", code__endswith="_pack") | (models.Q(pattern="2_SHIFT") & ~models.Q(code__endswith="_pack"))))]
    def __str__(self): return self.code


class ShiftRotation(models.Model):
    """Satu sel tabel rotasi: kelompok X pada hari ke-N (0=Senin) → shift tertentu, atau libur (shift kosong). Satu baris per kelompok per hari."""
    group = models.ForeignKey(ShiftGroup, on_delete=models.CASCADE, related_name="rotation")
    weekday = models.PositiveSmallIntegerField()  # 0=Senin … 6=Minggu
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="+")  # null = libur
    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "weekday"], name="uniq_rotation_group_weekday"),
                       models.CheckConstraint(name="rotation_weekday_0_6", condition=models.Q(weekday__gte=0, weekday__lte=6))]
        ordering = ["group__pattern", "weekday", "group__code"]
    def __str__(self): return f"{self.group.code} · hari {self.weekday} → {self.shift.code or self.shift.name if self.shift_id else 'libur'}"
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.shift_id and self.group_id:
            if not self.shift.active: raise ValidationError({"shift": "Shift nonaktif tidak boleh dipakai di rotasi."})
            if self.shift.is_gs: raise ValidationError({"shift": "Shift GS tidak ikut rotasi kelompok."})
            if self.group.pattern == ShiftGroup.P2 and self.shift.crosses_midnight: raise ValidationError({"shift": "Pola 2 shift hanya Pagi–Siang; shift melewati tengah malam (Malam) hanya untuk pola 3 shift/PACK."})

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
    shift_group = models.ForeignKey("ShiftGroup", null=True, blank=True, on_delete=models.SET_NULL, related_name="members", verbose_name="Kelompok shift")
    # Karyawan GS: pada hari kerja SEBELUM hari libur GS (mis. Sabtu), jam pulang dipersingkat ke GS-14 (08–14) atau GS-12 (08–12). Bawaan 14.
    GS_SHORT = [("14", "GS-14 (08:00–14:00)"), ("12", "GS-12 (08:00–12:00)")]
    gs_short = models.CharField("GS sebelum libur", max_length=2, choices=GS_SHORT, default="14")
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
    # Perluasan atas VISION: "cancelled" (dibatalkan pemohon/HRD) agar draft tidak menggantung dan pengajuan yang tidak bisa
    # dilaksanakan (mis. tanggal tukar shift sudah lewat) tidak macet di "approved".
    FLOW = {"draft": {"submitted", "cancelled"}, "submitted": {"pending"}, "pending": {"approved", "rejected", "cancelled"},
            "approved": {"executed", "cancelled"}}
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

class ShiftAssignment(models.Model):
    """Penyesuaian jadwal per TANGGAL (hasil tukar shift/libur yang disetujui & dilaksanakan). Master `Employee.shift` tidak diubah
    (VISION: master shift tidak berubah lewat tukar). Jadwal efektif = penyesuaian bila ada, kalau tidak shift master (`apps/hr/schedule.py`).
    Append-only: tidak diubah/dihapus lewat model; satu baris per karyawan per tanggal."""
    SHIFT, WORK, OFF = "shift", "work", "off"
    KINDS = [(SHIFT, "Shift pengganti"), (WORK, "Masuk (shift reguler)"), (OFF, "Libur")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="schedule_overrides")
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=KINDS)
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="+")  # wajib hanya bila kind=shift
    request = models.ForeignKey("ChangeRequest", on_delete=models.PROTECT, related_name="schedule_rows")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="uniq_shift_assignment_employee_date"),
            models.CheckConstraint(name="shift_assignment_shift_iff_kind_shift",
                                   condition=(models.Q(kind="shift", shift__isnull=False) | models.Q(kind__in=["work", "off"], shift__isnull=True))),
        ]
        indexes = [models.Index(fields=["date", "employee"])]
    def save(self, *a, **k):
        if self.pk: raise PermissionError("Penyesuaian jadwal tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Penyesuaian jadwal tidak boleh dihapus")


class LeaveLedger(models.Model):
    """Kartu saldo cuti tahunan (seperti kartu stok obat): jatah (+), pemakaian (−), koreksi (±). Saldo = jumlah `days` per karyawan per tahun.
    Append-only; kesalahan dikoreksi dengan baris `adjust`, bukan dengan mengubah baris lama. Tabel terpisah dari Employee."""
    GRANT, USE, ADJUST = "grant", "use", "adjust"
    KINDS = [(GRANT, "Jatah tahunan"), (USE, "Pemakaian cuti"), (ADJUST, "Koreksi")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_ledger")
    year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10, choices=KINDS)
    days = models.DecimalField(max_digits=5, decimal_places=1)  # bertanda: jatah/koreksi bisa +/−, pemakaian selalu −
    request = models.ForeignKey("ChangeRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="leave_rows")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-year", "-id"]
        constraints = [
            # satu pengajuan hanya boleh memotong saldo sekali per tahun (idempoten terhadap klik ganda / retry)
            models.UniqueConstraint(fields=["request", "year", "kind"], condition=models.Q(request__isnull=False), name="uniq_leave_request_year_kind"),
            # jatah tahunan tidak boleh ganda untuk karyawan+tahun yang sama (perintah grant_annual_leave aman diulang)
            models.UniqueConstraint(fields=["employee", "year"], condition=models.Q(kind="grant"), name="uniq_leave_grant_employee_year"),
            models.CheckConstraint(name="leave_use_is_negative", condition=~models.Q(kind="use") | models.Q(days__lt=0)),
        ]
        indexes = [models.Index(fields=["employee", "year"])]
    def save(self, *a, **k):
        if self.pk: raise PermissionError("Kartu saldo cuti tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Kartu saldo cuti tidak boleh dihapus")


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


class AttendanceCheck(models.Model):
    """P7 Validasi kehadiran (putaran 26): HRD menanyakan 'karyawan X pada tanggal X' lewat Admin Departemen → dijawab → diverifikasi HRD (terkunci).
    Satu karyawan-tanggal = satu catatan aktif (indeks unik parsial; yang dibatalkan boleh ditanyakan ulang). Koreksi sesudah terkunci = AttendanceCheckEvent 'koreksi' (append-only)."""
    ANSWERS = [("hadir", "Hadir"), ("izin", "Izin"), ("sakit", "Sakit"), ("cuti", "Cuti"), ("alfa", "Alfa")]
    STATUSES = [("diminta", "Diminta"), ("dijawab", "Dijawab Admin"), ("dikembalikan", "Dikembalikan"), ("diverifikasi", "Diverifikasi HRD"), ("dibatalkan", "Dibatalkan")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance_checks")
    department = models.ForeignKey(Department, on_delete=models.PROTECT)  # snapshot untuk scope cepat (pola ChangeRequest)
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="diminta", db_index=True)
    question = models.CharField(max_length=300, blank=True)   # catatan HRD kepada Admin
    hint = models.CharField(max_length=200, blank=True)       # dugaan awal (pengajuan Executed / jadwal libur); bukan jawaban
    due_date = models.DateField()                              # batas menjawab; lewat batas & belum dijawab = "terlambat" (dihitung, tidak disimpan)
    answer = models.CharField(max_length=10, choices=ANSWERS, blank=True)
    answer_note = models.CharField(max_length=300, blank=True)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"); answered_at = models.DateTimeField(null=True, blank=True)
    final_answer = models.CharField(max_length=10, choices=ANSWERS, blank=True)  # hasil terverifikasi (bisa beda dari jawaban bila HRD mengubah, dengan catatan)
    verify_note = models.CharField(max_length=300, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"); verified_at = models.DateTimeField(null=True, blank=True)
    conflict = models.CharField(max_length=200, blank=True)    # mis. 'Alfa' bertabrakan dengan izin/cuti Executed
    cancel_reason = models.CharField(max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["department", "status", "date"])]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], condition=~models.Q(status="dibatalkan"), name="uniq_attcheck_active")]

    @property
    def result(self):
        """Hasil berlaku: koreksi terakhir bila ada, kalau tidak final_answer (hanya bermakna bila diverifikasi)."""
        if self.status != "diverifikasi": return ""
        c = self.events.filter(action="koreksi").order_by("-id").first()
        return c.value if c else self.final_answer

    def is_late(self, today=None):
        from datetime import date as _d
        return self.status in ("diminta", "dikembalikan") and self.due_date < (today or _d.today())


class AttendanceCheckEvent(models.Model):
    """Jejak append-only: minta/jawab/kembalikan/verifikasi/batal/koreksi. Tidak dapat diubah atau dihapus."""
    att = models.ForeignKey(AttendanceCheck, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=20)
    from_status = models.CharField(max_length=20, blank=True); to_status = models.CharField(max_length=20, blank=True)
    value = models.CharField(max_length=10, blank=True); note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Jejak validasi kehadiran tidak dapat diubah.")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Jejak validasi kehadiran tidak dapat dihapus.")
