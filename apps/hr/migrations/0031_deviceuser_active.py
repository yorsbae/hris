from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hr", "0030_device_protocol_fk")]
    operations = [
        migrations.AddField(model_name="deviceuser", name="active", field=models.BooleanField(db_index=True, default=True, verbose_name="Aktif")),
        migrations.AddField(model_name="deviceuser", name="deactivated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="deviceuser", name="deactivated_reason", field=models.CharField(blank=True, max_length=200)),
    ]
