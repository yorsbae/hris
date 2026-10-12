"""URLconf situs publik (SITE_MODE=public): HANYA profil perusahaan + karir. Tidak ada /admin/, /login/, /employees/, /poli/, /hrd/ atau API internal.
Jalankan sebagai proses/host terpisah dari aplikasi internal (lihat docs/DEPLOY-PUBLIK.md)."""
from django.urls import include, path
from apps.company import public_views

urlpatterns = [path("", include("apps.company.public_urls"))]
handler404 = "apps.company.public_views.notfound"
