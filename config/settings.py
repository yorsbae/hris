from pathlib import Path
import environ
BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("DJANGO_SECRET_KEY")
COMPANY_NAME = env("COMPANY_NAME", default="PT X")  # sementara; kelak dari konfigurasi sistem (VISION → Konfigurasi sistem)
APP_VERSION = "1.1.0"
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")  # enkripsi kolom sensitif; lihat apps/core/crypto.py
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.core", "apps.hr.apps.HrConfig", "apps.poli.apps.PoliConfig", "apps.hrd",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sajikan /static/ (CSS/JS Django admin) walau DEBUG=False; harus tepat setelah SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
    "apps.core.middleware.RateLimitLoginMiddleware",
    "apps.core.middleware.RejectNulMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
  "OPTIONS": {"context_processors": ["django.template.context_processors.request",
  "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "apps.core.context.unread"]}}]
DATABASES = {"default": env.db("DATABASE_URL")}  # ORM = proteksi SQL injection
DATABASES["default"]["CONN_MAX_AGE"] = 60
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
  {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
# Di belakang Nginx: daftarkan IP/CIDR proxy agar X-Forwarded-For dipercaya (audit & rate limit login memakai IP klien asli; lihat apps/core/net.py).
# Kosong = header diabaikan (aman untuk akses langsung tanpa proxy).
TRUSTED_PROXY_IPS = env.list("TRUSTED_PROXY_IPS", default=[])
# HTTPS=True bila situs dilayani lewat HTTPS (Nginx yang menerminasi TLS). Cookie hanya dikirim lewat HTTPS; Nginx WAJIB menimpa X-Forwarded-Proto.
HTTPS = env.bool("HTTPS", False)
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = HTTPS
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])  # mis. https://hris.lan
if HTTPS: SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
LANGUAGE_CODE = "id"; TIME_ZONE = "Asia/Jakarta"; USE_TZ = True
STATIC_URL = "static/"; STATIC_ROOT = BASE_DIR / "static_root"
# Berkas statis (hanya milik Django admin; halaman aplikasi memakai gaya inline). WhiteNoise membaca langsung dari app (finders),
# jadi `collectstatic` TIDAK wajib; bila dijalankan, berkas dipadatkan (gzip/brotli). Tanpa manifest agar tes/deploy tidak gagal.
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
try: STATIC_ROOT.mkdir(exist_ok=True)  # cegah peringatan WhiteNoise "No directory" sebelum collectstatic pernah dijalankan
except OSError: pass
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
MEDIA_ROOT = BASE_DIR / "media"  # dokumen karyawan: sajikan lewat view ber-permission, bukan langsung
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_RATE_LIMIT = (5, 300)  # 5 percobaan / 5 menit per IP
LOGGING = {"version": 1, "disable_existing_loggers": False,
  "handlers": {"file": {"class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR / "app.log", "maxBytes": 10_000_000, "backupCount": 10}},
  "root": {"handlers": ["file"], "level": "INFO"}}
LOGIN_URL = "/login/"; LOGIN_REDIRECT_URL = "/"; LOGOUT_REDIRECT_URL = "/login/"

# Kebijakan cuti/jadwal (asumsi bawaan; sesuaikan dengan peraturan perusahaan, lihat docs/PROGRESS.md §4)
ANNUAL_LEAVE_DAYS = 12            # jatah cuti tahunan (hari kerja)
ANNUAL_LEAVE_MIN_MONTHS = 12      # masa kerja minimum sebelum berhak jatah
REGULAR_OFF_WEEKDAYS = (6,)       # hari libur reguler (0=Senin … 6=Minggu); tidak dihitung sebagai hari cuti
