"""Payroll (Tahap 7, putaran 57). SEMUA data upah ada di tabel sendiri — tidak ada nominal gaji di `Employee` (prinsip VISION: "data payroll tidak dicampur ke tabel karyawan").

Prinsip yang diwarisi dari modul lain:
  • master berlaku-sejak bersifat APPEND-ONLY: perubahan = baris baru bertanggal; salah ketik = dibatalkan beralasan (`voided_*`), bukan diubah/dihapus;
  • data historis DISALIN saat dicatat (slip membawa nama, jabatan, grade, upah harian, parameter) → slip lama tidak bergeser bila master berubah;
  • transaksi uang tidak dihapus; run yang sudah dibayar terkunci, koreksi lewat penyesuaian di periode berikutnya.
Asumsi bisnis (belum dikonfirmasi pemilik produk) dicatat di docs/PROGRESS.md putaran 57 (A122–A137)."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.hr.models import Employee
from apps.hrd.models import Separation
from . import params as P
from .ter_data import STATUS_LABELS

USER = settings.AUTH_USER_MODEL


class Voidable(models.Model):
    """Dasar master/transaksi yang tidak dihapus: dibatalkan dengan alasan; baris batal diabaikan (dan tidak menghalangi baris pengganti)."""
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)

    class Meta: abstract = True

    @property
    def is_void(self): return self.voided_at is not None

    def delete(self, *a, **k): raise PermissionError("Data payroll tidak boleh dihapus; batalkan dengan alasan.")


# ---------------------------------------------------------------- master
class PayParam(Voidable):
    """Nilai parameter hitung berlaku-sejak (lihat params.py untuk daftar kunci dan nilai bawaan)."""
    key = models.CharField(max_length=30, choices=P.CHOICES)
    value = models.DecimalField(max_digits=14, decimal_places=4)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["key", "-effective_from", "-id"]
        constraints = [models.UniqueConstraint(fields=["key", "effective_from"], condition=models.Q(voided_at__isnull=True), name="uniq_payparam_active"),
                       models.CheckConstraint(condition=models.Q(value__gte=0), name="payparam_nonneg")]


class WageScale(Voidable):
    """Skala GAJI HARIAN per grade + sub-grade (mis. grade 9, tingkat A) berlaku-sejak. Boleh berdesimal: slip contoh Rp 2.534.426 ÷ 30 = Rp 84.480,87 per hari."""
    grade = models.PositiveSmallIntegerField()
    tier = models.CharField(max_length=2)
    daily_wage = models.DecimalField(max_digits=14, decimal_places=2)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["grade", "tier", "-effective_from", "-id"]
        constraints = [models.UniqueConstraint(fields=["grade", "tier", "effective_from"], condition=models.Q(voided_at__isnull=True), name="uniq_wagescale_active"),
                       models.CheckConstraint(condition=models.Q(daily_wage__gt=0), name="wagescale_positive")]

    @property
    def label(self): return f"{self.grade} {self.tier}"


class PayProfile(Voidable):
    """Profil gaji karyawan berlaku-sejak: grade + sub-grade (upah harian dari skala) atau upah harian khusus, tunjangan tetap, hak uang makan.
    Yang berlaku pada periode = baris tidak batal dengan `effective_from` terbesar ≤ akhir periode."""
    PAY_TYPES = [("harian", "Harian"), ("bulanan", "Bulanan")]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="pay_profiles")
    effective_from = models.DateField()
    grade = models.PositiveSmallIntegerField(null=True, blank=True)
    tier = models.CharField(max_length=2, blank=True)
    wage_override = models.DecimalField("Upah harian khusus", max_digits=14, decimal_places=2, null=True, blank=True)
    allowance_position = models.DecimalField(max_digits=14, decimal_places=0, default=0)    # T Jabatan (per bulan penuh)
    allowance_education = models.DecimalField(max_digits=14, decimal_places=0, default=0)   # T Pendidikan
    allowance_special = models.DecimalField(max_digits=14, decimal_places=0, default=0)     # T Khusus
    allowance_other = models.DecimalField(max_digits=14, decimal_places=0, default=0)       # T Lain-lain
    meal_allowance = models.BooleanField("Berhak uang makan", default=False)   # hak; nominal & kapan dibayar ditentukan master Uang Makan (periode puasa, tarif per tipe gaji/shift)
    pay_type = models.CharField("Tipe gaji", max_length=8, choices=PAY_TYPES, default="harian")
    ptkp_status = models.CharField("Status PTKP (pajak)", max_length=3, choices=[(k, v) for k, v in STATUS_LABELS.items()], default="TK0")   # putaran 60: kategori TER & PTKP PPh 21
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-effective_from", "-id"]
        constraints = [models.UniqueConstraint(fields=["employee", "effective_from"], condition=models.Q(voided_at__isnull=True), name="uniq_payprofile_active"),
                       models.CheckConstraint(condition=models.Q(allowance_position__gte=0, allowance_education__gte=0, allowance_special__gte=0, allowance_other__gte=0), name="payprofile_allowance_nonneg"),
                       models.CheckConstraint(condition=models.Q(grade__isnull=False) & ~models.Q(tier="") | models.Q(wage_override__isnull=False), name="payprofile_has_wage_source")]
        indexes = [models.Index(fields=["employee", "effective_from"])]

    @property
    def grade_label(self): return f"{self.grade} {self.tier}".strip() if self.grade is not None else "khusus"


class TerRate(Voidable):
    """Master Tarif Efektif Bulanan PPh 21 (TER): satu baris per kategori (A/B/C) per batas atas bruto. Berlaku-sejak: aturan baru = kumpulan baris baru bertanggal (yang lama tidak diubah).
    Awal diisi migrasi dari PP 58/2023. `upper` kosong = baris terakhir (tanpa batas atas)."""
    CATEGORIES = [("A", "Kategori A (TK/0, TK/1, K/0)"), ("B", "Kategori B (TK/2, TK/3, K/1, K/2)"), ("C", "Kategori C (K/3)")]
    category = models.CharField(max_length=1, choices=CATEGORIES)
    upper = models.DecimalField("Bruto sebulan sampai (Rp)", max_digits=16, decimal_places=0, null=True, blank=True)
    percent = models.DecimalField("Tarif (%)", max_digits=5, decimal_places=2)
    effective_from = models.DateField()

    class Meta:
        ordering = ["category", "effective_from", "upper"]
        indexes = [models.Index(fields=["category", "effective_from"])]


class TaxBracket(Voidable):
    """Master tarif progresif Pasal 17 (PPh 21 setahun): satu baris per lapisan, berlaku-sejak. `upper` kosong = lapisan teratas. Aturan baru = kumpulan baris baru bertanggal."""
    upper = models.DecimalField("PKP sampai (Rp)", max_digits=16, decimal_places=0, null=True, blank=True)
    percent = models.DecimalField("Tarif (%)", max_digits=5, decimal_places=2)
    effective_from = models.DateField()

    class Meta:
        ordering = ["effective_from", "upper"]
        indexes = [models.Index(fields=["effective_from"])]


class DeductionRule(Voidable):
    """MASTER POTONGAN tambahan (putaran 61): potongan otomatis per karyawan yang aturannya diisi pengguna, bukan ditanam di kode (mis. iuran koperasi 1% gaji pokok, iuran serikat Rp 10.000).
    Per komponen potongan, yang berlaku = baris tidak batal dengan `effective_from` terbesar ≤ akhir periode; nilai 0 = potongan dihentikan sejak tanggal itu. Riwayat tidak ditimpa."""
    METHODS = [("persen_pokok", "% dari gaji pokok"), ("persen_bruto", "% dari total pendapatan"), ("nominal_bulan", "Nominal tetap per bulan"), ("nominal_hari", "Nominal per hari dibayar")]
    component = models.ForeignKey("PayComponent", on_delete=models.PROTECT, related_name="rules")
    method = models.CharField("Cara hitung", max_length=14, choices=METHODS)
    value = models.DecimalField("Nilai", max_digits=14, decimal_places=4)
    rounding = models.DecimalField("Pembulatan ke bawah (Rp)", max_digits=8, decimal_places=0, default=1)
    pay_type = models.CharField("Berlaku untuk", max_length=8, blank=True, choices=[("", "Semua"), ("harian", "Karyawan harian"), ("bulanan", "Karyawan bulanan")], default="")
    effective_from = models.DateField()
    note = models.CharField("Dasar aturan", max_length=300, blank=True)

    class Meta:
        ordering = ["component_id", "-effective_from", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(value__gte=0), name="dedrule_nonneg")]


class MealPeriod(Voidable):
    """Periode uang makan dibayar (mis. bulan puasa/Ramadan, saat perusahaan tidak menyediakan makan). DI LUAR periode ini uang makan = Rp 0 untuk semua karyawan."""
    name = models.CharField("Nama periode", max_length=60)
    start_date = models.DateField(); end_date = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="mealperiod_dates_ok")]


class MealRate(Voidable):
    """Tarif uang makan per HARI HADIR berlaku-sejak, per tipe gaji (harian/bulanan) dan per shift. Shift kosong = tarif umum tipe itu (dipakai bila shift tidak punya baris sendiri).
    Nominal 0 = shift itu tidak mendapat uang makan (mis. shift yang sudah disediakan makan). Tanpa baris sama sekali = Rp 0."""
    pay_type = models.CharField(max_length=8, choices=PayProfile.PAY_TYPES)
    shift = models.ForeignKey("hr.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField("Uang makan per hari (Rp)", max_digits=12, decimal_places=0)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["pay_type", "shift_id", "-effective_from", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name="mealrate_nonneg")]


class OvertimeRate(Voidable):
    """Tarif lembur per JAM (nominal tetap) berlaku-sejak, per tipe gaji. Bila ada, lembur = jam terbukti scan × tarif ini; bila tidak ada, dipakai rumus upah/jam × pengali (params)."""
    KINDS = [("lembur", "Lembur (overtime)"), ("standby", "Stand by (kerja saat jam istirahat)")]
    kind = models.CharField("Jenis", max_length=8, choices=KINDS, default="lembur")
    pay_type = models.CharField(max_length=8, choices=PayProfile.PAY_TYPES)
    amount = models.DecimalField("Tarif per jam (Rp)", max_digits=12, decimal_places=0)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["pay_type", "-effective_from", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="otrate_positive")]


class EducationAllowance(Voidable):
    """Tunjangan pendidikan STANDAR per tingkat pendidikan (mis. SMA, D3, S1) berlaku-sejak. Dipakai bila profil gaji karyawan tidak mengisi T Pendidikan sendiri (nilai profil > 0 selalu menang,
    jadi tiap karyawan boleh berbeda). T Khusus dan T Lain-lain tidak punya standar: diisi per karyawan di Profil Gaji."""
    education = models.CharField("Pendidikan", max_length=30)
    amount = models.DecimalField("Tunjangan / bulan (Rp)", max_digits=14, decimal_places=0)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["education", "-effective_from", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name="eduallow_nonneg")]


class PayComponent(models.Model):
    """Komponen baris slip. `auto` dihitung mesin (kode menentukan rumus); `manual` diisi lewat Penyesuaian (insentif, pinjaman, dll.).
    Komponen tidak dihapus, hanya dinonaktifkan: slip lama menyalin kode/label/urutan."""
    SECTIONS = [("pokok", "I. Gaji Pokok"), ("tunjangan", "II. Tunjangan"), ("premi", "III. Premi Produksi"), ("lembur", "IV. Lembur"), ("makan", "V. Uang Makan"),
                ("lain", "VI. Lain-lain"), ("potongan", "VII. Potongan")]
    KINDS = [("auto", "Dihitung otomatis"), ("manual", "Diisi manual (Penyesuaian)")]
    code = models.SlugField(max_length=30, unique=True)
    label = models.CharField(max_length=60)
    section = models.CharField(max_length=10, choices=SECTIONS)
    kind = models.CharField(max_length=6, choices=KINDS, default="manual")
    order = models.PositiveSmallIntegerField(default=100)
    signed = models.BooleanField("Boleh negatif", default=False, help_text="Hanya untuk koreksi pembayaran (mis. kelebihan bayar bulan lalu).")
    active = models.BooleanField(default=True)

    class Meta: ordering = ["order", "id"]

    def __str__(self): return self.label

    @property
    def is_deduction(self): return self.section == "potongan"

    def delete(self, *a, **k): raise PermissionError("Komponen payroll tidak boleh dihapus; nonaktifkan.")


class PayAdjustment(Voidable):
    """Isian manual per karyawan per periode untuk komponen `manual` (insentif, premi produksi, pinjaman, pengobatan, materai, dll.).
    Satu baris aktif per karyawan+periode+komponen; hanya boleh diubah/ditambah selama run periode itu belum disetujui."""
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="pay_adjustments")
    period = models.CharField(max_length=7, help_text="YYYY-MM")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="adjustments")
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    qty = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-period", "component__order", "employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "period", "component"], condition=models.Q(voided_at__isnull=True), name="uniq_payadjustment_active")]
        indexes = [models.Index(fields=["period"])]


class TaliAsihRule(Voidable):
    """Dasar USULAN tali asih per jenis keluar, berlaku-sejak: tali asih = min(masa kerja penuh × hari upah per tahun, batas hari) × upah harian.
    Hanya usulan — nominal resmi tetap ditetapkan di Karyawan Keluar (tali asih) dan baru dibayar lewat run gaji."""
    kind = models.CharField(max_length=15, choices=Separation.Kind.choices)
    min_years = models.PositiveSmallIntegerField("Masa kerja minimum (tahun)", default=0)
    days_per_year = models.DecimalField("Hari upah per tahun masa kerja", max_digits=6, decimal_places=2)
    max_days = models.DecimalField("Batas hari upah", max_digits=7, decimal_places=2, null=True, blank=True)
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["kind", "-effective_from", "-id"]
        constraints = [models.UniqueConstraint(fields=["kind", "effective_from"], condition=models.Q(voided_at__isnull=True), name="uniq_taliasihrule_active"),
                       models.CheckConstraint(condition=models.Q(days_per_year__gte=0), name="taliasihrule_nonneg")]


# ---------------------------------------------------------------- run gaji
def report_path(instance, filename):
    """Arsip laporan XLSX run yang dibayar: nama acak (uuid); disajikan hanya lewat view ber-permission (Payroll)."""
    return f"payroll_reports/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.xlsx"


class PayrollRun(models.Model):
    """Satu run gaji per periode (bulan gaji). Alur (putaran 59, Payroll berdiri sendiri): draft (Payroll membuat run setelah HRD MENUTUP periode) → dihitung → ditetapkan → dibayar (terkunci);
    dibatalkan sebelum dibayar. HRD tidak ikut alur ini: serah-terima data lewat Tutup Periode (berkas XLSX), bukan lewat status run.
    `params` = salinan parameter yang dipakai saat dihitung (slip lama tidak bergeser bila parameter diubah kemudian)."""
    DRAFT, CALCULATED, FINALIZED, PAID, CANCELLED = "draft", "dihitung", "ditetapkan", "dibayar", "dibatalkan"
    STATUSES = [(DRAFT, "Draft (belum dihitung)"), (CALCULATED, "Sudah dihitung"), (FINALIZED, "Ditetapkan Payroll"), (PAID, "Sudah dibayar"), (CANCELLED, "Dibatalkan")]
    LOCKED = (FINALIZED, PAID)
    period = models.CharField(max_length=7, db_index=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    pay_date = models.DateField("Tanggal bayar (rencana)", null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    params = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    n_slips = models.PositiveIntegerField(default=0)
    total_gross = models.DecimalField(max_digits=16, decimal_places=0, default=0)
    total_deductions = models.DecimalField(max_digits=16, decimal_places=0, default=0)
    total_net = models.DecimalField(max_digits=16, decimal_places=0, default=0)
    calc_at = models.DateTimeField(null=True, blank=True); calc_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handed_at = models.DateTimeField(null=True, blank=True); handed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")        # (lama, putaran 58; tidak dipakai lagi sejak putaran 59 — dipertahankan agar riwayat tidak hilang)
    finalized_at = models.DateTimeField(null=True, blank=True); finalized_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")  # Payroll menetapkan
    paid_at = models.DateTimeField(null=True, blank=True); paid_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    paid_on = models.DateField(null=True, blank=True)
    report_file = models.FileField(upload_to=report_path, blank=True); report_at = models.DateTimeField(null=True, blank=True)   # arsip laporan XLSX saat dibayar (putaran 60)
    cancelled_at = models.DateTimeField(null=True, blank=True); cancelled_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "-id"]
        constraints = [models.UniqueConstraint(fields=["period"], condition=~models.Q(status="dibatalkan"), name="uniq_payrollrun_period_live")]

    def __str__(self): return f"Run gaji {self.period}"

    @property
    def locked(self): return self.status in self.LOCKED or self.status == self.CANCELLED
    @property
    def editable(self): return self.status in (self.DRAFT, self.CALCULATED)

    def delete(self, *a, **k): raise PermissionError("Run gaji tidak boleh dihapus; batalkan dengan alasan.")


class PayrollRunEvent(models.Model):
    """Jejak alur run (append-only)."""
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=20)
    by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta: ordering = ["id"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Jejak run tidak boleh diubah")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Jejak run tidak boleh dihapus")


class Payslip(models.Model):
    """Slip gaji satu karyawan dalam satu run. Identitas, grade, upah harian, dan parameter DISALIN saat dihitung."""
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="slips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    nik = models.CharField(max_length=20); emp_name = models.CharField(max_length=150)
    department_name = models.CharField(max_length=100); position_name = models.CharField(max_length=100, blank=True)
    grade_label = models.CharField(max_length=20, blank=True); education = models.CharField(max_length=30, blank=True)
    join_date = models.DateField(); service_years = models.PositiveSmallIntegerField(default=0)
    daily_wage = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    paid_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    total_earnings = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_deductions = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    net_pay = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    flags = models.JSONField(default=list, blank=True)
    flagged = models.BooleanField(default=False, db_index=True)   # = ada flag; kolom sendiri agar filter "perlu ditinjau" tidak membandingkan JSON

    class Meta:
        ordering = ["department_name", "emp_name", "id"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="uniq_payslip_run_employee")]
        indexes = [models.Index(fields=["run", "department_name"])]

    @property
    def needs_review(self): return self.flagged

    def delete(self, *a, **k):
        if self.run.status not in (PayrollRun.DRAFT, PayrollRun.CALCULATED): raise PermissionError("Slip pada run yang sudah ditetapkan/dibayar tidak boleh dihapus.")
        return super().delete(*a, **k)


class PayslipItem(models.Model):
    """Satu baris slip. Kode/label/bagian/urutan disalin dari komponen saat dihitung. `refs` = id sumber (mis. pembelian seragam, catatan keluar) untuk penandaan saat dibayar."""
    slip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="items")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=30); label = models.CharField(max_length=60)
    section = models.CharField(max_length=10); order = models.PositiveSmallIntegerField(default=100)
    qty = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    source = models.CharField(max_length=12, blank=True)   # profil · hitung · absensi · bpjs · seragam · keluar · manual
    note = models.CharField(max_length=200, blank=True)
    refs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["slip", "component"], name="uniq_payslipitem_slip_component")]
