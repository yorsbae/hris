"""Putaran 61: master tarif Pasal 17 (TaxBracket, disemai), master potongan tambahan (DeductionRule); kunci parameter baru (PTKP, biaya jabatan)."""
import datetime
from decimal import Decimal as D

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.payroll import params as P, tax


def seed(apps, schema_editor):
    B = apps.get_model("payroll", "TaxBracket")
    if not B.objects.exists():
        B.objects.bulk_create([B(upper=u, percent=r * 100, effective_from=datetime.date(2022, 1, 1)) for u, r in tax.PASAL_17])


class Migration(migrations.Migration):
    dependencies = [("payroll", "0009_param_choices_pph21"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.AlterField(model_name="payparam", name="key", field=models.CharField(choices=P.CHOICES, max_length=30)),
        migrations.CreateModel(name="TaxBracket", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("voided_at", models.DateTimeField(blank=True, null=True)), ("void_reason", models.CharField(blank=True, max_length=300)),
            ("upper", models.DecimalField(blank=True, decimal_places=0, max_digits=16, null=True, verbose_name="PKP sampai (Rp)")), ("percent", models.DecimalField(decimal_places=2, max_digits=5, verbose_name="Tarif (%)")),
            ("effective_from", models.DateField()),
            ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["effective_from", "upper"]}),
        migrations.AddIndex(model_name="taxbracket", index=models.Index(fields=["effective_from"], name="payroll_tax_effecti_a5253c_idx")),
        migrations.CreateModel(name="DeductionRule", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("voided_at", models.DateTimeField(blank=True, null=True)), ("void_reason", models.CharField(blank=True, max_length=300)),
            ("method", models.CharField(choices=[("persen_pokok", "% dari gaji pokok"), ("persen_bruto", "% dari total pendapatan"), ("nominal_bulan", "Nominal tetap per bulan"), ("nominal_hari", "Nominal per hari dibayar")], max_length=14, verbose_name="Cara hitung")),
            ("value", models.DecimalField(decimal_places=4, max_digits=14, verbose_name="Nilai")),
            ("rounding", models.DecimalField(decimal_places=0, default=1, max_digits=8, verbose_name="Pembulatan ke bawah (Rp)")),
            ("pay_type", models.CharField(blank=True, choices=[("", "Semua"), ("harian", "Karyawan harian"), ("bulanan", "Karyawan bulanan")], default="", max_length=8, verbose_name="Berlaku untuk")),
            ("effective_from", models.DateField()), ("note", models.CharField(blank=True, max_length=300, verbose_name="Dasar aturan")),
            ("component", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="rules", to="payroll.paycomponent")),
            ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ("voided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["component_id", "-effective_from", "-id"]}),
        migrations.AddConstraint(model_name="deductionrule", constraint=models.CheckConstraint(condition=models.Q(("value__gte", 0)), name="dedrule_nonneg")),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
