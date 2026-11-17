"""Form pengajuan. Validasi di server adalah otoritas; JS di halaman hanya menyembunyikan field yang tidak relevan."""
from django import forms
from django.utils import timezone
from apps.core.models import Role
from apps.core.scope import DEPT_ACTORS
from . import leave, schedule
from django.db.models import Case, IntegerField, Value, When
from .models import ChangeRequest, Department, Employee, Position, Shift, ShiftGroup

GROUPS = {  # tipe → field payload yang dipakai
    "range": (("izin", "cuti", "sakit", "izin_khusus"), ("start_date", "end_date", "reason")),   # izin_khusus: hanya data lama (tidak ditawarkan lagi)
    "point": (("izin_terlambat", "izin_pulang"), ("date", "time", "reason")),
    "swap_shift": (("tukar_shift",), ("date", "shift_group", "partner_nik", "reason")),   # shift tujuan = kelompok (GS, GS/N, A7…); jamnya dihitung dari rotasi/aturan GS
    "swap_off": (("tukar_libur",), ("date", "date_to", "partner_nik", "reason")),
    "dept": (("mutasi_dept", "rotasi"), ("department", "effective_date", "reason")),
    "pos": (("mutasi_jabatan", "promosi", "demosi"), ("position", "effective_date", "reason")),
    "shift": (("shift",), ("shift_group", "effective_date", "reason")),   # Perubahan shift = ganti kelompok: GS, GS/N, A7…G7, A7Pack…G7Pack
    "status": (("status",), ("status", "effective_date", "reason")),
    "pay": (("status_kerja",), ("pay_basis", "effective_date", "reason")),   # status karyawan: bulanan/harian/borongan/magang/prakerin
    "extra": (("standby", "lembur"), ("date", "time", "time_to", "reason")),  # form khusus: apps/hr/overtime.py
}
DEDICATED = {"standby", "lembur"}  # punya form sendiri (banyak karyawan sekaligus); tidak muncul di form pengajuan umum
FIELDS_BY_TYPE = {t: f for types, f in GROUPS.values() for t in types}
LABELS = {
    "mutasi_dept": "Mutasi / rotasi departemen", "mutasi_jabatan": "Mutasi jabatan (promosi / demosi)", "promosi": "Promosi", "demosi": "Demosi",
    "rotasi": "Rotasi", "status": "Aktif / nonaktif karyawan", "status_kerja": "Perubahan status karyawan (harian / borongan / magang)", "shift": "Perubahan shift", "izin": "Izin", "cuti": "Cuti",
    "sakit": "Sakit", "izin_terlambat": "Izin terlambat", "izin_pulang": "Izin pulang", "izin_khusus": "Izin khusus",
    "tukar_shift": "Tukar shift", "tukar_libur": "Tukar libur", "standby": "Stand By (kerja saat istirahat)", "lembur": "Lembur",
}
# Submenu Pengajuan (putaran 20): tiap grup = halaman daftar sendiri (/requests/g/<slug>/); semua jenis tetap satu model/alur.
REQUEST_GROUPS = {
    "izin": ("Izin & Cuti", ("izin", "cuti", "sakit", "izin_terlambat", "izin_pulang", "izin_khusus")),
    "mutasi": ("Mutasi & Promosi", ("mutasi_dept", "mutasi_jabatan", "promosi", "demosi", "rotasi")),
    "status": ("Perubahan Status", ("status_kerja", "status")),
    "jadwal": ("Shift & Tukar Jadwal", ("shift", "tukar_shift", "tukar_libur")),
    "lembur": ("Stand By & Lembur", ("standby", "lembur")),
}
OPTIONAL_FIELDS = {"partner_nik"}  # tidak wajib: kosong = tukar sendiri (1 orang), terisi = tukar dengan rekan (2 orang)
HRD_ONLY_TYPES = {"status", "status_kerja"}
LEGACY_TYPES = {"promosi", "demosi", "rotasi", "izin_khusus"}  # digabung/dihapus: promosi & demosi → mutasi jabatan, rotasi → mutasi departemen. Data lama tetap terbaca, tidak bisa dibuat baru  # Admin Departemen tidak boleh mengajukan perubahan status karyawan
RANGE_TYPES = set(GROUPS["range"][0])
ACTIVE = ("submitted", "pending", "approved", "executed")


def allowed_types(user):
    return [t for t in ChangeRequest.TYPES if t not in LEGACY_TYPES and (user.role not in DEPT_ACTORS or t not in HRD_ONLY_TYPES)]


def _group_choices():
    order = Case(When(pattern=ShiftGroup.GS, then=Value(0)), When(pattern=ShiftGroup.GSN, then=Value(1)), When(pattern=ShiftGroup.P2, then=Value(2)), default=Value(3), output_field=IntegerField())
    return ShiftGroup.objects.order_by(order, "code")


class RequestForm(forms.Form):
    type = forms.ChoiceField(label="Jenis pengajuan")
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Ketik NIK atau nama…"}))
    start_date = forms.DateField(label="Tanggal mulai", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Tanggal selesai", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date = forms.DateField(label="Tanggal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Diganti ke tanggal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(label="Jam", required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    effective_date = forms.DateField(label="Tanggal efektif", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    department = forms.ModelChoiceField(Department.objects.order_by("name"), label="Departemen tujuan", required=False)
    position = forms.ModelChoiceField(Position.objects.order_by("name"), label="Jabatan tujuan", required=False)
    shift_group = forms.ModelChoiceField(ShiftGroup.objects.none(), label="Shift tujuan", required=False)  # perubahan shift & tukar shift: GS, GS/N, A7…G7, A7Pack…G7Pack
    pay_basis = forms.ChoiceField(label="Status karyawan baru", required=False, choices=[("", "---")] + Employee.PAY_BASIS)
    partner_nik = forms.CharField(label="NIK rekan tukar (kosongkan bila menukar sendiri)", required=False, max_length=20,
                                  widget=forms.TextInput(attrs={"data-lookup": "employee", "placeholder": "Kosong = tukar sendiri (1 orang)"}))
    status = forms.ChoiceField(label="Status baru", required=False, choices=[("", "---"), ("aktif", "aktif"), ("nonaktif", "nonaktif")])
    reason = forms.CharField(label="Alasan / keterangan", widget=forms.Textarea(attrs={"rows": 3}), max_length=1000)

    def __init__(self, *a, user, group=None, **k):
        super().__init__(*a, **k)
        self.user, self.employee = user, None
        types = [t for t in allowed_types(user) if t not in DEDICATED]
        if group in REQUEST_GROUPS: types = [t for t in types if t in REQUEST_GROUPS[group][1]]  # buat dari submenu: hanya jenis grup itu
        self.fields["type"].choices = [(t, LABELS[t]) for t in types]
        self.fields["shift_group"].queryset = _group_choices(); self.fields["shift_group"].label_from_instance = lambda g: g.label

    def clean(self):
        d = super().clean()
        t = d.get("type")
        if not t: return d
        has_partner = t in schedule.SWAP_TYPES and bool((d.get("partner_nik") or "").strip())
        for f in FIELDS_BY_TYPE[t]:  # semua field milik tipe ini wajib terisi (kecuali yang opsional; shift tujuan tidak perlu bila menukar dengan rekan)
            if f in OPTIONAL_FIELDS or (f == "shift_group" and t == "tukar_shift" and has_partner): continue
            if d.get(f) in (None, ""): self.add_error(f, "Wajib diisi.")
        emp_qs = Employee.objects.select_related("department").filter(nik=d.get("nik", "").strip(), status="aktif")
        if self.user.role in DEPT_ACTORS: emp_qs = emp_qs.filter(department_id=self.user.department_id)
        self.employee = emp_qs.first()  # di luar scope = "tidak ditemukan" (tidak membocorkan keberadaan)
        if not self.employee:
            self.add_error("nik", "Karyawan aktif dengan NIK ini tidak ditemukan.")
            return d
        if t in RANGE_TYPES and d.get("start_date") and d.get("end_date"):
            if d["end_date"] < d["start_date"]: self.add_error("end_date", "Tidak boleh sebelum tanggal mulai.")
            elif self._overlaps(t, d["start_date"], d["end_date"]):
                raise forms.ValidationError("Sudah ada pengajuan izin/cuti/sakit yang tumpang tindih pada tanggal tersebut.")
        if t == "cuti" and d.get("start_date") and d.get("end_date") and d["end_date"] >= d["start_date"]:
            msg = leave.check_request(self.employee, d["start_date"], d["end_date"])
            if msg: raise forms.ValidationError(msg)
        self.partner = None
        if has_partner:  # rekan tukar: karyawan aktif lain; Admin Departemen hanya rekan dari departemennya (di luar scope = "tidak ditemukan")
            pq = Employee.objects.select_related("department").filter(nik=d["partner_nik"].strip(), status="aktif")
            if self.user.role in DEPT_ACTORS: pq = pq.filter(department_id=self.user.department_id)
            self.partner = pq.first()
            if not self.partner: self.add_error("partner_nik", "Rekan tukar (karyawan aktif dengan NIK ini) tidak ditemukan.")
            elif self.partner.pk == self.employee.pk: self.add_error("partner_nik", "Rekan tukar harus karyawan lain; untuk menukar liburmu sendiri kosongkan kolom ini."); self.partner = None
            if not self.partner: return d
        if t in schedule.SWAP_TYPES: self._clean_swap(t, d)
        if t in ("mutasi_dept", "rotasi"):
            if d.get("department") and d["department"].pk == self.employee.department_id:
                self.add_error("department", "Sama dengan departemen saat ini.")
        if t in ("mutasi_jabatan", "promosi", "demosi") and d.get("position") and d["position"].pk == self.employee.position_id:
            self.add_error("position", "Sama dengan jabatan saat ini.")
        if t == "shift" and d.get("shift_group") and d["shift_group"].pk == self.employee.shift_group_id:
            self.add_error("shift_group", "Sama dengan shift saat ini.")
        if t == "status_kerja" and d.get("pay_basis") == self.employee.pay_basis:
            self.add_error("pay_basis", "Sama dengan status karyawan saat ini.")
        if t == "status" and d.get("status") == self.employee.status:
            self.add_error("status", "Sama dengan status saat ini.")
        return d

    def _clean_swap(self, t, d):
        """Tukar shift/libur (1 orang atau 2 orang): tanggal tidak boleh lampau, tidak boleh bentrok dengan tukar lain / penyesuaian yang ada /
        izin-cuti-sakit — untuk KEDUA karyawan — dan jadwal efektif keduanya harus memenuhi syarat jenis tukarnya (schedule.build_rows)."""
        today, emp, partner = timezone.localdate(), self.employee, self.partner
        dates = [(f, d.get(f)) for f in (("date",) if t == "tukar_shift" else ("date", "date_to")) if d.get(f)]
        for f, v in dates:
            if v < today: self.add_error(f, "Tidak boleh tanggal yang sudah lewat.")
        if self.errors: return
        if t == "tukar_libur" and d.get("date") == d.get("date_to"):
            self.add_error("date_to", "Harus berbeda dari tanggal libur yang diganti."); return
        self.swap_shift = None
        if t == "tukar_shift" and not partner and d.get("shift_group"):
            g = d["shift_group"]; self.swap_shift = schedule.shift_of_group(emp, g, d["date"])
            if not self.swap_shift: self.add_error("shift_group", f"Kelompok {g.label} libur pada {d['date']:%d-%m-%Y}; pilih shift lain."); return
            cur = schedule.base_schedule(emp, d["date"])  # jadwal dasar: penyesuaian lain dilaporkan sebagai bentrok di bawah
            if not cur["off"] and cur["shift"] and cur["shift"].pk == self.swap_shift.pk: self.add_error("shift_group", "Sama dengan shift reguler karyawan pada tanggal itu."); return
        payload = {f: v.isoformat() for f, v in dates}
        if partner: payload["partner_id"] = partner.pk
        for who in (emp, partner):
            if not who: continue
            hit = schedule.live_conflict(who, t, payload)
            if hit: raise forms.ValidationError(f"Tanggal {hit} sudah dipakai pengajuan tukar jadwal lain atau sudah punya penyesuaian jadwal" + (f" ({who.name})." if partner else "."))
            for f, v in dates:
                if self._overlaps(t, v, v, types=RANGE_TYPES, emp=who): self.add_error(f, f"{who.name} sedang izin/cuti/sakit pada tanggal ini." if partner else "Karyawan sedang izin/cuti/sakit pada tanggal ini.")
        if self.errors: return
        try:
            schedule.build_rows(t, emp, partner, {**payload, **({"shift_id": self.swap_shift.pk} if self.swap_shift else {})})
        except ValueError as e:
            raise forms.ValidationError(str(e))

    def _overlaps(self, t, start, end, types=None, emp=None):
        for r in ChangeRequest.objects.filter(employee=emp or self.employee, type__in=types or RANGE_TYPES, status__in=ACTIVE):
            s, e = r.payload.get("start_date"), r.payload.get("end_date")
            if s and e and s <= end.isoformat() and e >= start.isoformat(): return True
        return False

    def payload(self):
        """Susun payload JSON. Kunci department_id/position_id/shift_id/status/effective_date dibaca langsung oleh services._execute."""
        d, out = self.cleaned_data, {}
        for f in FIELDS_BY_TYPE[d["type"]]:
            v = d[f]
            if f in OPTIONAL_FIELDS or v in (None, ""): continue  # rekan tukar ditambahkan di bawah (id + nama); shift kosong bila menukar dengan rekan
            if f == "pay_basis": out["pay_basis"], out["pay_basis_name"] = v, dict(Employee.PAY_BASIS)[v]
            elif f in ("department", "position", "shift", "shift_group"):
                out[f"{f}_id"], out[f"{f}_name"] = v.pk, str(getattr(v, "name", v))
            elif hasattr(v, "isoformat"): out[f] = v.isoformat()
            else: out[f] = v
        if getattr(self, "swap_shift", None): out["shift_id"] = self.swap_shift.pk   # shift hasil penerjemahan kelompok → jam kerja pada tanggal tukar
        if getattr(self, "partner", None): out.update(partner_id=self.partner.pk, partner_nik=self.partner.nik, partner_name=self.partner.name)
        return out
