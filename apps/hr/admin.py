from django.contrib import admin
from . import models
for m in (models.Department, models.Position, models.Shift, models.Contract, models.ChangeRequest, models.Announcement):
    admin.site.register(m)


@admin.register(models.Employee)
class EmployeeAdmin(admin.ModelAdmin):
    """Soft delete: tampilkan semua (termasuk terhapus), hapus fisik dimatikan, tersedia aksi Pulihkan. Kolom terenkripsi disembunyikan dari daftar."""
    list_display = ("nik", "name", "department", "status", "deleted_at")
    search_fields = ("nik", "name"); list_filter = ("status", "department")
    actions = ["restore_selected"]
    def get_queryset(self, request): return models.Employee.all_objects.select_related("department")
    def has_delete_permission(self, request, obj=None): return False
    @admin.action(description="Pulihkan karyawan terpilih (batalkan soft delete)")
    def restore_selected(self, request, qs):
        n = 0
        for e in qs.filter(deleted_at__isnull=False): e.restore(); n += 1
        self.message_user(request, f"{n} karyawan dipulihkan.")
