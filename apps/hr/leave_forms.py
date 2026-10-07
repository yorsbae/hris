from datetime import date
from decimal import Decimal
from django import forms
from django.conf import settings
from . import leave
from .models import LeaveLedger


class LeaveEntryForm(forms.Form):
    """HRD: beri jatah tahunan (sekali per karyawan+tahun) atau koreksi saldo (±, catatan wajib). Tidak ada ubah/hapus baris lama."""
    kind = forms.ChoiceField(label="Jenis", choices=[(LeaveLedger.GRANT, "Jatah tahunan"), (LeaveLedger.ADJUST, "Koreksi saldo (+/−)")])
    year = forms.IntegerField(label="Tahun")
    days = forms.DecimalField(label="Jumlah hari", max_digits=5, decimal_places=1, help_text="Jatah: bilangan positif. Koreksi: positif menambah, negatif mengurangi. Kelipatan 0,5.")
    note = forms.CharField(label="Catatan", max_length=300, required=False, help_text="Wajib untuk koreksi.")

    def __init__(self, *a, employee, **k):
        super().__init__(*a, **k)
        self.employee = employee
        self.fields["year"].initial = date.today().year
        self.fields["days"].initial = Decimal(settings.ANNUAL_LEAVE_DAYS)

    def clean_year(self):
        y, now = self.cleaned_data["year"], date.today().year
        if not now - 1 <= y <= now + 1: raise forms.ValidationError(f"Tahun harus {now - 1}–{now + 1}.")
        return y

    def clean_days(self):
        v = self.cleaned_data["days"]
        if v == 0: raise forms.ValidationError("Tidak boleh 0.")
        if (v * 2) % 1 != 0: raise forms.ValidationError("Gunakan kelipatan 0,5.")
        return v

    def clean(self):
        d = super().clean()
        k, y, v = d.get("kind"), d.get("year"), d.get("days")
        if not (k and y and v is not None): return d
        if k == LeaveLedger.GRANT:
            if v < 0: self.add_error("days", "Jatah harus positif.")
            elif LeaveLedger.objects.filter(employee=self.employee, year=y, kind=LeaveLedger.GRANT).exists():
                raise forms.ValidationError(f"Jatah tahun {y} sudah diberikan. Gunakan koreksi bila perlu.")
        else:
            if not (d.get("note") or "").strip(): self.add_error("note", "Wajib diisi untuk koreksi.")
            if leave.balance(self.employee, y) + v < 0:
                raise forms.ValidationError(f"Saldo {y} tidak boleh menjadi negatif (saldo sekarang {leave.fmt(leave.balance(self.employee, y))}).")
        return d
