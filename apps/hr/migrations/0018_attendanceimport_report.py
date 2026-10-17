from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hr", "0017_profile_fields_gs_groups")]
    operations = [migrations.AddField(model_name="attendanceimport", name="report", field=models.JSONField(blank=True, default=dict))]
