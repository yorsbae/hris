"""Operasional HRD: bantuan (rekap), cuti hamil, pekerja harian proyek, katering (rekap), Surat Peringatan, status BPJS.
Semua tabel di sini terpisah dari tabel karyawan (nominal/jadwal tidak dicampur ke Employee)."""
from django.conf import settings
from django.db import models
from apps.hr.models import Department, Employee

USER = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------- BPJS
class BpjsScheme(models.TextChoices):
    KES = "kes", "BPJS Kesehatan (K)"
    TK = "tk", "BPJS Ketenagakerjaan (TK)"


class BpjsState(models.TextChoices):
    AKTIF = "aktif", "Aktif"
    NONAKTIF = "nonaktif", "Nonaktif"


class BpjsMembership(models.Model):
    """Status SAAT INI per karyawan per program (cepat untuk daftar 3.000+ karyawan). Perubahan selalu lewat services.set_bpjs_status."""
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="bpjs_memberships")
    scheme = models.CharField(max_length=3, choices=BpjsScheme.choices)
    status = models.CharField(max_length=10, choices=BpjsState.choices)
    effective_date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "scheme"], name="uniq_bpjs_employee_scheme")]
        indexes = [models.Index(fields=["scheme", "status"])]


class BpjsStatusLog(models.Model):
    """Histori perubahan status BPJS. Append-only (seperti AuditLog): tidak boleh diubah/dihapus lewat model."""
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="bpjs_logs")
    scheme = models.CharField(max_length=3, choices=BpjsScheme.choices)
    old_status = models.CharField(max_length=10, blank=True)
    new_status = models.CharField(max_length=10, choices=BpjsState.choices)
    effective_date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    changed_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Histori BPJS tidak boleh diubah")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Histori BPJS tidak boleh dihapus")


class BpjsDeduction(models.Model):
    """Potongan BPJS per karyawan, program, dan periode gaji (putaran 22, P4). Tabel SENDIRI — nominal tidak dicampur ke Employee.
    Satu baris per (karyawan, program, periode); impor ulang periode yang sama MENGGANTI nilai (jejak di audit ringkasan impor)."""
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="bpjs_deductions")
    scheme = models.CharField(max_length=3, choices=BpjsScheme.choices)
    period = models.CharField(max_length=7, help_text="YYYY-MM")
    employee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # porsi karyawan (dipotong dari gaji)
    employer_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # porsi perusahaan
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "scheme", "period"], name="uniq_bpjs_deduction"),
                       models.CheckConstraint(condition=models.Q(employee_amount__gte=0, employer_amount__gte=0), name="bpjs_deduction_nonneg")]
        indexes = [models.Index(fields=["period", "scheme"])]
        ordering = ["-period", "scheme", "employee_id"]


# ---------------------------------------------------------------- Seragam (putaran 23, P5)
class UniformType(models.Model):
    """Master jenis seragam (mis. 'Seragam Kerja'). Dinonaktifkan, tidak dihapus: pembelian lama tetap merujuknya."""
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta: ordering = ["name"]

    def __str__(self): return self.name


class UniformSize(models.Model):
    """Master ukuran (S, M, L, XL …). `sort` mengatur urutan tampil di rekap pesanan vendor."""
    code = models.CharField(max_length=10, unique=True)
    sort = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta: ordering = ["sort", "code"]

    def __str__(self): return self.code


class UniformRate(models.Model):
    """Tarif potongan gaji per satuan pembelian menurut jenis kelamin, berlaku sejak tanggal. Append-only: tarif baru = baris baru
    (tidak ada ubah/hapus); yang berlaku untuk suatu pembelian = baris dengan `effective_from` terbaru yang ≤ tanggal pembelian. Nilainya DISALIN ke pembelian."""
    GENDERS = [("L", "Laki-laki"), ("P", "Perempuan")]
    gender = models.CharField(max_length=1, choices=GENDERS)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    effective_from = models.DateField()
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["gender", "effective_from"], name="uniq_uniform_rate"),
                       models.CheckConstraint(condition=models.Q(amount__gte=0), name="uniform_rate_nonneg")]
        ordering = ["gender", "-effective_from"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Tarif seragam tidak boleh diubah; tambahkan tarif baru dengan tanggal berlaku.")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Tarif seragam tidak boleh dihapus.")


class UniformPurchase(models.Model):
    """Pembelian seragam karyawan = sumber komponen Potongan → Seragam di Payroll kelak. Tabel SENDIRI (tidak di Employee).
    Tarif, jenis kelamin, dan total potongan DISALIN saat dicatat → perubahan tarif/data karyawan tidak mengubah transaksi lama.
    Tidak ada ubah/hapus: koreksi = pembatalan beralasan (`voided_*`); baris batal tetap tampil tetapi tidak masuk rekap dan tidak lagi menghalangi pencatatan ulang."""
    class Status(models.TextChoices):
        BELUM = "belum", "Belum dipotong"
        SUDAH = "sudah", "Sudah dipotong"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="uniform_purchases")
    purchase_date = models.DateField()
    utype = models.ForeignKey(UniformType, on_delete=models.PROTECT, related_name="purchases")
    size = models.ForeignKey(UniformSize, on_delete=models.PROTECT, related_name="purchases")
    gender = models.CharField(max_length=1, choices=UniformRate.GENDERS)       # salinan jenis kelamin karyawan saat dicatat
    quantity = models.PositiveSmallIntegerField()
    rate_amount = models.DecimalField(max_digits=14, decimal_places=2)         # salinan tarif per satuan
    deduction_amount = models.DecimalField(max_digits=14, decimal_places=2)    # = rate_amount × quantity (disalin; A40)
    deduction_status = models.CharField(max_length=5, choices=Status.choices, default=Status.BELUM)
    deducted_period = models.CharField(max_length=7, blank=True, help_text="YYYY-MM periode gaji saat dipotong")
    note = models.CharField(max_length=300, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "purchase_date", "utype", "size"], condition=models.Q(voided_at__isnull=True), name="uniq_uniform_purchase_active"),
                       models.CheckConstraint(condition=models.Q(quantity__gte=1, rate_amount__gte=0, deduction_amount__gte=0), name="uniform_purchase_valid")]
        indexes = [models.Index(fields=["purchase_date"]), models.Index(fields=["deduction_status"])]
        ordering = ["-purchase_date", "-id"]

    @property
    def is_void(self): return self.voided_at is not None

    def delete(self, *a, **k): raise PermissionError("Pembelian seragam tidak boleh dihapus; batalkan dengan alasan.")


# ---------------------------------------------------------------- Stok seragam (putaran 25)
class UniformStock(models.Model):
    """Saldo berjalan per jenis × ukuran (satu baris per pasangan; dikunci select_for_update saat berubah). Boleh NEGATIF: pembelian dicatat sesudah barang diambil,
    jadi saldo minus = peringatan 'stok belum dicatat masuk', bukan penolakan (A63). Hanya berubah lewat `services.stock_move` → selalu ada baris kartu."""
    utype = models.ForeignKey(UniformType, on_delete=models.PROTECT, related_name="stocks")
    size = models.ForeignKey(UniformSize, on_delete=models.PROTECT, related_name="stocks")
    balance = models.IntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["utype", "size"], name="uniq_uniform_stock")]
        ordering = ["utype__name", "size__sort", "size__code"]


class UniformStockMovement(models.Model):
    """Kartu stok seragam, APPEND-ONLY (seperti kartu stok obat). masuk (+, barang masuk dari vendor), keluar (−, otomatis dari pembelian),
    batal (+, otomatis saat pembelian dibatalkan), koreksi (±, beralasan). `quantity` bertanda; `balance_after` = saldo sesudah baris ini."""
    class Kind(models.TextChoices):
        MASUK = "masuk", "Barang masuk"
        KELUAR = "keluar", "Keluar (pembelian)"
        BATAL = "batal", "Kembali (pembelian batal)"
        KOREKSI = "koreksi", "Koreksi stok"

    utype = models.ForeignKey(UniformType, on_delete=models.PROTECT, related_name="movements")
    size = models.ForeignKey(UniformSize, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    quantity = models.IntegerField()
    balance_after = models.IntegerField()
    movement_date = models.DateField()
    purchase = models.ForeignKey(UniformPurchase, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_movements")
    note = models.CharField(max_length=300, blank=True, help_text="Vendor / no. surat jalan / alasan koreksi")
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~models.Q(quantity=0), name="uniform_move_nonzero"),
                       models.UniqueConstraint(fields=["purchase", "kind"], condition=models.Q(purchase__isnull=False), name="uniq_uniform_move_purchase_kind")]
        indexes = [models.Index(fields=["utype", "size", "movement_date"])]
        ordering = ["-movement_date", "-id"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Kartu stok seragam tidak boleh diubah; buat koreksi.")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Kartu stok seragam tidak boleh dihapus.")


# ---------------------------------------------------------------- Bantuan
class Aid(models.Model):
    """Bantuan kepada karyawan — REKAPAN saja (putaran 20): tidak ada status/alur persetujuan; hanya dicatat siapa, jenis, tanggal, nominal."""
    KINDS = [("kematian", "Kematian keluarga"), ("pernikahan", "Pernikahan"), ("kelahiran", "Kelahiran"), ("musibah", "Musibah (bencana/kebakaran)"),
             ("rawat_inap", "Rawat inap"), ("pendidikan", "Pendidikan"), ("lainnya", "Lainnya")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="aids")
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    event_date = models.DateField()
    amount = models.PositiveBigIntegerField(default=0, help_text="Rupiah")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_date", "-id"]
        indexes = [models.Index(fields=["employee", "kind", "event_date"])]


# ---------------------------------------------------------------- Cuti hamil
class MaternityLeave(models.Model):
    """Jadwal & status administratif saja. TIDAK ada data medis (itu milik modul Poli dan tertutup untuk HRD)."""
    STATES = [("aktif", "Aktif"), ("selesai", "Selesai"), ("batal", "Dibatalkan")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="maternity_leaves")
    due_date = models.DateField("Perkiraan lahir (HPL)")
    start_date = models.DateField(); end_date = models.DateField()
    delivery_date = models.DateField("Tanggal lahir sebenarnya", null=True, blank=True)
    state = models.CharField(max_length=10, choices=STATES, default="aktif", db_index=True)
    note = models.CharField(max_length=500, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [models.Index(fields=["state", "start_date", "end_date"])]

    def phase(self, today):
        if self.state == "selesai": return "Selesai"
        if self.state == "batal": return "Dibatalkan"
        if today < self.start_date: return "Belum mulai"
        if today <= self.end_date: return "Sedang cuti"
        return "Lewat masa cuti (tandai selesai)"


# ---------------------------------------------------------------- Proyek & kerja harian
class Project(models.Model):
    STATUSES = [("aktif", "Aktif"), ("ditunda", "Ditunda"), ("selesai", "Selesai")]
    code = models.CharField(max_length=30, unique=True); name = models.CharField(max_length=150)
    location = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(); end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="aktif", db_index=True)
    note = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: ordering = ["-start_date", "code"]
    def __str__(self): return f"{self.code} {self.name}"


class ProjectWork(models.Model):
    """Pekerja harian proyek yang BUKAN karyawan: 'hari Senin A mengerjakan X, upah Rp O'. Satu baris = satu orang pada satu hari.
    Hanya rekapan; belum terhubung ke Payroll (nama pekerja diketik, tidak ada tabel pekerja terpisah)."""
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="works")
    work_date = models.DateField(db_index=True)
    worker_name = models.CharField(max_length=100)
    activity = models.CharField(max_length=300)
    wage = models.PositiveIntegerField(default=0, help_text="Rupiah (upah hari itu)")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "worker_name", "-id"]
        indexes = [models.Index(fields=["project", "work_date"]), models.Index(fields=["project", "worker_name"])]


# ---------------------------------------------------------------- Katering
class CateringOrder(models.Model):
    """Rekap katering (putaran 20): hanya pesan & diterima. Tanpa departemen, snack, harga, vendor, atau status.
    Waktu makan: 09:00, 12:00, 18:00, dan 02:00 (dini hari; dihitung ke tanggal kerja shift malam). Dua ukuran: tepak besar & kecil."""
    MEALS = [("0900", "09:00"), ("1200", "12:00"), ("1800", "18:00"), ("0200", "02:00")]
    date = models.DateField(db_index=True)
    meal = models.CharField(max_length=4, choices=MEALS)
    qty_large = models.PositiveIntegerField("Tepak besar (dipesan)", default=0)
    qty_small = models.PositiveIntegerField("Tepak kecil (dipesan)", default=0)
    received_large = models.PositiveIntegerField("Tepak besar (diterima)", null=True, blank=True)
    received_small = models.PositiveIntegerField("Tepak kecil (diterima)", null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "meal", "id"]
        constraints = [models.UniqueConstraint(fields=["date", "meal"], name="uniq_catering_date_meal")]

    @property
    def received(self): return self.received_large is not None or self.received_small is not None


# ---------------------------------------------------------------- Surat Peringatan
class WarningLetter(models.Model):
    """Surat Peringatan (SP1-SP3). Tidak dihapus: salah terbit = DICABUT dengan alasan (tercatat). Masa berlaku bawaan 6 bulan."""
    LEVELS = [(1, "SP 1"), (2, "SP 2"), (3, "SP 3")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="warning_letters")
    number = models.CharField(max_length=40, blank=True, unique=True)
    level = models.PositiveSmallIntegerField(choices=LEVELS)
    issue_date = models.DateField()
    valid_until = models.DateField()
    violation = models.CharField("Pelanggaran", max_length=200)
    description = models.TextField(blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoke_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [models.Index(fields=["employee", "level"])]
        constraints = [models.CheckConstraint(name="warning_level_1_3", condition=models.Q(level__gte=1, level__lte=3)),
                       models.CheckConstraint(name="warning_valid_after_issue", condition=models.Q(valid_until__gte=models.F("issue_date")))]

    def state(self, today):
        if self.revoked_at: return "dicabut"
        if today < self.issue_date: return "belum berlaku"
        return "aktif" if today <= self.valid_until else "kedaluwarsa"
