"""Putaran 58 — alur run gaji dibalik: HRD menyerahkan data → Payroll menghitung, menetapkan, membayar (tidak ada lagi persetujuan HRD setelah hitung).
Kolom submitted_* → handed_* (HRD menyerahkan), approved_* → finalized_* (Payroll menetapkan). Status lama dipetakan: diajukan → dihitung (harus ditetapkan ulang oleh Payroll),
disetujui → ditetapkan. Pemetaan dapat dibalik."""
from django.conf import settings
from django.db import migrations, models


def forward(apps, schema_editor):
    R = apps.get_model("payroll", "PayrollRun")
    R.objects.filter(status="diajukan").update(status="dihitung")
    R.objects.filter(status="disetujui").update(status="ditetapkan")


def backward(apps, schema_editor):
    R = apps.get_model("payroll", "PayrollRun")
    R.objects.filter(status="ditetapkan").update(status="disetujui")
    R.objects.filter(status="diserahkan").update(status="draft")


class Migration(migrations.Migration):
    dependencies = [("payroll", "0002_seed_components"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.RenameField("payrollrun", "submitted_at", "handed_at"),
        migrations.RenameField("payrollrun", "submitted_by", "handed_by"),
        migrations.RenameField("payrollrun", "approved_at", "finalized_at"),
        migrations.RenameField("payrollrun", "approved_by", "finalized_by"),
        migrations.RunPython(forward, backward),
        migrations.AlterField(
            model_name="payrollrun", name="status",
            field=models.CharField(
                choices=[("draft", "Draft (disiapkan HRD)"), ("diserahkan", "Diserahkan HRD ke Payroll"), ("dihitung", "Sudah dihitung"), ("ditetapkan", "Ditetapkan Payroll"),
                         ("dibayar", "Sudah dibayar"), ("dibatalkan", "Dibatalkan")], db_index=True, default="draft", max_length=10),
        ),
    ]
