from django.contrib import admin
from .models import PayComponent


@admin.register(PayComponent)
class PayComponentAdmin(admin.ModelAdmin):
    """Komponen slip tambahan (Superadmin). Tidak bisa dihapus: nonaktifkan."""
    list_display = ("label", "code", "section", "kind", "order", "signed", "active")
    list_filter = ("section", "kind", "active")
    def has_delete_permission(self, request, obj=None): return False
