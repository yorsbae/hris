"""Tutup periode berbasis rentang tanggal bebas + riwayat + snapshot XLSX (putaran 59).
Baris lama (satu per bulan, `period` unik) diubah: start_date/end_date = awal & akhir bulan itu, jadi riwayat tetap utuh; tanpa berkas snapshot (dibuat saat penutupan baru).
Urutan operasi: semua perubahan SKEMA lebih dulu, UPDATE data terakhir (aman di PostgreSQL: tidak ada ALTER setelah UPDATE dalam satu transaksi)."""
import calendar
import datetime

import apps.hr.models
from django.db import migrations, models


def backfill(apps_, schema_editor):
    PC = apps_.get_model("hr", "PeriodClose")
    for r in PC.objects.all():
        try: y, m = int(r.period[:4]), int(r.period[5:7])
        except (ValueError, IndexError): continue
        PC.objects.filter(pk=r.pk).update(start_date=datetime.date(y, m, 1), end_date=datetime.date(y, m, calendar.monthrange(y, m)[1]))


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0023_medical_checkup"),
    ]

    operations = [
        migrations.AddField(model_name="periodclose", name="start_date", field=models.DateField(default=datetime.date(2000, 1, 1), verbose_name="Dari tanggal"), preserve_default=False),
        migrations.AddField(model_name="periodclose", name="end_date", field=models.DateField(default=datetime.date(2000, 1, 1), verbose_name="Sampai tanggal"), preserve_default=False),
        migrations.AddField(model_name="periodclose", name="export_file", field=models.FileField(blank=True, upload_to=apps.hr.models.period_export_path)),
        migrations.AddField(model_name="periodclose", name="export_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="periodclose", name="export_counts", field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name="periodclose", name="period", field=models.CharField(db_index=True, max_length=7, verbose_name="Untuk gaji bulan (YYYY-MM)")),
        migrations.AlterModelOptions(name="periodclose", options={"ordering": ["-end_date", "-id"]}),
        migrations.AddConstraint(model_name="periodclose", constraint=models.UniqueConstraint(condition=models.Q(("closed", True)), fields=("period",), name="uniq_periodclose_period_closed")),
        migrations.AddConstraint(model_name="periodclose", constraint=models.CheckConstraint(condition=models.Q(("end_date__gte", models.F("start_date"))), name="periodclose_range_valid")),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
