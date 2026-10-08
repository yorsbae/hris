from pathlib import Path
import environ
BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("DJANGO_SECRET_KEY")
COMPANY_NAME = env("COMPANY_NAME", default="PT X")  # sementara; kelak dari konfigurasi sistem (VISION → Konfigurasi sistem)
# Kop & penanda tangan surat cetak (putaran 20). Nama di formulir contoh hanya TEMPLATE: isi lewat .env sesuai pejabat yang bertugas.
COMPANY_ADDRESS = env("COMPANY_ADDRESS", default="")
POLI_NAME = env("POLI_NAME", default="")  # kosong → "POLIKLINIK <COMPANY_NAME>"
POLI_DOCTOR_NAME = env("POLI_DOCTOR_NAME", default="")  # dokter perusahaan; kosong → nama pengguna Poli yang membuat surat
HRD_SIGNER_NAME = env("HRD_SIGNER_NAME", default="")  # penanda tangan HRD (mis. Manager HRD / Kabag Personalia)
HRD_SIGNER_TITLE = env("HRD_SIGNER_TITLE", default="Manager HRD")
PERSONALIA_SIGNER_NAME = env("PERSONALIA_SIGNER_NAME", default="")  # persetujuan cuti hamil
PERSONALIA_SIGNER_TITLE = env("PERSONALIA_SIGNER_TITLE", default="Kabag Personalia")
APP_VERSION = "1.1.0"
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")  # enkripsi kolom sensitif; lihat apps/core/crypto.py
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
INSTALLED_APPS = [
    "apps.core.admin_config.HrisAdminConfig", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.core", "apps.hr.apps.HrConfig", "apps.poli.apps.PoliConfig", "apps.hrd",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sajikan /static/ (CSS/JS Django admin) walau DEBUG=False; harus tepat setelah SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.IdleTimeoutMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
    "apps.core.middleware.RateLimitLoginMiddleware",
    "apps.core.middleware.ThrottleMiddleware",
    "apps.core.middleware.RejectNulMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,  # DIRS dulu: templates/admin/* menimpa tema bawaan Django admin
  "OPTIONS": {"context_processors": ["django.template.context_processors.request",
  "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "apps.core.context.unread"]}}]
DATABASES = {"default": env.db("DATABASE_URL")}  # ORM = proteksi SQL injection
DATABASES["default"]["CONN_MAX_AGE"] = 60
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
  {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
  {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
  {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
  {"NAME": "apps.core.validators.LetterAndDigitValidator"}]
AUTHENTICATION_BACKENDS = ["apps.core.auth_backend.LockoutBackend"]  # ModelBackend + kunci akun per username
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
# --- Keamanan berlapis (putaran 21, P1). Semua angka = USULAN awal (A44), disetel setelah uji.
LOGIN_LOCK_THRESHOLD = env.int("LOGIN_LOCK_THRESHOLD", 8)          # salah sandi berturut-turut sebelum akun dikunci sementara
LOGIN_LOCK_STEPS_MIN = (1, 5, 15, 60, 240)                          # menit kunci per jenjang (jenjang terakhir berulang)
LOGIN_LOCK_RESET_HOURS = 24                                         # hitungan gagal kembali nol bila kegagalan terakhir lebih lama dari ini
SESSION_IDLE_TIMEOUT = env.int("SESSION_IDLE_MINUTES", 30) * 60     # 0 = nonaktif
THROTTLE_ZONES = {"api": (240, 60), "export": (20, 60), "upload": (30, 60)}  # {zona: (jumlah, detik)} per user+IP
import sys
if "test" in sys.argv: THROTTLE_ZONES = {}  # cache LocMem dipakai bersama antartes (IP & pk user sama) → tes zona memakai override_settings
CSP_POLICY = env("CSP_POLICY", default="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                 "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SILENCED_SYSTEM_CHECKS = ["security.W008"]  # pengalihan HTTP→HTTPS dilakukan Nginx, bukan Django
if HTTPS:
    SECURE_HSTS_SECONDS = env.int("HSTS_SECONDS", 0)  # naikkan bertahap (mis. 300 → 86400 → 31536000) setelah HTTPS terbukti stabil; jangan langsung setahun
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("HSTS_INCLUDE_SUBDOMAINS", False)
# Cache bersama untuk rate limit/throttle: Redis bila REDIS_URL diisi (WAJIB bila gunicorn >1 worker), selain itu LocMem (per proses)
REDIS_URL = env("REDIS_URL", default="")
CACHES = {"default": ({"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL, "KEY_PREFIX": "hris"} if REDIS_URL
                      else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"})}
LOGGING = {"version": 1, "disable_existing_loggers": False,
  "formatters": {"std": {"format": "%(asctime)s %(name)s %(levelname)s %(message)s"}},
  "handlers": {"file": {"class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR / "app.log", "maxBytes": 10_000_000, "backupCount": 10, "formatter": "std"}},
  "root": {"handlers": ["file"], "level": "INFO"}}
LOGIN_URL = "/login/"; LOGIN_REDIRECT_URL = "/"; LOGOUT_REDIRECT_URL = "/login/"

# Kebijakan cuti/jadwal (asumsi bawaan; sesuaikan dengan peraturan perusahaan, lihat docs/PROGRESS.md §4)
ANNUAL_LEAVE_DAYS = 12            # jatah cuti tahunan (hari kerja)
ANNUAL_LEAVE_MIN_MONTHS = 12      # masa kerja minimum sebelum berhak jatah
REGULAR_OFF_WEEKDAYS = (6,)       # hari libur reguler (0=Senin … 6=Minggu); tidak dihitung sebagai hari cuti
