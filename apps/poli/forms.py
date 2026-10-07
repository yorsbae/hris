from decimal import Decimal
from django import forms
from django.forms import formset_factory
from .models import Diagnosis, MedicalRecord, Medicine
from . import services

TA = lambda rows=3: forms.Textarea(attrs={"rows": rows})


class RecordForm(forms.Form):
    nik = forms.CharField(label="NIK karyawan", max_length=20, help_text="Hanya karyawan aktif.")
    kind = forms.ChoiceField(label="Jenis kunjungan", choices=MedicalRecord.KINDS)
    complaint = forms.CharField(label="Keluhan", required=False, widget=TA(), max_length=4000)
    # tanda vital (opsional; rentang dibatasi agar salah ketik tertangkap)
    tensi = forms.RegexField(label="Tensi (mmHg)", required=False, regex=r"^\d{2,3}/\d{2,3}$", max_length=7, error_messages={"invalid": "Format 120/80"})
    suhu = forms.DecimalField(label="Suhu (°C)", required=False, min_value=Decimal("30"), max_value=Decimal("45"), max_digits=4, decimal_places=1)
    nadi = forms.IntegerField(label="Nadi (x/menit)", required=False, min_value=20, max_value=250)
    bb = forms.DecimalField(label="Berat badan (kg)", required=False, min_value=Decimal("1"), max_value=Decimal("500"), max_digits=5, decimal_places=1)
    tb = forms.IntegerField(label="Tinggi badan (cm)", required=False, min_value=30, max_value=250)
    # khusus kecelakaan kerja
    incident_place = forms.CharField(label="Lokasi kejadian (kecelakaan kerja)", required=False, max_length=150)
    incident_story = forms.CharField(label="Kronologi (kecelakaan kerja)", required=False, widget=TA(), max_length=2000)
    # khusus kehamilan
    preg_weeks = forms.IntegerField(label="Usia kehamilan (minggu) — kehamilan", required=False, min_value=1, max_value=45)
    preg_hpl = forms.DateField(label="HPL — kehamilan", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    preg_tfu = forms.DecimalField(label="TFU (cm) — kehamilan", required=False, min_value=Decimal("0"), max_value=Decimal("60"), max_digits=4, decimal_places=1)
    preg_djj = forms.IntegerField(label="DJJ (x/menit) — kehamilan", required=False, min_value=60, max_value=220)
    diagnosis_code = forms.CharField(label="Kode diagnosa", required=False, max_length=10, help_text="Ketik kode (mis. A09); daftar di Master diagnosa.")
    treatment = forms.CharField(label="Tindakan / anjuran", required=False, widget=TA(), max_length=4000)

    def clean_nik(self):
        self.employee = services.active_employee(nik=self.cleaned_data["nik"].strip())
        if not self.employee: raise forms.ValidationError("Karyawan tidak ditemukan atau tidak aktif.")
        return self.cleaned_data["nik"].strip()

    def clean_diagnosis_code(self):
        c = self.cleaned_data["diagnosis_code"].strip().upper(); self.diagnosis = None
        if c:
            self.diagnosis = Diagnosis.objects.filter(code=c).first()
            if not self.diagnosis: raise forms.ValidationError(f"Diagnosa {c} belum ada di master.")
        return c

    def clean(self):
        d = super().clean(); k = d.get("kind")
        if k == "berobat" and not d.get("complaint", "").strip(): self.add_error("complaint", "Keluhan wajib diisi untuk berobat.")
        if k == "kecelakaan_kerja":
            for f in ("incident_place", "incident_story"):
                if not d.get(f, "").strip(): self.add_error(f, "Wajib diisi untuk kecelakaan kerja.")
        if k == "kehamilan":
            if not d.get("preg_weeks"): self.add_error("preg_weeks", "Usia kehamilan wajib diisi.")
            if getattr(self, "employee", None) and self.employee.gender != "P": self.add_error("nik", "Pemeriksaan kehamilan hanya untuk karyawan perempuan.")
        return d

    def exam(self):
        """Dict JSON-able; hanya bidang yang relevan untuk jenis kunjungan dan yang terisi."""
        d, k = self.cleaned_data, self.cleaned_data["kind"]; out = {}
        num = lambda v: float(v) if isinstance(v, Decimal) else v
        for f in ("tensi", "suhu", "nadi", "bb", "tb"):
            if d.get(f) not in (None, ""): out[f] = num(d[f])
        if k == "kecelakaan_kerja": out["kecelakaan"] = {"lokasi": d["incident_place"].strip(), "kronologi": d["incident_story"].strip()}
        if k == "kehamilan":
            preg = {"usia_minggu": d["preg_weeks"], "hpl": d["preg_hpl"].isoformat() if d.get("preg_hpl") else None, "tfu": num(d.get("preg_tfu")), "djj": d.get("preg_djj")}
            out["kehamilan"] = {a: b for a, b in preg.items() if b is not None}
        return out


class PrescriptionLineForm(forms.Form):
    medicine = forms.ModelChoiceField(queryset=Medicine.objects.none(), required=False, label="Obat")
    qty = forms.IntegerField(required=False, min_value=1, max_value=10000, label="Jumlah")
    dosage = forms.CharField(required=False, max_length=100, label="Aturan pakai")

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.fields["medicine"].queryset = Medicine.objects.order_by("name")
        self.fields["medicine"].label_from_instance = lambda m: f"{m.name} (stok {m.stock} {m.unit})"

    def clean(self):
        d = super().clean()
        if bool(d.get("medicine")) != bool(d.get("qty")): raise forms.ValidationError("Isi obat dan jumlahnya (atau kosongkan keduanya).")
        return d


class BasePrescriptionFormSet(forms.BaseFormSet):
    def clean(self):
        if any(f.errors for f in self.forms): return
        ids = [f.cleaned_data["medicine"].pk for f in self.forms if f.cleaned_data.get("medicine")]
        if len(set(ids)) != len(ids): raise forms.ValidationError("Obat yang sama dipilih lebih dari sekali; gabungkan jumlahnya dalam satu baris.")

    def lines(self): return [(f.cleaned_data["medicine"].pk, f.cleaned_data["qty"], f.cleaned_data.get("dosage", "")) for f in self.forms if f.cleaned_data.get("medicine")]


PrescriptionFormSet = formset_factory(PrescriptionLineForm, formset=BasePrescriptionFormSet, extra=5, max_num=services.MAX_LINES, validate_max=True)


class MedicineForm(forms.ModelForm):
    class Meta:
        model = Medicine; fields = ["code", "name", "unit", "min_stock"]
        labels = {"code": "Kode", "name": "Nama obat", "unit": "Satuan (tablet, botol, …)", "min_stock": "Stok minimum (peringatan)"}
    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Medicine.objects.filter(code=c).exclude(pk=self.instance.pk).exists(): raise forms.ValidationError("Kode sudah dipakai.")
        return c
    def clean_min_stock(self):
        v = self.cleaned_data["min_stock"]
        if v is None or v < 0: raise forms.ValidationError("Tidak boleh negatif.")
        return v


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis; fields = ["code", "name", "category"]
        labels = {"code": "Kode (mis. ICD-10)", "name": "Nama diagnosa", "category": "Kategori"}
    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Diagnosis.objects.filter(code=c).exclude(pk=self.instance.pk).exists(): raise forms.ValidationError("Kode sudah dipakai.")
        return c


class StockInForm(forms.Form):
    qty = forms.IntegerField(label="Jumlah masuk", min_value=1, max_value=1000000)
    ref = forms.CharField(label="No. faktur / sumber", required=False, max_length=40)
    note = forms.CharField(label="Catatan", required=False, max_length=300)


class StockAdjustForm(forms.Form):
    delta = forms.IntegerField(label="Penyesuaian (+/−)", min_value=-1000000, max_value=1000000, help_text="Contoh: -5 untuk rusak/kedaluwarsa, 3 untuk koreksi opname.")
    note = forms.CharField(label="Alasan (wajib)", max_length=300)
    def clean_delta(self):
        if self.cleaned_data["delta"] == 0: raise forms.ValidationError("Penyesuaian tidak boleh 0.")
        return self.cleaned_data["delta"]


class AddendumForm(forms.Form):
    note = forms.CharField(label="Catatan tambahan / koreksi", widget=TA(4), max_length=4000)


class ReferralForm(forms.Form):
    facility = forms.CharField(label="Fasilitas tujuan", max_length=150)
    note = forms.CharField(label="Catatan klinis singkat", required=False, widget=TA(), max_length=2000)
