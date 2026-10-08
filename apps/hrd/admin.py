from django.contrib import admin
from . import models

for m in (models.Aid, models.MaternityLeave, models.Project, models.ProjectWork, models.CateringOrder, models.WarningLetter, models.BpjsMembership):
    admin.site.register(m)


@admin.register(models.BpjsStatusLog)
class BpjsStatusLogAdmin(admin.ModelAdmin):
    """Histori BPJS: hanya baca (append-only)."""
    list_display = ("employee", "scheme", "old_status", "new_status", "effective_date", "changed_by")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Seragam (putaran 23): hanya baca di /admin/ — tarif append-only, pembelian dibatalkan lewat halaman HRD (beralasan), jenis/ukuran dikelola di Master Seragam."""
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(models.UniformPurchase)
class UniformPurchaseAdmin(_ReadOnlyAdmin):
    list_display = ("employee", "purchase_date", "utype", "size", "quantity", "deduction_amount", "deduction_status", "voided_at")


@admin.register(models.UniformRate)
class UniformRateAdmin(_ReadOnlyAdmin):
    list_display = ("gender", "amount", "effective_from")


admin.site.register(models.UniformType, _ReadOnlyAdmin)
admin.site.register(models.UniformSize, _ReadOnlyAdmin)
