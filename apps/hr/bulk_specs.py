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
