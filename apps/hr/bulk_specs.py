"""Spesifikasi impor massal modul HR: master departemen/jabatan/shift dan tabel rotasi kelompok shift (putaran 18)."""
from django import forms
from django.core.exceptions import ValidationError
from apps.core.bulk import Spec
from .emp_forms import DepartmentForm, PositionForm, ShiftForm
from .models import Department, Position, Shift, ShiftGroup, ShiftRotation

YES = {"ya", "y", "yes", "true", "1", "on", "aktif"}
def _b(v, default=False): return "on" if (v.strip().lower() in YES if v.strip() else default) else ""


def _dept(r, ctx):
    pc, parent = r.get("parent_code", "").upper(), ""
    if pc:
        p = Department.objects.filter(code=pc).first()
        if p: parent = p.pk
        elif pc not in ctx["before"] or pc == r.get("code", "").upper(): raise ValueError(f"Induk dengan kode '{pc}' tidak ada (atau belum muncul di baris sebelumnya).")
    return {"code": r.get("code", ""), "name": r.get("name", ""), "parent": parent}


def _dept_after(o, r, ctx):  # induk yang baru dibuat pada baris sebelumnya
    pc = r.get("parent_code", "").upper()
    if pc and o.parent_id is None: o.parent = Department.objects.get(code=pc); o.save(update_fields=["parent"])


DEPARTMENT = Spec("departemen", "hr", "departemen", "/master/department/", Department, "code", DepartmentForm,
                  ["code", "name", "parent_code"], ["code", "name"], ["PROD", "Produksi", ""], _dept, after_save=_dept_after,
                  hint="Induk dirujuk lewat kode (boleh induk yang ada di baris sebelumnya).")
POSITION = Spec("jabatan", "hr", "jabatan", "/master/position/", Position, "name", PositionForm,
                ["name", "level"], ["name"], ["Operator", "1"], lambda r, c: {"name": r.get("name", ""), "level": r.get("level") or 0}, case_insensitive_key=True)
SHIFT = Spec("shift", "hr", "shift", "/master/shift/", Shift, "code", ShiftForm,
             ["code", "name", "start", "end", "crosses_midnight", "is_gs", "active"], ["code", "name", "start", "end"],
             ["GS-16", "General Shift 16", "08:00", "16:00", "tidak", "ya", "ya"],
             lambda r, c: {"code": r.get("code", ""), "name": r.get("name", ""), "start": r.get("start", ""), "end": r.get("end", ""),
                           "crosses_midnight": _b(r.get("crosses_midnight", "")), "is_gs": _b(r.get("is_gs", "")), "active": _b(r.get("active", ""), True)},
             hint="Jam format HH:MM. Kolom ya/tidak: ya, y, true, 1.")

DAYS = {"senin": 0, "selasa": 1, "rabu": 2, "kamis": 3, "jumat": 4, "jum'at": 4, "sabtu": 5, "minggu": 6, **{str(i): i for i in range(7)}}


class RotationForm(forms.Form):
    """Satu sel tabel rotasi. Kelompok dibuat otomatis bila belum ada: kode A–G → pola 2 shift; A_pack–G_pack → pola 3 shift/PACK."""
    group = forms.CharField(max_length=12); weekday = forms.CharField(); shift = forms.CharField(required=False)

    def __init__(self, data=None, instance=None, **k): super().__init__(data, **k)

    def clean(self):
        d = super().clean()
        if self.errors: return d
        code = d["group"].strip().upper().replace("_PACK", "_pack")
        import re
        if not re.fullmatch(r"[A-G](_pack)?", code): raise ValidationError("Kode kelompok harus A–G (2 shift) atau A_pack–G_pack (3 shift/PACK).")
        wd = DAYS.get(d["weekday"].strip().lower())
        if wd is None: raise ValidationError("Hari harus 0–6 (0=Senin) atau nama hari (senin … minggu).")
        pattern = ShiftGroup.P3 if code.endswith("_pack") else ShiftGroup.P2
        sh = None
        if d["shift"].strip():
            sh = Shift.objects.filter(code__iexact=d["shift"].strip()).first()
            if not sh: raise ValidationError(f"Shift dengan kode '{d['shift']}' tidak ada.")
            tmp = ShiftRotation(group=ShiftGroup(code=code, pattern=pattern), shift=sh, weekday=wd)
            if not sh.active: raise ValidationError("Shift nonaktif tidak boleh dipakai di rotasi.")
            if sh.is_gs: raise ValidationError("Shift GS tidak ikut rotasi kelompok.")
            if pattern == ShiftGroup.P2 and sh.crosses_midnight: raise ValidationError("Pola 2 shift (A–G) tidak boleh memakai shift yang melewati tengah malam (Malam hanya untuk A_pack–G_pack).")
        self.cleaned = (code, pattern, wd, sh); return d

    def save(self):
        code, pattern, wd, sh = self.cleaned
        g, _ = ShiftGroup.objects.get_or_create(code=code, defaults={"pattern": pattern})
        row, created = ShiftRotation.objects.update_or_create(group=g, weekday=wd, defaults={"shift": sh}); return row


ROTATION = Spec("rotasi", "hr", "tabel rotasi kelompok shift", "/master/shift/", None, "group", RotationForm,
                ["group", "weekday", "shift_code"], ["group", "weekday"], ["A", "senin", "PAGI"],
                lambda r, c: {"group": r.get("group", ""), "weekday": r.get("weekday", ""), "shift": r.get("shift_code", "")},
                key_fn=lambda r: f"{r.get('group', '').upper()}|{r.get('weekday', '').lower()}",
                hint="Satu baris = satu sel (kelompok × hari). Hari: senin…minggu atau 0–6. shift_code kosong = libur kelompok.",
                notes=["Kelompok dibuat otomatis: A–G = pola 2 shift (3 Pagi, 3 Siang, 1 Libur per hari); A_pack–G_pack = pola 3 shift/PACK (2 Pagi, 2 Siang, 2 Malam, 1 Libur per hari)."])
