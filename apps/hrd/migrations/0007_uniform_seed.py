"""Data awal seragam (putaran 23, P5): ukuran S–3XL, jenis 'Seragam Kerja', tarif potongan L Rp 19.000 / P Rp 17.000.
Tarif berlaku sejak 2000-01-01 agar pembelian bertanggal lampau tidak ditolak; HRD menambah tarif baru (tanggal berlaku) di /hrd/uniforms/master/ — tarif lama tidak diubah."""
from datetime import date
from decimal import Decimal
from django.db import migrations


def seed(apps, schema_editor):
    Size, Type, Rate = apps.get_model("hrd", "UniformSize"), apps.get_model("hrd", "UniformType"), apps.get_model("hrd", "UniformRate")
    for i, code in enumerate(["S", "M", "L", "XL", "XXL", "3XL"], start=1): Size.objects.get_or_create(code=code, defaults={"sort": i * 10})
    Type.objects.get_or_create(name="Seragam Kerja")
    for gender, amount in (("L", Decimal("19000")), ("P", Decimal("17000"))):
        Rate.objects.get_or_create(gender=gender, effective_from=date(2000, 1, 1), defaults={"amount": amount})


class Migration(migrations.Migration):
    dependencies = [("hrd", "0006_uniform_purchase")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
