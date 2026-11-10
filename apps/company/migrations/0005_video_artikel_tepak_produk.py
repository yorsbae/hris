"""Sub menu Video di halaman Artikel (jenis + tautan), tepak menautkan ke produk, dan pembersihan teks contoh lama.

Data: pita "Website Dalam Pengembangan" dan catatan kaki "Data contoh untuk tahap pengembangan." HANYA dihapus bila teksnya masih persis bawaan seed lama (suntingan HRD tidak disentuh)."""
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

OLD_BANNER_ID = "Website Dalam Pengembangan, beberapa fitur mungkin belum tersedia atau berubah sewaktu-waktu."
OLD_FOOTER_ID = "Data contoh untuk tahap pengembangan."


def clean_defaults(apps, schema_editor):
    S = apps.get_model("company", "SiteSetting")
    for s in S.objects.all():
        if s.banner_id == OLD_BANNER_ID: s.banner_active, s.banner_id, s.banner_en = False, "", ""
        if s.footer_note_id == OLD_FOOTER_ID: s.footer_note_id, s.footer_note_en = "", ""
        s.save(update_fields=["banner_active", "banner_id", "banner_en", "footer_note_id", "footer_note_en"])


class Migration(migrations.Migration):

    dependencies = [("company", "0004_tepak_hapus_lookbook")]

    operations = [
        migrations.AddField(model_name="article", name="kind", field=models.CharField(choices=[("artikel", "Artikel"), ("video", "Video (tautan media sosial)")], default="artikel", max_length=10, verbose_name="Jenis")),
        migrations.AddField(model_name="article", name="video_url", field=models.URLField(blank=True, help_text="Hanya untuk jenis Video (Facebook/Instagram/TikTok/YouTube). Kosong = memakai tautan Facebook di Pengaturan situs.", max_length=300, validators=[django.core.validators.URLValidator(schemes=["http", "https"])], verbose_name="Tautan video / pos media sosial")),
        migrations.AddField(model_name="tepak", name="product", field=models.ForeignKey(blank=True, help_text="Kartu tepak di halaman Tepak Sarung menautkan ke halaman produk ini.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tepak", to="company.product", verbose_name="Produk sarung di dalamnya")),
        migrations.RunPython(clean_defaults, migrations.RunPython.noop),
    ]
