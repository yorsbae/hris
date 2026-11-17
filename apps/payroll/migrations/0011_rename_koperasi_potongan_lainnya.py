from django.db import migrations


def rename(apps, schema_editor):
    C = apps.get_model("payroll", "PayComponent")
    if C.objects.filter(label__iexact="Potongan Lainnya").exists(): return
    C.objects.filter(code__startswith="pot_m_", label__iexact="Iuran Koperasi").update(label="Potongan Lainnya")   # kode tetap: riwayat aturan & slip lama tidak terputus


class Migration(migrations.Migration):
    dependencies = [("payroll", "0010_tax_masters_deduction_rules")]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
