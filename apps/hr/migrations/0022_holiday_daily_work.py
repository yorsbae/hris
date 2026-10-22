from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hr", "0021_holiday_duty")]
    operations = [migrations.AddField(model_name="holiday", name="daily_work", field=models.BooleanField(default=False, help_text="Karyawan berstatus Harian selalu masuk pada hari ini, apa pun pilihan untuk karyawan bulanan.", verbose_name="Karyawan harian wajib masuk"))]
