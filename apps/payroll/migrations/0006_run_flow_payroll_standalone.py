"""Putaran 59 — Payroll berdiri sendiri: tahap 'Diserahkan HRD' dihapus dari alur run. Serah-terima data HRD → Payroll kini lewat Tutup Periode (berkas XLSX).
Run berstatus 'diserahkan' dikembalikan ke 'draft' (Payroll tinggal menghitung); jejak (events) lama tetap utuh. Kolom handed_* dipertahankan (riwayat), tidak dipakai lagi."""
from django.db import migrations, models


def forward(apps, schema_editor):
    apps.get_model("payroll", "PayrollRun").objects.filter(status="diserahkan").update(status="draft")


class Migration(migrations.Migration):
    dependencies = [("payroll", "0005_standby_education_masters")]

    operations = [
        migrations.AlterField(
            model_name="payrollrun", name="status",
            field=models.CharField(choices=[("draft", "Draft (belum dihitung)"), ("dihitung", "Sudah dihitung"), ("ditetapkan", "Ditetapkan Payroll"), ("dibayar", "Sudah dibayar"),
                                            ("dibatalkan", "Dibatalkan")], db_index=True, default="draft", max_length=10),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
