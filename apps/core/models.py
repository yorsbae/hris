from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

class Role(models.TextChoices):
    SUPERADMIN = "superadmin"; HRD = "hrd"; DEPT_ADMIN = "dept_admin"; POLI = "poli"

class AppUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra):
        extra.setdefault("role", Role.SUPERADMIN)  # `createsuperuser` sebelumnya menghasilkan role kosong → akun tak bisa memakai aplikasi
        return super().create_superuser(username, email, password, **extra)

class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.PROTECT)
    must_change_password = models.BooleanField(default=False)  # sandi dibuat/direset Superadmin → wajib diganti saat login berikutnya
    # Penguncian akun per username (putaran 21, P1): salah sandi berulang → kunci sementara dengan jeda progresif; dibuka otomatis atau oleh Superadmin
    failed_logins = models.PositiveSmallIntegerField(default=0)
    last_failed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    objects = AppUserManager()
    def save(self, *a, **k):
        if self.role == Role.DEPT_ADMIN and not self.department_id:
            raise ValueError("Admin Departemen wajib punya departemen")
        super().save(*a, **k)

class AuditLog(models.Model):  # append-only
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True)
    module = models.CharField(max_length=40, db_index=True)
    action = models.CharField(max_length=40, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    before = models.JSONField(null=True); after = models.JSONField(null=True)
    class Meta: indexes = [models.Index(fields=["object_type", "object_id"])]
    def save(self, *a, **k):
        if self.pk: raise PermissionError("Audit log tidak boleh diubah")
        super().save(*a, **k)
    def delete(self, *a, **k): raise PermissionError("Audit log tidak boleh dihapus")

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30)  # request, approval, contract, absensi, rule, stock
    title = models.CharField(max_length=200); link = models.CharField(max_length=200, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes = [models.Index(fields=["user", "is_read", "-created_at"])]
