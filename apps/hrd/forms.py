"""Form Operasional HRD. Validasi di server adalah otoritas; field NIK diketik (bukan dropdown 3.000 baris)."""
import re
from datetime import date
from django import forms
from apps.hr.models import Employee
from . import services
from .models import Aid, BpjsMembership, BpjsScheme, BpjsState, CateringOrder, MaternityLeave, Project, ProjectDailyLog

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


# ---------------------------------------------------------------- Bantuan
class AidForm(EmployeeByNik):
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    kind = forms.ChoiceField(label="Jenis bantuan", choices=Aid.KINDS)
    event_date = forms.DateField(label="Tanggal kejadian", widget=D())
    amount = forms.IntegerField(label="Nominal (Rp)", min_value=1, max_value=2_000_000_000)
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
            dup = Aid.objects.filter(employee=self.employee, kind=d["kind"], event_date=d["event_date"]).exclude(status="ditolak")
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


NIK_SPLIT = re.compile(r"[\s,;]+")
MAX_WORKERS = 300


class ProjectLogForm(forms.ModelForm):
    worker_niks = forms.CharField(label="Pekerja (NIK)", required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                  help_text=f"Pisahkan dengan baris baru/koma. Maks {MAX_WORKERS}. Kosongkan bila hanya jumlah orang yang dicatat.")

    class Meta:
        model = ProjectDailyLog
        fields = ["work_date", "activity", "worker_niks", "headcount", "note"]
        widgets = {"work_date": D(), "activity": forms.Textarea(attrs={"rows": 4})}
        labels = {"work_date": "Tanggal kerja", "activity": "Pekerjaan yang dilakukan", "headcount": "Jumlah orang (bila NIK tidak diisi)", "note": "Catatan"}

    def __init__(self, *a, project, **k):
        super().__init__(*a, **k); self.project, self.workers = project, []
        if self.instance.pk: self.initial["worker_niks"] = "\n".join(self.instance.workers.order_by("nik").values_list("nik", flat=True))

    def clean_work_date(self):
        d = self.cleaned_data["work_date"]
        if d > date.today(): raise forms.ValidationError("Catatan harian tidak boleh untuk tanggal masa depan.")
        if d < self.project.start_date: raise forms.ValidationError(f"Sebelum proyek dimulai ({self.project.start_date:%d-%m-%Y}).")
        if self.project.end_date and d > self.project.end_date: raise forms.ValidationError(f"Setelah proyek berakhir ({self.project.end_date:%d-%m-%Y}).")
        return d

    def clean_worker_niks(self):
        niks = list(dict.fromkeys(n for n in NIK_SPLIT.split(self.cleaned_data["worker_niks"].strip()) if n))
        if len(niks) > MAX_WORKERS: raise forms.ValidationError(f"Maksimal {MAX_WORKERS} pekerja per catatan.")
        found = {e.nik: e for e in Employee.objects.filter(nik__in=niks, status="aktif")}
        missing = [n for n in niks if n not in found]
        if missing: raise forms.ValidationError("NIK tidak ditemukan/tidak aktif: " + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
        self.workers = list(found.values())
        return "\n".join(niks)

    def save(self, commit=True):
        obj = super().save(commit=False); obj.project = self.project
        if self.workers: obj.headcount = len(self.workers)
        if commit:
            obj.save(); obj.workers.set(self.workers)
        return obj


# ---------------------------------------------------------------- Katering
class CateringForm(forms.ModelForm):
    class Meta:
        model = CateringOrder
        fields = ["date", "meal", "department", "qty_large", "qty_small", "price_large", "price_small", "vendor", "note"]
        widgets = {"date": D()}
        labels = {"date": "Tanggal", "meal": "Waktu makan", "department": "Departemen (kosong = umum)", "qty_large": "Tepak besar (jumlah)",
                  "qty_small": "Tepak kecil (jumlah)", "price_large": "Harga tepak besar (Rp)", "price_small": "Harga tepak kecil (Rp)", "vendor": "Vendor", "note": "Catatan"}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        from apps.hr.models import Department
        self.fields["department"].queryset = Department.objects.order_by("name"); self.fields["department"].required = False

    def clean(self):
        d = super().clean()
        if d.get("qty_large") is None or d.get("qty_small") is None: return d
        if d["qty_large"] + d["qty_small"] == 0: raise forms.ValidationError("Isi minimal satu tepak (besar atau kecil).")
        if d.get("date") and d.get("meal"):
            dup = CateringOrder.objects.filter(date=d["date"], meal=d["meal"], department=d.get("department")).exclude(status="batal")
            if self.instance.pk: dup = dup.exclude(pk=self.instance.pk)
            if dup.exists(): raise forms.ValidationError("Pesanan untuk tanggal, waktu makan, dan departemen ini sudah ada. Ubah pesanan yang ada.")
        return d


class ReceiveCateringForm(forms.Form):
    received_large = forms.IntegerField(label="Tepak besar diterima", min_value=0)
    received_small = forms.IntegerField(label="Tepak kecil diterima", min_value=0)
