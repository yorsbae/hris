"""Form HR Core. Validasi di server adalah otoritas."""
import re
from datetime import date
from django import forms
from django.db.models import Q
from .models import Contract, Department, Employee, Position, Shift, ShiftGroup

STATUSES = [("aktif", "aktif"), ("nonaktif", "nonaktif")]
CONTRACT_KINDS = [("PKWT", "PKWT"), ("PKWTT", "PKWTT / tetap"), ("percobaan", "Percobaan"), ("magang", "Magang")]


class EmployeeForm(forms.ModelForm):
    status = forms.ChoiceField(choices=STATUSES)
    supervisor_nik = forms.CharField(label="NIK atasan", max_length=20, required=False,
                                     help_text="Kosongkan bila tidak ada. Ketik NIK/nama lalu pilih dari saran (bukan dropdown) agar ringan untuk 3.000+ karyawan.",
                                     widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama atasan…"}))
    effective_date = forms.DateField(label="Tanggal efektif perubahan", initial=date.today, widget=forms.DateInput(attrs={"type": "date"}),
                                     help_text="Dipakai untuk riwayat bila departemen/jabatan/status/shift berubah.")

    class Meta:
        model = Employee
        fields = ["nik", "name", "gender", "join_date", "department", "position", "shift", "shift_group", "status", "marital_status", "education",
                  "address", "phone", "nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account"]
        widgets = {"join_date": forms.DateInput(attrs={"type": "date"}), "address": forms.Textarea(attrs={"rows": 2}),
                   **{f: forms.TextInput() for f in Employee.ENCRYPTED}}
        labels = {"nik": "NIK induk kerja", "name": "Nama", "gender": "Jenis kelamin", "join_date": "Tanggal masuk", "nik_ktp": "NIK KTP",
                  "bpjs_kes": "BPJS Kesehatan", "bpjs_tk": "BPJS Ketenagakerjaan", "bank_name": "Bank", "bank_account": "No. rekening",
                  "marital_status": "Status pernikahan", "education": "Pendidikan", "address": "Alamat", "phone": "Telepon",
                  "department": "Departemen", "position": "Jabatan", "shift": "Shift tetap / GS", "shift_group": "Kelompok shift (rotasi)"}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["position"].required = False
        self.fields["position"].queryset = Position.objects.order_by("name")
        self.fields["department"].queryset = Department.objects.order_by("name")
        self.fields["shift"].queryset = Shift.objects.filter(Q(active=True) | Q(pk=self.instance.shift_id)).order_by("name")
        self.fields["shift"].help_text = "Untuk karyawan non-rotasi (mis. GS). Kosongkan bila memakai kelompok rotasi."
        self.fields["shift_group"].required = False
        self.fields["shift_group"].queryset = ShiftGroup.objects.order_by("pattern", "code")
        self.fields["shift_group"].help_text = "Pola 2 shift: A–G. Pola 3 shift/PACK: A_pack–G_pack (kelompok berbeda walau hurufnya sama)."
        if self.instance.pk: self.fields["supervisor_nik"].widget.attrs["data-q-exclude"] = self.instance.nik
        self.supervisor = None
        if self.instance.pk:
            self.fields["supervisor_nik"].initial = self.instance.supervisor.nik if self.instance.supervisor_id else ""
        else:
            del self.fields["effective_date"]  # karyawan baru: belum ada riwayat

    def clean_nik(self):
        nik = self.cleaned_data["nik"].strip()
        if Employee.all_objects.exclude(pk=self.instance.pk).filter(nik=nik).exists():  # termasuk yang sudah dihapus (soft delete)
            raise forms.ValidationError("NIK sudah dipakai (bisa jadi oleh data yang sudah dihapus — hubungi Superadmin untuk memulihkan).")
        return nik

    def clean_nik_ktp(self):
        v = self.cleaned_data["nik_ktp"].strip()
        if v and not re.fullmatch(r"\d{16}", v): raise forms.ValidationError("NIK KTP harus 16 digit angka.")
        return v

    def clean_npwp(self):
        v = self.cleaned_data["npwp"].strip()
        if v and not re.fullmatch(r"[\d.\-]{15,25}", v): raise forms.ValidationError("Format NPWP tidak valid (15–16 digit, boleh titik/strip).")
        return v

    def clean_join_date(self):
        d = self.cleaned_data["join_date"]
        if d > date.today(): raise forms.ValidationError("Tanggal masuk tidak boleh di masa depan.")
        return d

    def clean_supervisor_nik(self):
        nik = self.cleaned_data["supervisor_nik"].strip()
        if not nik: return ""
        sup = Employee.objects.filter(nik=nik).first()
        if not sup or (self.instance.pk and sup.pk == self.instance.pk):
            raise forms.ValidationError("Atasan tidak ditemukan atau sama dengan karyawan ini.")
        self.supervisor = sup
        return nik

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.supervisor = self.supervisor
        if commit: obj.save()
        return obj


class ContractForm(forms.ModelForm):
    previous = forms.ModelChoiceField(Contract.objects.none(), required=False, label="Perpanjangan dari kontrak",
                                      help_text="Pilih bila ini perpanjangan; kontrak sebelumnya otomatis ditandai 'diperpanjang'.")
    kind = forms.ChoiceField(choices=CONTRACT_KINDS, label="Jenis")

    class Meta:
        model = Contract
        fields = ["number", "kind", "start", "end", "previous"]
        widgets = {"start": forms.DateInput(attrs={"type": "date"}), "end": forms.DateInput(attrs={"type": "date"})}
        labels = {"number": "Nomor kontrak", "start": "Mulai", "end": "Berakhir (kosong = tidak terbatas)"}

    def __init__(self, *a, employee, **k):
        super().__init__(*a, **k)
        self.employee = employee
        self.fields["previous"].queryset = employee.contracts.filter(status="aktif")

    def clean(self):
        d = super().clean()
        s, e, prev = d.get("start"), d.get("end"), d.get("previous")
        if s and e and e < s: self.add_error("end", "Tidak boleh sebelum tanggal mulai.")
        if prev and s and s <= prev.start: self.add_error("start", "Harus setelah tanggal mulai kontrak sebelumnya.")
        if not prev and self.employee.contracts.filter(status="aktif").exists():
            raise forms.ValidationError("Karyawan sudah punya kontrak aktif. Pilih kontrak sebelumnya bila ini perpanjangan.")
        return d


class MasterForm(forms.ModelForm):
    """Dasar form master; subclass di bawah."""


class DepartmentForm(MasterForm):
    class Meta:
        model = Department; fields = ["code", "name", "parent"]
        labels = {"code": "Kode", "name": "Nama", "parent": "Induk (organisasi)"}

    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Department.objects.exclude(pk=self.instance.pk).filter(code=c).exists(): raise forms.ValidationError("Kode sudah dipakai.")
        return c

    def clean_parent(self):
        p = self.cleaned_data["parent"]
        node = p
        while node and self.instance.pk:  # tolak siklus: induk tidak boleh diri sendiri atau turunannya
            if node.pk == self.instance.pk: raise forms.ValidationError("Induk tidak boleh diri sendiri atau turunannya.")
            node = node.parent
        return p


class PositionForm(MasterForm):
    class Meta:
        model = Position; fields = ["name", "level"]
        labels = {"name": "Nama jabatan", "level": "Level (angka lebih besar = lebih tinggi)"}


class ShiftForm(MasterForm):
    class Meta:
        model = Shift; fields = ["name", "start", "end", "crosses_midnight"]
        widgets = {"start": forms.TimeInput(attrs={"type": "time"}), "end": forms.TimeInput(attrs={"type": "time"})}
        labels = {"name": "Nama shift", "start": "Jam masuk", "end": "Jam pulang", "crosses_midnight": "Melewati tengah malam"}

    def clean(self):
        d = super().clean(); s, e, x = d.get("start"), d.get("end"), d.get("crosses_midnight")
        if s and e:
            if s == e: raise forms.ValidationError("Jam masuk dan pulang tidak boleh sama.")
            if e < s and not x: self.add_error("crosses_midnight", "Jam pulang lebih awal dari jam masuk: centang 'Melewati tengah malam'.")
            if e > s and x: self.add_error("crosses_midnight", "Jam pulang setelah jam masuk di hari yang sama: hilangkan centang.")
        return d


MASTERS = {"department": ("Departemen", Department, DepartmentForm), "position": ("Jabatan", Position, PositionForm), "shift": ("Shift", Shift, ShiftForm)}
