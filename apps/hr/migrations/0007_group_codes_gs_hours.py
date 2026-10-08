from datetime import time
from django.db import migrations, models


def rename_forward(apps, schema_editor):
    """Pola 3 shift: 'A_pack' → 'A7'; pola 2 shift: 'A' → 'A7_pack'. Tabel rotasi tetap utuh (hanya kode yang berubah)."""
    G = apps.get_model("hr", "ShiftGroup")
    for g in G.objects.all():
        if g.pattern == "3_SHIFT" and g.code.endswith("_pack"): g.code = g.code[:-5] + "7"
        elif g.pattern == "2_SHIFT" and not g.code.endswith("_pack"): g.code = g.code + "7_pack"
        g.save(update_fields=["code"])


def rename_back(apps, schema_editor):
    G = apps.get_model("hr", "ShiftGroup")
    for g in G.objects.all():
        if g.pattern == "3_SHIFT" and g.code.endswith("7"): g.code = g.code[:-1] + "_pack"
        elif g.pattern == "2_SHIFT" and g.code.endswith("7_pack"): g.code = g.code[:-6]
        g.save(update_fields=["code"])


def gs_hours(apps, schema_editor):
    """Semua GS masuk 08:00; pulang 16:00 (hari biasa), 14:00 / 12:00 (sebelum libur GS)."""
    Shift = apps.get_model("hr", "Shift")
    Shift.objects.filter(is_gs=True).update(start=time(8))
    for code, end in (("GS-16", 16), ("GS-14", 14), ("GS-12", 12)): Shift.objects.filter(code=code).update(end=time(end))


class Migration(migrations.Migration):
    dependencies = [("hr", "0006_shift_master_groups_rotation")]
    operations = [
        migrations.RemoveConstraint(model_name="shiftgroup", name="shift_group_pack_suffix_matches_pattern"),
        migrations.RunPython(rename_forward, rename_back),
        migrations.AddConstraint(model_name="shiftgroup", constraint=models.CheckConstraint(
            name="shift_group_pack_suffix_matches_pattern",
            condition=models.Q(models.Q(("code__endswith", "_pack"), ("pattern", "2_SHIFT")), models.Q(("pattern", "3_SHIFT"), models.Q(("code__endswith", "_pack"), _negated=True)), _connector="OR"))),
        migrations.AlterField(model_name="shiftgroup", name="pattern", field=models.CharField(choices=[("2_SHIFT", "2 shift / pack (Pagi–Siang)"), ("3_SHIFT", "3 shift (Pagi–Siang–Malam)")], max_length=8, verbose_name="Pola")),
        migrations.AddField(model_name="employee", name="gs_short", field=models.CharField(choices=[("14", "GS-14 (08:00–14:00)"), ("12", "GS-12 (08:00–12:00)")], default="14", max_length=2, verbose_name="GS sebelum libur")),
        migrations.RunPython(gs_hours, migrations.RunPython.noop),
    ]
