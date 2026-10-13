from django.db import migrations, models


class Migration(migrations.Migration):
    """Putaran 57: role baru 'payroll' (hanya pilihan kolom; tanpa perubahan data)."""

    dependencies = [
        ('core', '0004_user_login_lockout'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(choices=[('superadmin', 'Superadmin'), ('hrd', 'Hrd'), ('dept_admin', 'Dept Admin'), ('poli', 'Poli'), ('payroll', 'Payroll')], db_index=True, max_length=20),
        ),
    ]
