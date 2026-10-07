from django.contrib import admin
from . import models

for m in (models.Aid, models.MaternityLeave, models.Project, models.ProjectDailyLog, models.CateringOrder, models.BpjsMembership):
    admin.site.register(m)


@admin.register(models.BpjsStatusLog)
class BpjsStatusLogAdmin(admin.ModelAdmin):
    """Histori BPJS: hanya baca (append-only)."""
    list_display = ("employee", "scheme", "old_status", "new_status", "effective_date", "changed_by")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
