"""Form Operasional HRD. Validasi di server adalah otoritas; field NIK diketik (bukan dropdown 3.000 baris)."""
import re
from datetime import date
from django import forms
from apps.core.money import RupiahField
from apps.hr.models import Employee
from . import services
from .models import Aid, BpjsDeduction, BpjsMembership, BpjsScheme, BpjsState, CateringOrder, MaternityLeave, Project, ProjectWork, UniformPurchase, UniformRate, UniformSize, UniformType, WarningLetter

D = lambda: forms.DateInput(attrs={"type": "date"})  # noqa: E731


class EmployeeByNik(forms.Form):
    """Mixin: field `nik` → self.employee (hanya karyawan aktif yang belum dihapus)."""
    gender = None  # isi "P" untuk mensyaratkan jenis kelamin

    def _find_employee(self, d):
        nik = (d.get("nik") or "").strip()
        emp = Employee.objects.select_related("department").filter(nik=nik, status="aktif").first() if nik else None
        if not emp: self.add_error("nik", "Karyawan aktif dengan NIK ini tidak ditemukan.")
        elif self.gender and emp.gender != self.gender: self.add_error("nik", "Karyawan ini tidak memenuhi syarat untuk jenis data ini (jenis kelamin).")
        else: self.employee = emp
        return self.employee


# ---------------------------------------------------------------- BPJS
class BpjsStatusForm(forms.Form):
    scheme = forms.ChoiceField(label="Program", choices=BpjsScheme.choices)
    status = forms.ChoiceField(label="Status baru", choices=BpjsState.choices)
    effective_date = forms.DateField(label="Tanggal efektif", initial=date.today, widget=D())
    note = forms.CharField(label="Alasan / keterangan", max_length=300, required=False, help_text="Wajib bila status nonaktif (mis. resign, belum didaftarkan, menunggak).")

    def __init__(self, *a, employee, **k):
        super().__init__(*a, **k); self.employee = employee

    def clean(self):
        d = super().clean()
        if not (d.get("scheme") and d.get("status") and d.get("effective_date")): return d
        if d["status"] == "nonaktif" and not d.get("note", "").strip(): self.add_error("note", "Alasan wajib diisi untuk status nonaktif.")
        if d["effective_date"] < self.employee.join_date: self.add_error("effective_date", "Tidak boleh sebelum tanggal masuk karyawan.")
        cur = BpjsMembership.objects.filter(employee=self.employee, scheme=d["scheme"]).first()
        if cur and cur.status == d["status"]: self.add_error("status", "Sama dengan status saat ini.")
        elif cur and d["effective_date"] < cur.effective_date: self.add_error("effective_date", f"Tidak boleh lebih awal dari {cur.effective_date:%d-%m-%Y} (status saat ini).")
        return d


class BpjsDeductionForm(forms.Form):
    """Satu aturan untuk input manual DAN impor XLSX/CSV (mesin core.bulk memanggil form ini). Karyawan nonaktif BOLEH (gaji bulan terakhir);
    kejanggalan ditandai di halaman rekap, bukan ditolak di sini."""
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    scheme = forms.ChoiceField(label="Program", choices=BpjsScheme.choices)
    period = forms.CharField(label="Periode (YYYY-MM)", max_length=7)
    employee_amount = RupiahField(label="Porsi karyawan (Rp)", min_value=0, max_value=2_000_000_000)
    employer_amount = RupiahField(label="Porsi perusahaan (Rp)", min_value=0, max_value=2_000_000_000, required=False)
    note = forms.CharField(label="Keterangan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.employee, self.user = instance, None, user

    def clean_period(self):
        v = self.cleaned_data["period"].strip()
        m = re.fullmatch(r"(\d{4})-(\d{2})", v)
        if not m or not (2000 <= int(m[1]) <= 2100 and 1 <= int(m[2]) <= 12): raise forms.ValidationError("Periode harus berformat YYYY-MM (mis. 2026-10).")
        return v

    def clean(self):
        d = super().clean()
        nik = (d.get("nik") or "").strip()
        self.employee = Employee.objects.filter(nik=nik).first() if nik else None
        if nik and not self.employee: self.add_error("nik", "Karyawan dengan NIK ini tidak ditemukan.")
        if self.employee and d.get("period") and d["period"] < self.employee.join_date.strftime("%Y-%m"):
            self.add_error("period", "Periode sebelum bulan masuk karyawan.")
        return d

    def save(self):
        d = self.cleaned_data
        obj, _ = BpjsDeduction.objects.update_or_create(employee=self.employee, scheme=d["scheme"], period=d["period"],
            defaults={"employee_amount": d["employee_amount"], "employer_amount": d.get("employer_amount") or 0, "note": d.get("note", "").strip(), "created_by": self.user})
        return obj


# ---------------------------------------------------------------- Seragam (putaran 23, P5)
class UniformPurchaseForm(EmployeeByNik):
    """Satu aturan untuk input manual DAN impor. Karyawan harus AKTIF; tarif menurut jenis kelamin karyawan pada tanggal pembelian (disalin ke baris);
    pembelian ganda (karyawan+tanggal+jenis+ukuran) yang belum dibatalkan ditolak."""
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    purchase_date = forms.DateField(label="Tanggal pembelian", initial=date.today, widget=D())
    utype = forms.ModelChoiceField(label="Jenis seragam", queryset=UniformType.objects.none(), empty_label="— pilih —")
    size = forms.ModelChoiceField(label="Ukuran", queryset=UniformSize.objects.none(), empty_label="— pilih —")
    quantity = forms.IntegerField(label="Jumlah (pcs)", min_value=1, max_value=50, initial=1)
    note = forms.CharField(label="Catatan", required=False, max_length=300)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.employee, self.user, self.rate = instance, None, user, None
        self.fields["utype"].queryset = UniformType.objects.filter(is_active=True); self.fields["size"].queryset = UniformSize.objects.filter(is_active=True)

    def clean_purchase_date(self):
        v = self.cleaned_data["purchase_date"]
        if v > date.today(): raise forms.ValidationError("Tanggal pembelian tidak boleh di masa depan.")
        return v

    def clean(self):
        d = super().clean()
        if not self._find_employee(d): return d
        emp, when = self.employee, d.get("purchase_date")
        if when and when < emp.join_date: self.add_error("purchase_date", "Sebelum tanggal masuk karyawan.")
        if when and emp.gender in ("L", "P"):
            self.rate = services.uniform_rate_for(emp.gender, when)
            if not self.rate: self.add_error("purchase_date", "Belum ada tarif potongan seragam yang berlaku pada tanggal ini untuk jenis kelamin karyawan (tambahkan di Master Seragam).")
        elif when: self.add_error("nik", "Jenis kelamin karyawan belum diisi/tidak valid; lengkapi data karyawan dulu.")
        if when and d.get("utype") and d.get("size") and not self.errors:
            if UniformPurchase.objects.filter(employee=emp, purchase_date=when, utype=d["utype"], size=d["size"], voided_at__isnull=True).exists():
                raise forms.ValidationError("Pembelian yang sama (karyawan, tanggal, jenis, ukuran) sudah tercatat. Batalkan yang lama bila keliru.")
        return d

    def save(self):
        d = self.cleaned_data
        return UniformPurchase.objects.create(employee=self.employee, purchase_date=d["purchase_date"], utype=d["utype"], size=d["size"], gender=self.employee.gender, quantity=d["quantity"],
                                              rate_amount=self.rate.amount, deduction_amount=self.rate.amount * d["quantity"], note=d.get("note", "").strip(), created_by=self.user)


class UniformRateForm(forms.Form):
    gender = forms.ChoiceField(label="Jenis kelamin", choices=UniformRate.GENDERS)
    amount = RupiahField(label="Potongan per satuan (Rp)", min_value=0, max_value=100_000_000)
    effective_from = forms.DateField(label="Berlaku sejak", widget=D())

    def clean(self):
        d = super().clean()
        if d.get("gender") and d.get("effective_from") and UniformRate.objects.filter(gender=d["gender"], effective_from=d["effective_from"]).exists():
            raise forms.ValidationError("Sudah ada tarif untuk jenis kelamin dan tanggal berlaku ini. Tarif tidak dapat diubah; pakai tanggal berlaku yang lain.")
        return d


# ---------------------------------------------------------------- Bantuan
class AidForm(EmployeeByNik):
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    kind = forms.ChoiceField(label="Jenis bantuan", choices=Aid.KINDS)
    event_date = forms.DateField(label="Tanggal kejadian", widget=D())
    amount = RupiahField(label="Nominal (Rp)", min_value=1, max_value=2_000_000_000)
    description = forms.CharField(label="Keterangan", required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=1000)

    def __init__(self, *a, instance=None, **k):
        super().__init__(*a, **k); self.instance, self.employee = instance, None
        if instance:
            self.fields["nik"].disabled = True; self.initial.update(nik=instance.employee.nik, kind=instance.kind, event_date=instance.event_date,
                                                                    amount=instance.amount, description=instance.description)

    def clean(self):
        d = super().clean()
        if self.instance: self.employee = self.instance.employee
        elif not self._find_employee(d): return d
        if d.get("kind") and d.get("event_date"):
            dup = Aid.objects.filter(employee=self.employee, kind=d["kind"], event_date=d["event_date"])
            if self.instance: dup = dup.exclude(pk=self.instance.pk)
            if dup.exists(): raise forms.ValidationError("Bantuan jenis ini untuk karyawan dan tanggal kejadian yang sama sudah tercatat.")
        return d


# ---------------------------------------------------------------- Cuti hamil
class MaternityForm(EmployeeByNik):
    gender = "P"
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…", "data-q-gender": "P"}))
    due_date = forms.DateField(label="Perkiraan lahir (HPL)", widget=D())
    start_date = forms.DateField(label="Mulai cuti", required=False, widget=D(), help_text="Kosongkan = otomatis dari HPL.")
    end_date = forms.DateField(label="Selesai cuti", required=False, widget=D(), help_text="Kosongkan = otomatis dari HPL.")
    note = forms.CharField(label="Catatan administratif", required=False, max_length=500,
                           help_text="Jangan isi data medis (kondisi/diagnosa); itu hanya di modul Poli.")

    def __init__(self, *a, instance=None, **k):
        super().__init__(*a, **k); self.instance, self.employee = instance, None
        if instance:
            self.fields["nik"].disabled = True
            self.initial.update(nik=instance.employee.nik, due_date=instance.due_date, start_date=instance.start_date, end_date=instance.end_date, note=instance.note)

    def clean(self):
        d = super().clean()
        if self.instance: self.employee = self.instance.employee
        elif not self._find_employee(d): return d
        if not d.get("due_date"): return d
        auto_s, auto_e = services.default_maternity_window(d["due_date"])
        d["start_date"], d["end_date"] = d.get("start_date") or auto_s, d.get("end_date") or auto_e
        if d["end_date"] < d["start_date"]:
            self.add_error("end_date", "Tidak boleh sebelum tanggal mulai."); return d
        if services.maternity_overlaps(self.employee, d["start_date"], d["end_date"], self.instance.pk if self.instance else None).exists():
            raise forms.ValidationError("Karyawan ini sudah punya cuti hamil yang tumpang tindih dengan rentang tersebut.")
        return d


class FinishMaternityForm(forms.Form):
    delivery_date = forms.DateField(label="Tanggal lahir sebenarnya", required=False, widget=D())


# ---------------------------------------------------------------- Proyek & kerja harian
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["code", "name", "location", "start_date", "end_date", "status", "note"]
        widgets = {"start_date": D(), "end_date": D()}
        labels = {"code": "Kode proyek", "name": "Nama proyek", "location": "Lokasi", "start_date": "Mulai", "end_date": "Selesai (rencana/aktual)", "note": "Catatan"}

    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Project.objects.exclude(pk=self.instance.pk).filter(code=c).exists(): raise forms.ValidationError("Kode proyek sudah dipakai.")
        return c

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]: self.add_error("end_date", "Tidak boleh sebelum tanggal mulai.")
        return d


class ProjectWorkForm(forms.ModelForm):
    """Satu pekerja harian (bukan karyawan) pada satu hari: nama, pekerjaan, upah."""
    class Meta:
        model = ProjectWork
        fields = ["work_date", "worker_name", "activity", "wage", "note"]
        widgets = {"work_date": D()}
        labels = {"work_date": "Tanggal kerja", "worker_name": "Nama pekerja", "activity": "Mengerjakan apa", "note": "Catatan"}
    wage = RupiahField(label="Upah hari itu (Rp)", min_value=0)

    def __init__(self, *a, project, **k):
        super().__init__(*a, **k); self.project = project
        self.fields["wage"].min_value = 0

    def clean_worker_name(self):
        n = " ".join(self.cleaned_data["worker_name"].split())
        if not n: raise forms.ValidationError("Nama pekerja wajib diisi.")
        return n

    def clean_wage(self):
        w = self.cleaned_data["wage"]
        if w is None or w < 0 or w > 100_000_000: raise forms.ValidationError("Upah harus 0 – 100.000.000.")
        return w

    def clean_work_date(self):
        d = self.cleaned_data["work_date"]
        if d > date.today(): raise forms.ValidationError("Tanggal kerja tidak boleh di masa depan.")
        if d < self.project.start_date: raise forms.ValidationError(f"Sebelum proyek dimulai ({self.project.start_date:%d-%m-%Y}).")
        if self.project.end_date and d > self.project.end_date: raise forms.ValidationError(f"Setelah proyek berakhir ({self.project.end_date:%d-%m-%Y}).")
        return d

    def clean(self):
        d = super().clean()
        if d.get("work_date") and d.get("worker_name") and d.get("activity"):  # cegah dobel ketik (pekerja + tanggal + pekerjaan sama)
            dup = ProjectWork.objects.filter(project=self.project, work_date=d["work_date"], worker_name__iexact=d["worker_name"], activity__iexact=d["activity"])
            if self.instance.pk: dup = dup.exclude(pk=self.instance.pk)
            if dup.exists(): raise forms.ValidationError("Catatan yang sama (pekerja, tanggal, pekerjaan) sudah ada.")
        return d

    def save(self, commit=True):
        obj = super().save(commit=False); obj.project = self.project
        if commit: obj.save()
        return obj


# ---------------------------------------------------------------- Katering
class CateringForm(forms.ModelForm):
    class Meta:
        model = CateringOrder
        fields = ["date", "meal", "qty_large", "qty_small", "received_large", "received_small", "note"]
        widgets = {"date": D()}
        labels = {"date": "Tanggal", "meal": "Jam makan", "qty_large": "Dipesan — tepak besar", "qty_small": "Dipesan — tepak kecil",
                  "received_large": "Diterima — tepak besar", "received_small": "Diterima — tepak kecil", "note": "Catatan"}
        help_texts = {"received_large": "Kosongkan bila belum diterima.", "received_small": "Kosongkan bila belum diterima."}

    def clean(self):
        d = super().clean()
        if d.get("qty_large") is None or d.get("qty_small") is None: return d
        if d["qty_large"] + d["qty_small"] == 0: raise forms.ValidationError("Isi minimal satu tepak (besar atau kecil) yang dipesan.")
        rl, rs = d.get("received_large"), d.get("received_small")
        if (rl is None) != (rs is None): raise forms.ValidationError("Isi jumlah diterima untuk KEDUA ukuran (boleh 0), atau kosongkan keduanya.")
        if d.get("date") and d.get("meal"):
            dup = CateringOrder.objects.filter(date=d["date"], meal=d["meal"])
            if self.instance.pk: dup = dup.exclude(pk=self.instance.pk)
            if dup.exists(): raise forms.ValidationError("Rekap untuk tanggal dan jam makan ini sudah ada. Ubah yang sudah ada.")
        return d


# ---------------------------------------------------------------- Surat Peringatan
class WarningForm(EmployeeByNik):
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    level = forms.TypedChoiceField(label="Tingkat", choices=WarningLetter.LEVELS, coerce=int)
    issue_date = forms.DateField(label="Tanggal terbit", initial=date.today, widget=D())
    valid_until = forms.DateField(label="Berlaku sampai", required=False, widget=D(), help_text="Kosongkan = 6 bulan sejak tanggal terbit.")
    violation = forms.CharField(label="Pelanggaran", max_length=200)
    description = forms.CharField(label="Uraian / kronologi", required=False, widget=forms.Textarea(attrs={"rows": 4}), max_length=2000)

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.employee, self.skipped = None, False

    def clean(self):
        d = super().clean()
        if not self._find_employee(d): return d
        if not (d.get("level") and d.get("issue_date")): return d
        d["valid_until"] = d.get("valid_until") or services.add_months(d["issue_date"], 6)
        if d["valid_until"] < d["issue_date"]: self.add_error("valid_until", "Tidak boleh sebelum tanggal terbit.")
        active = [w for w in WarningLetter.objects.filter(employee=self.employee, revoked_at__isnull=True, valid_until__gte=d["issue_date"], issue_date__lte=d["issue_date"])]
        top = max((w.level for w in active), default=0)
        if top >= d["level"]:
            raise forms.ValidationError(f"Karyawan ini masih punya SP {top} yang berlaku pada tanggal tersebut. Terbitkan tingkat yang lebih tinggi, atau cabut SP lama terlebih dahulu.")
        self.skipped = d["level"] > top + 1  # lompat tingkat: hanya diperingatkan (kebijakan perusahaan)
        return d
