from datetime import date, timedelta
from django import forms
from .models import Position, StaffOrder

MAX_QTY = 200


class StaffOrderForm(forms.ModelForm):
    class Meta:
        model = StaffOrder
        fields = ["section", "position", "qty", "needed_from", "needed_until", "urgency", "reason"]
        widgets = {"needed_from": forms.DateInput(attrs={"type": "date"}), "needed_until": forms.DateInput(attrs={"type": "date"}),
                   "reason": forms.Textarea(attrs={"rows": 4, "maxlength": 1000}), "section": forms.TextInput(attrs={"maxlength": 100})}
        labels = {"position": "Jabatan yang dicari (opsional)"}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["position"].queryset = Position.objects.order_by("name"); self.fields["position"].required = False
        self.fields["qty"].widget.attrs.update(min=1, max=MAX_QTY)
        self.fields["needed_from"].initial = date.today() + timedelta(days=7)

    def clean_qty(self):
        q = self.cleaned_data["qty"]
        if not 1 <= q <= MAX_QTY: raise forms.ValidationError(f"Jumlah harus 1–{MAX_QTY} orang.")
        return q

    def clean_section(self):
        s = " ".join(self.cleaned_data["section"].split())
        if not s: raise forms.ValidationError("Isi bagian / posisi yang membutuhkan tambahan.")
        return s

    def clean_reason(self):
        r = self.cleaned_data["reason"].strip()
        if len(r) < 10: raise forms.ValidationError("Jelaskan alasannya (minimal 10 karakter) agar HRD bisa menilai.")
        return r

    def clean(self):
        d = super().clean(); a, b = d.get("needed_from"), d.get("needed_until")
        if a and a < date.today(): self.add_error("needed_from", "Tanggal mulai tidak boleh di masa lalu.")
        if a and b and b < a: self.add_error("needed_until", "Tanggal selesai tidak boleh sebelum tanggal mulai.")
        return d
