from django.contrib import admin
from . import models


@admin.register(models.Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category"); search_fields = ("code", "name")


class _ReadOnly(admin.ModelAdmin):
    """Kartu stok append-only (VISION): hanya lihat. Perubahan stok lewat /poli/medicines/ (tercatat & atomik)."""
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(models.Medicine)
class MedicineAdmin(_ReadOnly):
    list_display = ("code", "name", "unit", "stock", "min_stock"); search_fields = ("code", "name")


@admin.register(models.StockMovement)
class StockMovementAdmin(_ReadOnly):
    list_display = ("created_at", "medicine", "qty", "balance_after", "reason", "created_by"); list_filter = ("reason",)
# Data medis pasien (MedicalRecord, Prescription, Referral, surat) SENGAJA tidak didaftarkan: hanya Poli lewat /poli/ (VISION).
