"""Lookbook dihapus; halaman Tepak Sarung ditambahkan; katalog menjadi sarung saja.

Data: kelompok "Bawahan" diganti nama menjadi "Sarung"; kelompok/subkategori non-sarung (Atasan, Aksesoris, Celana Sarung) dinonaktifkan dan produk contoh non-sarung
dijadikan draf (TIDAK dihapus, bisa dipulihkan HRD lewat menu Produk/Kategori)."""
import apps.company.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

NON_SARUNG_SLUGS = ["celana-sarung-wadimor", "baju-koko-putih-bordir", "baju-koko-songket-hijau", "kemeja-muslim-garis", "peci-hitam-beludru", "peci-rajut-putih", "surban-putih-polos"]
NON_SARUNG_CATS = ["Atasan", "Aksesoris", "Baju Koko", "Kemeja Muslim", "Peci", "Surban", "Celana Sarung"]


def sarung_only(apps, schema_editor):
    Cat, Prod = apps.get_model("company", "ProductCategory"), apps.get_model("company", "Product")
    for g in Cat.objects.filter(name_id="Bawahan", parent__isnull=True):
        g.name_id = "Sarung"
        g.name_en = "Sarong" if g.name_en in ("", "Bottoms") else g.name_en
        if g.hint_id == "Sarung, celana sarung, dan bawahan lainnya.": g.hint_id, g.hint_en = "Sarung tenun, printing, ibadah, dan anak.", "Woven, printed, worship and kids' sarongs."
        g.save(update_fields=["name_id", "name_en", "hint_id", "hint_en"])
    Cat.objects.filter(name_id__in=NON_SARUNG_CATS).update(active=False)
    Prod.objects.filter(slug__in=NON_SARUNG_SLUGS, status="published").update(status="draft", featured=False)


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0003_motif_warna_produk"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="Look"),
        migrations.CreateModel(
            name="Tepak",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("slug", models.SlugField(blank=True, max_length=80, unique=True)),
                ("name", models.CharField(max_length=120, verbose_name="Nama tepak")),
                ("variant", models.CharField(blank=True, help_text="Label kecil di atas nama, mis. Citra, Penang, Horizon", max_length=60, verbose_name="Varian / seri")),
                ("summary_id", models.CharField(blank=True, max_length=200, verbose_name="Ringkasan (ID)")),
                ("summary_en", models.CharField(blank=True, max_length=200, verbose_name="Ringkasan (EN)")),
                ("spec", models.CharField(blank=True, help_text="Mis. Kotak keras · isi 1 sarung", max_length=80, verbose_name="Keterangan singkat")),
                ("image", models.FileField(blank=True, upload_to=apps.company.models.upload_tepak, verbose_name="Foto tepak")),
                ("status", models.CharField(choices=[("draft", "Draf"), ("published", "Terbit")], default="draft", max_length=10)),
                ("order", models.PositiveIntegerField(default=100, verbose_name="Urutan")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "tepak", "ordering": ["order", "id"], "indexes": [models.Index(fields=["status", "order"], name="company_tep_status_cf4a29_idx")]},
        ),
        migrations.RunPython(sarung_only, migrations.RunPython.noop),
    ]
