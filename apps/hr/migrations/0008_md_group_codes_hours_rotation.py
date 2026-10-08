from datetime import time
from django.db import migrations, models

from apps.hr import rotation_table as rt


def to_md_codes(apps, schema_editor):
    """Kode mengikuti Aturan Pengaturan Jadwal Shift 2026: 2 shift = A…G; 3 shift/PACK = A_pack…G_pack. (0007 memakai A7/A7_pack.)"""
    G = apps.get_model("hr", "ShiftGroup")
    for g in G.objects.all():
        if g.pattern == "3_SHIFT" and g.code.endswith("7"): g.code = g.code[:-1] + "_pack"
        elif g.pattern == "2_SHIFT" and g.code.endswith("7_pack"): g.code = g.code[:-6]
        else: continue
        g.save(update_fields=["code"])


def from_md_codes(apps, schema_editor):
    G = apps.get_model("hr", "ShiftGroup")
    for g in G.objects.all():
        if g.pattern == "3_SHIFT" and g.code.endswith("_pack"): g.code = g.code[:-5] + "7"
        elif g.pattern == "2_SHIFT" and not g.code.endswith("_pack") and len(g.code) == 1: g.code = g.code + "7_pack"
        else: continue
        g.save(update_fields=["code"])


def hours_and_rotation(apps, schema_editor):
    """Jam resmi PAGI 06–14, SIANG 14–22, MALAM 22–06; tabel rotasi resmi dimuat bila shift PAGI/SIANG/MALAM sudah ada (DB kosong → dilewati)."""
    Shift, G, R = (apps.get_model("hr", n) for n in ("Shift", "ShiftGroup", "ShiftRotation"))
    for code, a, b in (("PAGI", 6, 14), ("SIANG", 14, 22), ("MALAM", 22, 6)):
        Shift.objects.filter(code=code).update(start=time(a), end=time(b), crosses_midnight=(b < a))
    by_code = {s.code: s for s in Shift.objects.filter(code__in=["PAGI", "SIANG", "MALAM"])}
    if len(by_code) < 3: return
    for pattern in (rt.P2, rt.P3):
        for code, cells in rt.official(pattern).items():
            g, _ = G.objects.get_or_create(code=code, defaults={"pattern": pattern})
            for wd, sc in enumerate(cells): R.objects.update_or_create(group=g, weekday=wd, defaults={"shift": by_code[sc] if sc else None})


class Migration(migrations.Migration):
    dependencies = [("hr", "0007_group_codes_gs_hours")]
    operations = [
        migrations.RemoveConstraint(model_name="shiftgroup", name="shift_group_pack_suffix_matches_pattern"),
        migrations.RunPython(to_md_codes, from_md_codes),
        migrations.AddConstraint(model_name="shiftgroup", constraint=models.CheckConstraint(
            name="shift_group_pack_suffix_matches_pattern",
            condition=models.Q(models.Q(("code__endswith", "_pack"), ("pattern", "3_SHIFT")), models.Q(("pattern", "2_SHIFT"), models.Q(("code__endswith", "_pack"), _negated=True)), _connector="OR"))),
        migrations.AlterField(model_name="shiftgroup", name="pattern", field=models.CharField(choices=[("2_SHIFT", "2 shift (Pagi–Siang)"), ("3_SHIFT", "3 shift / PACK (Pagi–Siang–Malam)")], max_length=8, verbose_name="Pola")),
        migrations.RunPython(hours_and_rotation, migrations.RunPython.noop),
    ]
