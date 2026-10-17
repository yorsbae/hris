"""Form HR Core. Validasi di server adalah otoritas."""
import re
from datetime import date
from django import forms
from django.db.models import Case, IntegerField, Q, Value, When
from .models import Contract, Department, Employee, Position, Shift, ShiftGroup

STATUSES = [("aktif", "aktif"), ("nonaktif", "nonaktif")]
CONTRACT_KINDS = [("PKWT", "PKWT"), ("PKWTT", "PKWTT / tetap"), ("percobaan", "Percobaan"), ("magang", "Magang")]


def norm_ident(v):
    """Normalisasi pengenal pribadi (KTP/BPJS/NPWP) untuk membandingkan: hanya huruf-angka, huruf kecil."""
    return re.sub(r"[^0-9a-z]", "", (v or "").lower())


IDENT_FIELDS = {"nik_ktp": "NIK KTP", "bpjs_kes": "No. BPJS Kesehatan", "bpjs_tk": "No. BPJS Ketenagakerjaan", "npwp": "NPWP"}


def identifier_owners(exclude_pk=None):
    """{field: {nilai-ternormalisasi: nik}} untuk SEMUA karyawan (termasuk yang di-soft-delete). Kolom terenkripsi tak bisa dicari lewat SQL, jadi dibaca & dibandingkan di Python
    (satu kali per impor / per simpan form — sekitar ribuan baris, murah)."""
    out = {f: {} for f in IDENT_FIELDS}
    qs = Employee.all_objects.all()
    if exclude_pk: qs = qs.exclude(pk=exclude_pk)
    for row in qs.values("nik", *IDENT_FIELDS):
        for f in IDENT_FIELDS:
            n = norm_ident(row[f])
            if n: out[f].setdefault(n, row["nik"])
    return out


class ShiftChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, g): return g.label


class EmployeeForm(forms.ModelForm):
    status = forms.ChoiceField(choices=STATUSES)
    shift_choice = ShiftChoiceField(ShiftGroup.objects.none(), required=False, label="Shift", empty_label="— belum diatur —",
                                    help_text="Satu pilihan: GS, GS/N (kadang GS, kadang shift), A7–G7 (pola 2 shift: Pagi–Siang), A7Pack–G7Pack (pola 3 shift/PACK: Pagi–Siang–Malam).")
    supervisor_nik = forms.CharField(label="NIK atasan", max_length=20, required=False,
                                     help_text="Kosongkan bila tidak ada. Ketik NIK/nama lalu pilih dari saran (bukan dropdown) agar ringan untuk 3.000+ karyawan.",
                                     widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama atasan…"}))
    effective_date = forms.DateField(label="Tanggal efektif perubahan", initial=date.today, widget=forms.DateInput(attrs={"type": "date"}),
                                     help_text="Dipakai untuk riwayat bila departemen/jabatan/status/shift berubah.")

    class Meta:
        model = Employee
        fields = ["nik", "name", "gender", "join_date", "department", "position", "shift", "shift_group", "gs_short", "off_weekday", "pay_basis", "grade", "grade_tier",
                  "status", "marital_status", "education", "birth_date", "address", "phone", "nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account"]
        widgets = {"join_date": forms.DateInput(attrs={"type": "date"}), "birth_date": forms.DateInput(attrs={"type": "date"}), "address": forms.Textarea(attrs={"rows": 2}),
                   **{f: forms.TextInput() for f in Employee.ENCRYPTED}}
        labels = {"nik": "NIK induk kerja", "name": "Nama", "gender": "Jenis kelamin", "join_date": "Tanggal masuk", "nik_ktp": "NIK KTP",
                  "bpjs_kes": "BPJS Kesehatan", "bpjs_tk": "BPJS Ketenagakerjaan", "bank_name": "Bank", "bank_account": "No. rekening",
                  "marital_status": "Status pernikahan", "education": "Pendidikan", "birth_date": "Tanggal lahir", "address": "Alamat", "phone": "Telepon",
                  "department": "Departemen", "position": "Jabatan", "gs_short": "GS sebelum libur", "off_weekday": "Hari libur (GS, GS/N)",
                  "pay_basis": "Status karyawan (harian/bulanan)", "grade": "Grade", "grade_tier": "Tingkat grade"}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["position"].required = False
        self.fields["position"].queryset = Position.objects.order_by("name")
        self.fields["department"].queryset = Department.objects.order_by("name")
        for f in ("shift", "shift_group"): del self.fields[f]   # digantikan satu field "Shift" (shift_choice); kolom DB tetap dipakai mesin jadwal
        order = Case(When(pattern=ShiftGroup.GS, then=Value(0)), When(pattern=ShiftGroup.GSN, then=Value(1)), When(pattern=ShiftGroup.P2, then=Value(2)), default=Value(3), output_field=IntegerField())
        self.fields["shift_choice"].queryset = ShiftGroup.objects.order_by(order, "code")
        cur = self.instance.shift_group if self.instance.shift_group_id else (ShiftGroup.objects.filter(pattern=ShiftGroup.GS).first() if self.instance.shift_id and self.instance.shift.is_gs else None)
        self.fields["shift_choice"].initial = cur.pk if cur else None
        self.fields["off_weekday"].required = False
        self.fields["off_weekday"].help_text = "Khusus GS dan GS/N: hari libur mingguan masing-masing karyawan (acak per orang). Kosong = libur umum (Minggu). Pindah libur sesekali lewat pengajuan tukar libur."
        self.fields["grade"].required = False
        self.fields["grade"].help_text = "Contoh: 9 dan tingkat A. Hanya profil; upah tetap diatur di modul Payroll."
        self.fields["grade_tier"].required = False
        self.fields["pay_basis"].initial = "bulanan"; self.fields["pay_basis"].required = False   # kosong = pertahankan nilai lama (karyawan baru: bulanan)
        self.order_fields(["nik", "name", "gender", "join_date", "department", "position", "shift_choice", "gs_short", "off_weekday", "pay_basis", "grade", "grade_tier", "status"])
        self.fields["gs_short"].required = False  # kosong = pertahankan nilai lama (karyawan baru: 14)
        self.fields["gs_short"].help_text = "Khusus karyawan GS dan GS/N: jam pulang pada hari kerja sebelum libur GS (mis. Sabtu): GS-14 atau GS-12. Pada hari biasa GS 08:00–16:00."
        if self.instance.pk: self.fields["supervisor_nik"].widget.attrs["data-q-exclude"] = self.instance.nik
        self.supervisor = None
        if self.instance.pk:
            self.fields["supervisor_nik"].initial = self.instance.supervisor.nik if self.instance.supervisor_id else ""
        else:
            del self.fields["effective_date"]  # karyawan baru: belum ada riwayat

    def clean_pay_basis(self):
        return self.cleaned_data.get("pay_basis") or (self.instance.pay_basis if self.instance.pk else "bulanan")

    def clean_grade(self):
        g = self.cleaned_data.get("grade")
        if g is not None and not 1 <= g <= 99: raise forms.ValidationError("Grade 1–99.")
        return g

    def clean_grade_tier(self):
        t = (self.cleaned_data.get("grade_tier") or "").strip().upper()
        if t and not re.fullmatch(r"[A-Z]{1,2}", t): raise forms.ValidationError("Tingkat grade berupa 1–2 huruf (mis. A).")
        return t

    def clean_gs_short(self):
        return self.cleaned_data.get("gs_short") or (self.instance.gs_short if self.instance.pk else "14")

    def clean_nik(self):
        nik = self.cleaned_data["nik"].strip()
        if Employee.all_objects.exclude(pk=self.instance.pk).filter(nik=nik).exists():  # termasuk yang sudah dihapus (soft delete)
            raise forms.ValidationError("NIK sudah dipakai (bisa jadi oleh data yang sudah dihapus — hubungi Superadmin untuk memulihkan).")
        return nik

    def clean_nik_ktp(self):
        v = self.cleaned_data["nik_ktp"].strip()
        if v and not re.fullmatch(r"\d{16}", v): raise forms.ValidationError("NIK KTP harus 16 digit angka.")
        return v

    def _owners(self):
        # Impor massal memberi tabel pemilik siap pakai (`owners`) agar tidak membaca ulang seluruh karyawan per baris.
        if getattr(self, "owners", None) is None: self.owners = identifier_owners(self.instance.pk)
        return self.owners

    def _check_identifiers(self, d):
        for f, label in IDENT_FIELDS.items():
            n = norm_ident(d.get(f))
            if not n or f in self.errors: continue
            other = self._owners()[f].get(n)
            if other and other != (self.instance.nik if self.instance.pk else None):
                self.add_error(f, f"{label} ini sudah tercatat pada karyawan lain (NIK {other}). Satu orang tidak boleh terdaftar dua kali.")

    def clean_npwp(self):
        v = self.cleaned_data["npwp"].strip()
        if v and not re.fullmatch(r"[\d.\-]{15,25}", v): raise forms.ValidationError("Format NPWP tidak valid (15–16 digit, boleh titik/strip).")
        return v

    def clean(self):
        d = super().clean()
        if d.get("grade_tier") and d.get("grade") is None: self.add_error("grade", "Isi grade bila tingkat diisi.")
        g = d.get("shift_choice")
        if g and not g.is_rotation and "shift_choice" in self.changed_data and not (self.instance.shift_id and self.instance.shift.is_gs) and not self._gs_shift():
            self.add_error("shift_choice", "Belum ada shift GS di Master Shift; buat dulu (mis. GS-16).")
        self._check_identifiers(d)
        return d

    def clean_birth_date(self):
        d = self.cleaned_data.get("birth_date")
        if d and not (14 <= (date.today().year - d.year - ((date.today().month, date.today().day) < (d.month, d.day))) <= 100):
            raise forms.ValidationError("Tanggal lahir tidak masuk akal (umur harus 14–100 tahun).")
        return d

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

    def _gs_shift(self):
        """Shift GS bawaan (jam pulang terlambat = GS-16). Impor massal mengisi `gs_shift` agar tidak menanyakan DB per baris."""
        if getattr(self, "gs_shift", None) is None: self.gs_shift = Shift.objects.filter(is_gs=True, active=True).order_by("-end", "pk").first()
        return self.gs_shift

    def _apply_shift_choice(self, obj):
        """Satu pilihan 'Shift' → dua kolom yang dibaca mesin jadwal. Rotasi: kelompok saja. GS & GS/N: kelompok + shift GS (jam 08–16).
        Tidak diubah bila pilihan tidak berubah (mis. data lama dengan shift tetap non-GS tidak terhapus diam-diam)."""
        g = self.cleaned_data.get("shift_choice")
        if not g and getattr(self, "legacy_shift", None) is not None: obj.shift = self.legacy_shift; return   # impor format lama: shift tetap non-GS
        if "shift_choice" not in self.changed_data: return
        obj.shift_group = g
        if g is None: obj.shift = None
        elif g.is_rotation: obj.shift = None
        elif not (obj.shift_id and obj.shift.is_gs): obj.shift = self._gs_shift()   # GS-16

    def save(self, commit=True):
        obj = super().save(commit=False)
        self._apply_shift_choice(obj)
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
        model = Shift; fields = ["name", "code", "start", "end", "crosses_midnight", "is_gs", "active"]
        widgets = {"start": forms.TimeInput(attrs={"type": "time"}), "end": forms.TimeInput(attrs={"type": "time"})}
        labels = {"name": "Nama shift", "code": "Kode (mis. PAGI, SIANG, MALAM, GS-12)", "start": "Jam masuk", "end": "Jam pulang", "crosses_midnight": "Melewati tengah malam",
                  "is_gs": "General shift (GS): tidak ikut rotasi kelompok", "active": "Aktif (nonaktif = tidak ditawarkan lagi di form pengajuan/karyawan)"}

    def clean_code(self):
        c = self.cleaned_data.get("code", "").strip().upper()
        if c and Shift.objects.exclude(pk=self.instance.pk).filter(code=c).exists(): raise forms.ValidationError("Kode sudah dipakai shift lain.")
        return c

    def clean(self):
        d = super().clean(); s, e, x = d.get("start"), d.get("end"), d.get("crosses_midnight")
        if s and e:
            if s == e: raise forms.ValidationError("Jam masuk dan pulang tidak boleh sama.")
            if e < s and not x: self.add_error("crosses_midnight", "Jam pulang lebih awal dari jam masuk: centang 'Melewati tengah malam'.")
            if e > s and x: self.add_error("crosses_midnight", "Jam pulang setelah jam masuk di hari yang sama: hilangkan centang.")
        return d


MASTERS = {"department": ("Departemen", Department, DepartmentForm), "position": ("Jabatan", Position, PositionForm), "shift": ("Shift", Shift, ShiftForm)}
