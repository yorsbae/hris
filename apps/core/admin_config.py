from django.contrib.admin.apps import AdminConfig
class HrisAdminConfig(AdminConfig):
    """Mengganti situs admin bawaan dengan HrisAdminSite (tema selaras aplikasi; lihat apps/core/admin_site.py)."""
    default_site = "apps.core.admin_site.HrisAdminSite"

