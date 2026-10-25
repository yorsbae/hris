"""Putaran 60: PPh 21 (TER) + THR. Skema dulu, lalu isi data (aman di PostgreSQL): status PTKP di profil, master TerRate (+ data resmi PP 58/2023), komponen slip THR & PPh 21."""
import datetime

from django.conf import settings
from django.db import migrations, models

from apps.payroll import defaults, ter_data


def seed(apps, schema_editor):
    C = apps.get_model("payroll", "PayComponent"); T = apps.get_model("payroll", "TerRate")
    for code, label, section, kind, order, signed in defaults.EXTRA:
        C.objects.get_or_create(code=code, defaults={"label": label, "section": section, "kind": kind, "order": order, "signed": signed})
    eff = datetime.date(*ter_data.EFFECTIVE)
    if not T.objects.exists():
        T.objects.bulk_create([T(category=cat, upper=up, percent=pct, effective_from=eff) for cat, rows in ter_data.TABLES.items() for up, pct in rows])


class Migration(migrations.Migration):
    dependencies = [("payroll", "0007_run_report_archive"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.AddField(model_name="payprofile", name="ptkp_status", field=models.CharField(choices=[(k, v) for k, v in ter_data.STATUS_LABELS.items()], default="TK0", max_length=3, verbose_name="Status PTKP (pajak)")),
        migrations.CreateModel(
            name="TerRate",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                    ("created_at", models.DateTimeField(auto_now_add=True)), ("voided_at", models.DateTimeField(blank=True, null=True)), ("void_reason", models.CharField(blank=True, max_length=300)),
                    ("category", models.CharField(choices=[("A", "Kategori A (TK/0, TK/1, K/0)"), ("B", "Kategori B (TK/2, TK/3, K/1, K/2)"), ("C", "Kategori C (K/3)")], max_length=1)),
                    ("upper", models.DecimalField(blank=True, decimal_places=0, max_digits=16, null=True, verbose_name="Bruto sebulan sampai (Rp)")),
                    ("percent", models.DecimalField(decimal_places=2, max_digits=5, verbose_name="Tarif (%)")), ("effective_from", models.DateField()),
                    ("created_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                    ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL))],
            options={"ordering": ["category", "effective_from", "upper"], "indexes": [models.Index(fields=["category", "effective_from"], name="payroll_ter_categor_84a8cf_idx")]},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
