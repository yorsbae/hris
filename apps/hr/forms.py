"""Form pengajuan. Validasi di server adalah otoritas; JS di halaman hanya menyembunyikan field yang tidak relevan."""
from django import forms
from apps.core.models import Role
from .models import ChangeRequest, Department, Employee, Position, Shift

GROUPS = {  # tipe → field payload yang dipakai
    "range": (("izin", "cuti", "sakit", "izin_khusus"), ("start_date", "end_date", "reason")),
    "point": (("izin_terlambat", "izin_pulang"), ("date", "time", "reason")),
    "swap_shift": (("tukar_shift",), ("date", "shift", "reason")),
    "swap_off": (("tukar_libur",), ("date", "date_to", "reason")),
    "dept": (("mutasi_dept", "rotasi"), ("department", "effective_date", "reason")),
    "pos": (("mutasi_jabatan", "promosi", "demosi"), ("position", "effective_date", "reason")),
    "shift": (("shift",), ("shift", "effective_date", "reason")),
    "status": (("status",), ("status", "effective_date", "reason")),
}
FIELDS_BY_TYPE = {t: f for types, f in GROUPS.values() for t in types}
LABELS = {
    "mutasi_dept": "Mutasi departemen", "mutasi_jabatan": "Mutasi jabatan", "promosi": "Promosi", "demosi": "Demosi",
    "rotasi": "Rotasi", "status": "Perubahan status", "shift": "Perubahan shift", "izin": "Izin", "cuti": "Cuti",
    "sakit": "Sakit", "izin_terlambat": "Izin terlambat", "izin_pulang": "Izin pulang", "izin_khusus": "Izin khusus",
    "tukar_shift": "Tukar shift", "tukar_libur": "Tukar libur",
}
HRD_ONLY_TYPES = {"status"}  # Admin Departemen tidak boleh mengajukan perubahan status karyawan
RANGE_TYPES = set(GROUPS["range"][0])
ACTIVE = ("submitted", "pending", "approved", "executed")


def allowed_types(user):
    return [t for t in ChangeRequest.TYPES if user.role != Role.DEPT_ADMIN or t not in HRD_ONLY_TYPES]


class RequestForm(forms.Form):
    type = forms.ChoiceField(label="Jenis pengajuan")
    nik = forms.CharField(label="NIK karyawan", max_length=20)
    start_date = forms.DateField(label="Tanggal mulai", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Tanggal selesai", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date = forms.DateField(label="Tanggal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Diganti ke tanggal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(label="Jam", required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    effective_date = forms.DateField(label="Tanggal efektif", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    department = forms.ModelChoiceField(Department.objects.order_by("name"), label="Departemen tujuan", required=False)
    position = forms.ModelChoiceField(Position.objects.order_by("name"), label="Jabatan tujuan", required=False)
    shift = forms.ModelChoiceField(Shift.objects.order_by("name"), label="Shift tujuan", required=False)
    status = forms.ChoiceField(label="Status baru", required=False, choices=[("", "---"), ("aktif", "aktif"), ("nonaktif", "nonaktif")])
    reason = forms.CharField(label="Alasan / keterangan", widget=forms.Textarea(attrs={"rows": 3}), max_length=1000)

    def __init__(self, *a, user, **k):
        super().__init__(*a, **k)
        self.user, self.employee = user, None
        self.fields["type"].choices = [(t, LABELS[t]) for t in allowed_types(user)]

    def clean(self):
        d = super().clean()
        t = d.get("type")
        if not t: return d
        for f in FIELDS_BY_TYPE[t]:  # semua field milik tipe ini wajib terisi
            if d.get(f) in (None, ""): self.add_error(f, "Wajib diisi.")
        emp_qs = Employee.objects.select_related("department").filter(nik=d.get("nik", "").strip(), status="aktif")
        if self.user.role == Role.DEPT_ADMIN: emp_qs = emp_qs.filter(department_id=self.user.department_id)
        self.employee = emp_qs.first()  # di luar scope = "tidak ditemukan" (tidak membocorkan keberadaan)
        if not self.employee:
            self.add_error("nik", "Karyawan aktif dengan NIK ini tidak ditemukan.")
            return d
        if t in RANGE_TYPES and d.get("start_date") and d.get("end_date"):
            if d["end_date"] < d["start_date"]: self.add_error("end_date", "Tidak boleh sebelum tanggal mulai.")
            elif self._overlaps(t, d["start_date"], d["end_date"]):
                raise forms.ValidationError("Sudah ada pengajuan izin/cuti/sakit yang tumpang tindih pada tanggal tersebut.")
        if t == "mutasi_dept" or t == "rotasi":
            if d.get("department") and d["department"].pk == self.employee.department_id:
                self.add_error("department", "Sama dengan departemen saat ini.")
        if t in ("mutasi_jabatan", "promosi", "demosi") and d.get("position") and d["position"].pk == self.employee.position_id:
            self.add_error("position", "Sama dengan jabatan saat ini.")
        if t == "shift" and d.get("shift") and d["shift"].pk == self.employee.shift_id:
            self.add_error("shift", "Sama dengan shift saat ini.")
        if t == "status" and d.get("status") == self.employee.status:
            self.add_error("status", "Sama dengan status saat ini.")
        return d

    def _overlaps(self, t, start, end):
        for r in ChangeRequest.objects.filter(employee=self.employee, type__in=RANGE_TYPES, status__in=ACTIVE):
            s, e = r.payload.get("start_date"), r.payload.get("end_date")
            if s and e and s <= end.isoformat() and e >= start.isoformat(): return True
        return False

    def payload(self):
        """Susun payload JSON. Kunci department_id/position_id/shift_id/status/effective_date dibaca langsung oleh services._execute."""
        d, out = self.cleaned_data, {}
        for f in FIELDS_BY_TYPE[d["type"]]:
            v = d[f]
            if f in ("department", "position", "shift"):
                out[f"{f}_id"], out[f"{f}_name"] = v.pk, str(getattr(v, "name", v))
            elif hasattr(v, "isoformat"): out[f] = v.isoformat()
            else: out[f] = v
        return out
