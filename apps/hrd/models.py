"""Operasional HRD (VISION → 'Operasional HRD'): bantuan, cuti hamil, kerja harian proyek, katering, status BPJS.
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


# ---------------------------------------------------------------- Bantuan
class Aid(models.Model):
    KINDS = [("kematian", "Kematian keluarga"), ("pernikahan", "Pernikahan"), ("kelahiran", "Kelahiran"), ("musibah", "Musibah (bencana/kebakaran)"),
             ("rawat_inap", "Rawat inap"), ("pendidikan", "Pendidikan"), ("lainnya", "Lainnya")]
    STATUSES = [("diajukan", "Diajukan"), ("disetujui", "Disetujui"), ("ditolak", "Ditolak"), ("dibayar", "Dibayar")]
    FLOW = {"diajukan": {"disetujui", "ditolak"}, "disetujui": {"dibayar"}}  # ditolak & dibayar = final
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="aids")
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    event_date = models.DateField()
    amount = models.PositiveBigIntegerField(default=0, help_text="Rupiah")
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="diajukan", db_index=True)
    decision_note = models.CharField(max_length=300, blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateField(null=True, blank=True)
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


class ProjectDailyLog(models.Model):
    """Catatan 'hari X mengerjakan apa'. Boleh lebih dari satu catatan per proyek per hari (mis. regu berbeda)."""
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="logs")
    work_date = models.DateField(db_index=True)
    activity = models.TextField()
    workers = models.ManyToManyField(Employee, blank=True, related_name="project_logs")
    headcount = models.PositiveIntegerField(default=0, help_text="Terisi otomatis bila pekerja diisi lewat NIK")
    note = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "-id"]
        indexes = [models.Index(fields=["project", "work_date"])]


# ---------------------------------------------------------------- Katering
class CateringOrder(models.Model):
    """Pesanan katering per tanggal + waktu makan (+ departemen opsional) dalam dua ukuran: tepak besar & tepak kecil."""
    MEALS = [("sarapan", "Sarapan"), ("siang", "Makan siang"), ("malam", "Makan malam"), ("snack", "Snack / lembur")]
    STATUSES = [("dipesan", "Dipesan"), ("diterima", "Diterima"), ("batal", "Dibatalkan")]
    date = models.DateField(db_index=True)
    meal = models.CharField(max_length=10, choices=MEALS)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, help_text="Kosong = umum / semua departemen")
    qty_large = models.PositiveIntegerField("Tepak besar (dipesan)", default=0)
    qty_small = models.PositiveIntegerField("Tepak kecil (dipesan)", default=0)
    received_large = models.PositiveIntegerField(null=True, blank=True); received_small = models.PositiveIntegerField(null=True, blank=True)
    price_large = models.PositiveIntegerField("Harga tepak besar", default=0); price_small = models.PositiveIntegerField("Harga tepak kecil", default=0)
    vendor = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="dipesan", db_index=True)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "meal", "id"]
        indexes = [models.Index(fields=["date", "meal"])]

    @property
    def total_cost(self):
        """Biaya memakai jumlah DITERIMA bila sudah diterima, selain itu jumlah dipesan; batal = 0."""
        if self.status == "batal": return 0
        big = self.received_large if self.status == "diterima" and self.received_large is not None else self.qty_large
        small = self.received_small if self.status == "diterima" and self.received_small is not None else self.qty_small
        return big * self.price_large + small * self.price_small
