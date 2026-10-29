"""Settings SITUS PUBLIK (internet): proses, DB, .env, dan host TERPISAH dari HRIS (LAN). Satu repo, dua deployable.
Hanya memuat model yang dibutuhkan (core/hr = cangkang kosong karena FK company → User/Department); TANPA admin, login, API internal, middleware sesi internal."""
import os
os.environ.setdefault("SITE_MODE", "public")
from .settings import *  # noqa: F401,F403  (nilai bersama: keamanan, CSP, logging)
SITE_MODE = "public"
ROOT_URLCONF = "config.urls_public"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.staticfiles", "apps.core", "apps.hr.apps.HrConfig", "apps.company"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "apps.core.middleware.SecurityHeadersMiddleware",
              "whitenoise.middleware.WhiteNoiseMiddleware", "django.middleware.common.CommonMiddleware",
              "apps.core.middleware.RejectNulMiddleware"]
AUTHENTICATION_BACKENDS = []
# Tanpa context processor auth/messages/unread: situs publik tidak punya sesi, user, atau notifikasi.
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request"]}}]
