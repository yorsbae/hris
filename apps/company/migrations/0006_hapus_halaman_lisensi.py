"""Halaman publik "Lisensi & Kredit" dihapus (lisensi kini berupa berkas LICENSE.md di akar repositori). Menghapus halaman bawaan seed bila masih ada."""
from django.db import migrations


def drop(apps, schema_editor):
    apps.get_model("company", "Page").objects.filter(slug="lisensi").delete()


class Migration(migrations.Migration):
    dependencies = [("company", "0005_video_artikel_tepak_produk")]
    operations = [migrations.RunPython(drop, migrations.RunPython.noop)]
