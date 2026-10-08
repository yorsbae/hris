import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hrd', '0003_convert_recap_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='projectdailylog',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='projectdailylog',
            name='project',
        ),
        migrations.RemoveField(
            model_name='projectdailylog',
            name='workers',
        ),
        migrations.RemoveIndex(
            model_name='cateringorder',
            name='hrd_caterin_date_24bfe5_idx',
        ),
        migrations.RemoveField(
            model_name='aid',
            name='decided_at',
        ),
        migrations.RemoveField(
            model_name='aid',
            name='decided_by',
        ),
        migrations.RemoveField(
            model_name='aid',
            name='decision_note',
        ),
        migrations.RemoveField(
            model_name='aid',
            name='paid_at',
        ),
        migrations.RemoveField(
            model_name='aid',
            name='status',
        ),
        migrations.RemoveField(
            model_name='cateringorder',
            name='department',
        ),
        migrations.RemoveField(
            model_name='cateringorder',
            name='price_large',
        ),
        migrations.RemoveField(
            model_name='cateringorder',
            name='price_small',
        ),
        migrations.RemoveField(
            model_name='cateringorder',
            name='status',
        ),
        migrations.RemoveField(
            model_name='cateringorder',
            name='vendor',
        ),
        migrations.AlterField(
            model_name='cateringorder',
            name='meal',
            field=models.CharField(choices=[('0900', '09:00'), ('1200', '12:00'), ('1800', '18:00'), ('0200', '02:00')], max_length=4),
        ),
        migrations.AlterField(
            model_name='cateringorder',
            name='received_large',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Tepak besar (diterima)'),
        ),
        migrations.AlterField(
            model_name='cateringorder',
            name='received_small',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Tepak kecil (diterima)'),
        ),
        migrations.AddConstraint(
            model_name='cateringorder',
            constraint=models.UniqueConstraint(fields=('date', 'meal'), name='uniq_catering_date_meal'),
        ),
        migrations.DeleteModel(
            name='ProjectDailyLog',
        ),
    ]
