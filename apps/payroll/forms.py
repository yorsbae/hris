"""Form Payroll (putaran 57). Satu aturan untuk input manual DAN impor CSV/XLSX (mesin core.bulk memanggil form yang sama). NIK diketik (bukan dropdown ribuan baris).
Master berlaku-sejak bersifat append-only: menyimpan pada tanggal berlaku-sejak yang sama MEMBATALKAN baris lama ('Diganti nilai baru') dan membuat baris baru."""
import re
from datetime import date
from decimal import Decimal as D, InvalidOperation

from django import forms
from django.db import transaction
from django.utils import timezone

from apps.core.money import RupiahField, parse_rupiah
from apps.hr.models import Employee
from apps.hrd.models import Separation
from . import calc, params as P
from .models import EducationAllowance, MealPeriod, MealRate, OvertimeRate, PayAdjustment, PayComponent, PayParam, PayProfile, TaliAsihRule, WageScale
from . import services

DATE = lambda: forms.DateInput(attrs={"type": "date"})  # noqa: E731
NIK = lambda: forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…", "autocomplete": "off"})  # noqa: E731


def same_values(obj, new):
    """True bila SEMUA nilai `new` (dict field→nilai) identik dengan `obj` (Decimal/int/str dibandingkan menurut nilai). Dipakai agar menyimpan ulang nilai yang sama
    (mis. impor ulang file yang sama) tidak membatalkan baris lama lalu membuat baris kembar — riwayat tetap ringkas dan tanpa data berulang."""
    for k, v in new.items():
        cur = getattr(obj, k)
        if isinstance(v, bool) or isinstance(cur, bool):
            if bool(cur) != bool(v): return False
        elif isinstance(v, (int, float, D)) or isinstance(cur, (int, float, D)):
            if cur is None or v is None:
                if cur is not v: return False
            elif D(str(cur)) != D(str(v)): return False
        elif (cur or "") != (v or ""): return False
    return True


class SignedRupiahField(RupiahField):
    """Seperti RupiahField tetapi boleh negatif (hanya komponen 'boleh negatif', mis. koreksi kelebihan bayar)."""
    def to_python(self, value):
        if value in self.empty_values: return None
        if isinstance(value, str):
            try: return parse_rupiah(value, allow_negative=True)
            except ValueError as e: raise forms.ValidationError(str(e) if "Format" not in str(e) else self.error_messages["invalid"], code="invalid")
        return super().to_python(value)


class DecimalField2(forms.DecimalField):
    """Desimal yang menerima koma Indonesia ('84.480,87' atau '84480,87' atau '84480.87')."""
    def to_python(self, value):
        if isinstance(value, str):
            v = value.strip().replace(" ", "")
            if "," in v: v = v.replace(".", "").replace(",", ".")
            elif v.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", v): v = v.replace(".", "")
            value = v
        return super().to_python(value)


class ByNik(forms.Form):
    """Mixin: `nik` → self.employee (karyawan yang belum dihapus; status apa pun — gaji bulan terakhir karyawan nonaktif sah)."""
    def _employee(self, d):
        nik = (d.get("nik") or "").strip()
        self.employee = Employee.objects.select_related("department").filter(nik=nik).first() if nik else None
        if nik and not self.employee: self.add_error("nik", "Karyawan dengan NIK ini tidak ditemukan.")
        return self.employee


# ---------------------------------------------------------------- run
class RunForm(forms.Form):
    period = forms.CharField(label="Periode gaji (YYYY-MM)", max_length=7, initial=lambda: timezone.localdate().strftime("%Y-%m"))
    pay_date = forms.DateField(label="Rencana tanggal bayar", required=False, widget=DATE())
    note = forms.CharField(label="Catatan", required=False, max_length=300)

    def clean_period(self):
        v = self.cleaned_data["period"].strip()
        if not calc.valid_period(v): raise forms.ValidationError("Periode harus berformat YYYY-MM (mis. 2026-10).")
        return v


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Alasan", max_length=300, widget=forms.TextInput(attrs={"required": True}))


class PayDateForm(forms.Form):
    paid_on = forms.DateField(label="Tanggal bayar", widget=DATE(), initial=timezone.localdate)


# ---------------------------------------------------------------- profil gaji karyawan
class ProfileForm(ByNik):
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=NIK())
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    grade = forms.IntegerField(label="Grade", required=False, min_value=0, max_value=99)
    tier = forms.CharField(label="Sub-grade (A, B, …)", required=False, max_length=2)
    wage_override = DecimalField2(label="Upah harian khusus (Rp)", required=False, min_value=1, max_value=100_000_000, max_digits=14, decimal_places=2,
                                  help_text="Isi HANYA bila tidak mengikuti skala grade. Kosongkan untuk memakai Skala Gaji Harian.")
    allowance_position = RupiahField(label="T Jabatan / bulan (Rp)", required=False, min_value=0, max_value=1_000_000_000)
    allowance_education = RupiahField(label="T Pendidikan / bulan (Rp)", required=False, min_value=0, max_value=1_000_000_000)
    allowance_special = RupiahField(label="T Khusus / bulan (Rp)", required=False, min_value=0, max_value=1_000_000_000)
    allowance_other = RupiahField(label="T Lain-lain / bulan (Rp)", required=False, min_value=0, max_value=1_000_000_000)
    pay_type = forms.ChoiceField(label="Tipe gaji", choices=PayProfile.PAY_TYPES, initial="harian", required=False, help_text="Menentukan tarif Uang Makan & Lembur di master (Payroll → Lembur & Uang Makan).")
    meal_allowance = forms.BooleanField(label="Berhak uang makan", required=False, help_text="Hanya dibayar pada Periode Uang Makan (mis. bulan puasa), dengan tarif sesuai tipe gaji & shift.")
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.employee, self.user = instance, None, user

    def clean_tier(self): return self.cleaned_data["tier"].strip().upper()

    def clean(self):
        d = super().clean(); self._employee(d)
        g, t, w = d.get("grade"), d.get("tier"), d.get("wage_override")
        if self.errors.get("grade") or self.errors.get("wage_override"): return d
        if w is None and (g is None or not t): self.add_error("grade", "Isi grade dan sub-grade (skala gaji), atau isi upah harian khusus.")
        if w is not None and (g is not None or t): self.add_error("wage_override", "Pilih salah satu: grade + sub-grade ATAU upah harian khusus (bukan keduanya).")
        return d

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        old = PayProfile.objects.select_for_update().filter(employee=self.employee, effective_from=d["effective_from"], voided_at__isnull=True).first()
        new = {"grade": d.get("grade"), "tier": d.get("tier") or "", "wage_override": d.get("wage_override"), "allowance_position": d.get("allowance_position") or 0,
               "allowance_education": d.get("allowance_education") or 0, "allowance_special": d.get("allowance_special") or 0, "allowance_other": d.get("allowance_other") or 0,
               "meal_allowance": bool(d.get("meal_allowance")), "pay_type": d.get("pay_type") or "harian", "note": d.get("note", "").strip()}
        if old and same_values(old, new): self.unchanged = True; return old
        if old: self.replaced = True; old.voided_at, old.voided_by, old.void_reason = timezone.now(), self.user, "Diganti nilai baru"; old.save(update_fields=["voided_at", "voided_by", "void_reason"])
        return PayProfile.objects.create(employee=self.employee, effective_from=d["effective_from"], grade=d.get("grade"), tier=d.get("tier") or "", wage_override=d.get("wage_override"),
                                         allowance_position=d.get("allowance_position") or 0, allowance_education=d.get("allowance_education") or 0, allowance_special=d.get("allowance_special") or 0,
                                         allowance_other=d.get("allowance_other") or 0, meal_allowance=bool(d.get("meal_allowance")), pay_type=d.get("pay_type") or "harian", note=d.get("note", "").strip(), created_by=self.user)


# ---------------------------------------------------------------- skala gaji harian
class ScaleForm(forms.Form):
    grade = forms.IntegerField(label="Grade", min_value=0, max_value=99)
    tier = forms.CharField(label="Sub-grade (A, B, …)", max_length=2)
    daily_wage = DecimalField2(label="Upah harian (Rp)", min_value=1, max_value=100_000_000, max_digits=14, decimal_places=2, help_text="Boleh berdesimal (mis. 84.480,87).")
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.user = instance, user

    def clean_tier(self):
        v = self.cleaned_data["tier"].strip().upper()
        if not v: raise forms.ValidationError("Sub-grade wajib diisi (mis. A).")
        return v

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        old = WageScale.objects.select_for_update().filter(grade=d["grade"], tier=d["tier"], effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"daily_wage": d["daily_wage"], "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; old.voided_at, old.voided_by, old.void_reason = timezone.now(), self.user, "Diganti nilai baru"; old.save(update_fields=["voided_at", "voided_by", "void_reason"])
        return WageScale.objects.create(grade=d["grade"], tier=d["tier"], daily_wage=d["daily_wage"], effective_from=d["effective_from"], note=d.get("note", "").strip(), created_by=self.user)


# ---------------------------------------------------------------- parameter
class ParamForm(forms.Form):
    key = forms.ChoiceField(label="Parameter", choices=P.CHOICES)
    value = DecimalField2(label="Nilai baru", min_value=0, max_value=1_000_000_000, max_digits=14, decimal_places=4)
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Alasan / dasar aturan", required=False, max_length=300)

    def __init__(self, *a, user=None, **k):
        super().__init__(*a, **k); self.user = user

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        old = PayParam.objects.select_for_update().filter(key=d["key"], effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"value": d["value"], "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; old.voided_at, old.voided_by, old.void_reason = timezone.now(), self.user, "Diganti nilai baru"; old.save(update_fields=["voided_at", "voided_by", "void_reason"])
        return PayParam.objects.create(key=d["key"], value=d["value"], effective_from=d["effective_from"], note=d.get("note", "").strip(), created_by=self.user)


# ---------------------------------------------------------------- penyesuaian
class AdjustmentForm(ByNik):
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=NIK())
    period = forms.CharField(label="Periode (YYYY-MM)", max_length=7, initial=lambda: timezone.localdate().strftime("%Y-%m"))
    component = forms.ModelChoiceField(label="Komponen", queryset=PayComponent.objects.none())
    amount = SignedRupiahField(label="Nominal (Rp)", min_value=-1_000_000_000, max_value=1_000_000_000)
    qty = forms.DecimalField(label="Jumlah (opsional)", required=False, min_value=0, max_digits=8, decimal_places=2, help_text="Mis. jumlah hari/jam; hanya keterangan di slip.")
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.employee, self.user = instance, None, user
        self.fields["component"].queryset = PayComponent.objects.filter(kind="manual", active=True).order_by("section", "order")

    def clean_period(self):
        v = self.cleaned_data["period"].strip()
        if not calc.valid_period(v): raise forms.ValidationError("Periode harus berformat YYYY-MM (mis. 2026-10).")
        return v

    def clean(self):
        d = super().clean(); self._employee(d)
        c, a = d.get("component"), d.get("amount")
        if c is not None and a is not None:
            if a == 0: self.add_error("amount", "Nominal 0 tidak perlu dicatat.")
            elif a < 0 and not c.signed: self.add_error("amount", f"Nominal {c.label} tidak boleh negatif.")
        if d.get("period") and not services.period_open_for_edit(d["period"]): self.add_error("period", "Run gaji periode ini sudah diajukan/disetujui/dibayar; tarik atau batalkan run dulu.")
        if self.employee and d.get("period") and d["period"] < self.employee.join_date.strftime("%Y-%m"): self.add_error("period", "Periode sebelum bulan masuk karyawan.")
        return d

    def save(self):
        d = self.cleaned_data
        obj = services.save_adjustment(self.employee, d["period"], d["component"], d["amount"], d.get("qty"), d.get("note", ""), self.user)
        self.unchanged = bool(getattr(obj, "_unchanged", False)); self.replaced = bool(getattr(obj, "_replaced", False))
        return obj


# ---------------------------------------------------------------- aturan tali asih (usulan)
class TaliRuleForm(forms.Form):
    kind = forms.ChoiceField(label="Jenis keluar", choices=Separation.Kind.choices)
    min_years = forms.IntegerField(label="Masa kerja minimum (tahun)", min_value=0, max_value=60, initial=1)
    days_per_year = DecimalField2(label="Hari upah per tahun masa kerja", min_value=0, max_value=365, max_digits=6, decimal_places=2)
    max_days = DecimalField2(label="Batas hari upah (opsional)", required=False, min_value=0, max_value=9999, max_digits=7, decimal_places=2)
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Dasar aturan", required=False, max_length=300)

    def __init__(self, *a, user=None, **k):
        super().__init__(*a, **k); self.user = user

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        old = TaliAsihRule.objects.select_for_update().filter(kind=d["kind"], effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"min_years": d["min_years"], "days_per_year": d["days_per_year"], "max_days": d.get("max_days"), "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; old.voided_at, old.voided_by, old.void_reason = timezone.now(), self.user, "Diganti nilai baru"; old.save(update_fields=["voided_at", "voided_by", "void_reason"])
        return TaliAsihRule.objects.create(kind=d["kind"], min_years=d["min_years"], days_per_year=d["days_per_year"], max_days=d.get("max_days"), effective_from=d["effective_from"],
                                           note=d.get("note", "").strip(), created_by=self.user)


# ---------------------------------------------------------------- master lembur & uang makan
def _void(obj, user, why="Diganti nilai baru"):
    obj.voided_at, obj.voided_by, obj.void_reason = timezone.now(), user, why; obj.save(update_fields=["voided_at", "voided_by", "void_reason"])


class MealPeriodForm(forms.Form):
    name = forms.CharField(label="Nama periode", max_length=60, initial="Ramadan")
    start_date = forms.DateField(label="Mulai", widget=DATE()); end_date = forms.DateField(label="Sampai", widget=DATE())
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.user = instance, user

    def clean(self):
        d = super().clean(); s, e = d.get("start_date"), d.get("end_date")
        if s and e:
            if e < s: self.add_error("end_date", "Tanggal selesai tidak boleh sebelum tanggal mulai.")
            elif (e - s).days > 62: self.add_error("end_date", "Periode uang makan maksimal 62 hari (mis. satu bulan puasa).")
            elif MealPeriod.objects.filter(voided_at__isnull=True, start_date__lte=e, end_date__gte=s).exists(): self.add_error("start_date", "Tumpang tindih dengan periode uang makan lain. Batalkan yang lama dahulu.")
        return d

    def save(self):
        d = self.cleaned_data
        return MealPeriod.objects.create(name=d["name"].strip(), start_date=d["start_date"], end_date=d["end_date"], note=d.get("note", "").strip(), created_by=self.user)


class MealRateForm(forms.Form):
    pay_type = forms.ChoiceField(label="Tipe gaji", choices=PayProfile.PAY_TYPES)
    shift = forms.ModelChoiceField(label="Shift", required=False, queryset=None, empty_label="Semua shift (tarif umum)", help_text="Kosongkan = tarif umum. Isi shift tertentu bila tarifnya berbeda; nominal 0 = shift itu tidak dapat uang makan.")
    amount = RupiahField(label="Uang makan per hari hadir (Rp)", min_value=0, max_value=10_000_000)
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        from apps.hr.models import Shift
        super().__init__(*a, **k); self.instance, self.user = instance, user
        self.fields["shift"].queryset = Shift.objects.filter(active=True).order_by("code", "name")

    @transaction.atomic
    def save(self):
        d = self.cleaned_data; old = MealRate.objects.select_for_update().filter(pay_type=d["pay_type"], shift=d.get("shift"), effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"amount": d["amount"], "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; _void(old, self.user)
        return MealRate.objects.create(pay_type=d["pay_type"], shift=d.get("shift"), amount=d["amount"], effective_from=d["effective_from"], note=d.get("note", "").strip(), created_by=self.user)


class OvertimeRateForm(forms.Form):
    kind = forms.ChoiceField(label="Jenis", choices=OvertimeRate.KINDS, initial="lembur", help_text="Stand by = bekerja pada jam istirahat (disetujui HRD).")
    pay_type = forms.ChoiceField(label="Tipe gaji", choices=PayProfile.PAY_TYPES)
    amount = RupiahField(label="Tarif per jam (Rp)", min_value=1, max_value=10_000_000, help_text="Nominal tetap per 1 jam (lembur: terbukti scan; stand by: disetujui). Menggantikan rumus upah/jam × pengali untuk jenis + tipe gaji ini.")
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.user = instance, user

    @transaction.atomic
    def save(self):
        d = self.cleaned_data; old = OvertimeRate.objects.select_for_update().filter(kind=d["kind"], pay_type=d["pay_type"], effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"amount": d["amount"], "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; _void(old, self.user)
        return OvertimeRate.objects.create(kind=d["kind"], pay_type=d["pay_type"], amount=d["amount"], effective_from=d["effective_from"], note=d.get("note", "").strip(), created_by=self.user)


class EducationAllowanceForm(forms.Form):
    education = forms.CharField(label="Pendidikan", max_length=30, help_text="Sama dengan isian Pendidikan di data karyawan (mis. SMA, D3, S1).")
    amount = RupiahField(label="Tunjangan pendidikan / bulan (Rp)", min_value=0, max_value=100_000_000, help_text="Dipakai bila T Pendidikan di Profil Gaji karyawan kosong/0. Nilai di profil karyawan selalu menang.")
    effective_from = forms.DateField(label="Berlaku sejak", initial=timezone.localdate, widget=DATE())
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.user = instance, user

    def clean_education(self): return self.cleaned_data["education"].strip().upper()

    @transaction.atomic
    def save(self):
        d = self.cleaned_data; old = EducationAllowance.objects.select_for_update().filter(education=d["education"], effective_from=d["effective_from"], voided_at__isnull=True).first()
        if old and same_values(old, {"amount": d["amount"], "note": d.get("note", "").strip()}): self.unchanged = True; return old
        if old: self.replaced = True; _void(old, self.user)
        return EducationAllowance.objects.create(education=d["education"], amount=d["amount"], effective_from=d["effective_from"], note=d.get("note", "").strip(), created_by=self.user)
