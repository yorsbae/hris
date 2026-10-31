from pathlib import Path
import environ
BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("DJANGO_SECRET_KEY")
COMPANY_NAME = env("COMPANY_NAME", default="PT X")  # sementara; kelak dari konfigurasi sistem (VISION → Konfigurasi sistem)
# Kop & penanda tangan surat cetak (putaran 20). Nama di formulir contoh hanya TEMPLATE: isi lewat .env sesuai pejabat yang bertugas.
COMPANY_ADDRESS = env("COMPANY_ADDRESS", default="Jl. Raya Kandeman Km. 4,5 Kandeman - Batang 51261, Jawa Tengah, Telp. (0285) 392884 / 4493084 - Fax. (0285) 392583")
COMPANY_TAGLINE = env("COMPANY_TAGLINE", default="EXPORT - IMPORT - MANUFACTURE - GENERAL TRADING")  # baris usaha di bawah nama pada kop
HRD_KOP_UNIT = env("HRD_KOP_UNIT", default="DEPARTEMEN HRD")  # baris di bawah nama perusahaan pada cetakan internal HRD (daftar karyawan dsb.) — tanpa baris usaha ekspor-impor
POLI_TAGLINE = env("POLI_TAGLINE", default="PELAYANAN KESEHATAN KARYAWAN")  # baris di bawah nama poli pada kop poli
COMPANY_CITY = env("COMPANY_CITY", default="")  # kota untuk baris "Batang, 11 Oktober 2026" di surat Poli; kosong → hanya tanggal
POLI_NAME = env("POLI_NAME", default="")  # kosong → "POLIKLINIK <COMPANY_NAME>"
POLI_DOCTOR_NAME = env("POLI_DOCTOR_NAME", default="")  # dokter perusahaan; kosong → nama pengguna Poli yang membuat surat
POLI_DEPARTMENT_CODE = env("POLI_DEPARTMENT_CODE", default="POL")  # kode departemen Poli: karyawan di sini yang ditawarkan sebagai "Pemeriksa" di rekam medis
HRD_SIGNER_NAME = env("HRD_SIGNER_NAME", default="")  # penanda tangan HRD (mis. Manager HRD / Kabag Personalia)
HRD_SIGNER_TITLE = env("HRD_SIGNER_TITLE", default="Manager HRD")
PERSONALIA_SIGNER_NAME = env("PERSONALIA_SIGNER_NAME", default="")  # persetujuan cuti hamil
PERSONALIA_SIGNER_TITLE = env("PERSONALIA_SIGNER_TITLE", default="Kabag Personalia")
# Pembagian beban tagihan berobat (Poli → Tagihan Mitra) menurut PENANGGUNG: BPJS → perusahaan menanggung PCT% dan karyawan sisanya; Non BPJS → 100% perusahaan.
# Ini hanya persen BAWAAN (dipakai bila belum ada aturan). Persen per tanggal berlaku: Poli → Master Tanggungan (tagihan yang sudah dicatat tidak bergeser).
POLI_BPJS_COMPANY_PCT = env.int("POLI_BPJS_COMPANY_PCT", default=75)
APP_VERSION = "1.1.0"
ATTENDANCE_FK_DLL = env("ATTENDANCE_FK_DLL", default="")  # jalur FKAttend.dll (bawaan: C:\\Program Files (x86)\\Fingerspot Personnel\\FKAttend.dll)
ATTENDANCE_FK_TIMEOUT = env.int("ATTENDANCE_FK_TIMEOUT", default=900)  # detik maksimal membaca satu mesin FK (log besar bisa lama; bawaan lama = 120)
ATTENDANCE_FK_BYDATE = env("ATTENDANCE_FK_BYDATE", default="")  # id tanda tangan FK_LoadGeneralLogDataByDate (i3/s3/i4/s4/d7/d8) hasil `fk_dump.py --bydate-test`; kosong = baca seluruh log (aman)
ATTENDANCE_SYNC_LATEST_HOURS = env.int("ATTENDANCE_SYNC_LATEST_HOURS", default=24)  # "Data terbaru" = sekian jam terakhir (mundur ke scan terakhir tersimpan bila lebih lama → tanpa celah)
ATTENDANCE_FK_NO_DISABLE = env.bool("ATTENDANCE_FK_NO_DISABLE", default=False)  # True = jangan kunci mesin saat membaca (bila firmware macet di tahap 'disable')
ATTENDANCE_FK_PYTHON32 = env("ATTENDANCE_FK_PYTHON32", default="")  # python.exe 32-bit untuk memuat DLL 32-bit; kosong = Python yang sedang jalan
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")  # enkripsi kolom sensitif; lihat apps/core/crypto.py
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
INSTALLED_APPS = [
    "apps.core.admin_config.HrisAdminConfig", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.core", "apps.hr.apps.HrConfig", "apps.poli.apps.PoliConfig", "apps.hrd", "apps.company", "apps.payroll.apps.PayrollConfig",
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
# SITE_MODE: "internal" (bawaan; aplikasi HRIS LAN) atau "public" (hanya situs profil + karir untuk internet; JANGAN jalankan mode public dengan
# URLconf internal). Mode public memakai config/urls_public.py: tidak ada /admin/, /employees/, /poli/, dll. Lihat docs/DEPLOY-PUBLIK.md.
SITE_MODE = env("SITE_MODE", default="internal")
ROOT_URLCONF = "config.urls_public" if SITE_MODE == "public" else "config.urls"
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
REQUEST_ATTACH_MAX_MB = env.int("REQUEST_ATTACH_MAX_MB", default=50)  # bukti pengajuan (foto/berkas): ukuran per berkas; ekstensi bebas (putaran 50)
REQUEST_ATTACH_MAX_FILES = env.int("REQUEST_ATTACH_MAX_FILES", default=10)  # jumlah berkas per pengajuan
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
# ---- Pemantauan: log, pelacakan error, alarm ----
# app.log = semua (INFO+); errors.log = HANYA ERROR+ beserta traceback (satu berkas kecil yang layak dipantau/di-tail). Folder lewat LOG_DIR.
LOG_DIR = Path(env("LOG_DIR", default="") or BASE_DIR)  # kosong di .env = folder aplikasi
# Traceback 5xx lewat email (opsional): isi ADMIN_EMAILS (butuh EMAIL_* di bawah). Email berisi cuplikan permintaan: jangan aktifkan bila email tidak aman.
ADMIN_EMAILS = env.list("ADMIN_EMAILS", default=[])
ADMINS = [("Admin HRIS", e) for e in ADMIN_EMAILS]
_ROTATING = "logging.handlers.RotatingFileHandler"
_LOG_HANDLERS = {
    "file": {"class": _ROTATING, "filename": LOG_DIR / "app.log", "maxBytes": 10_000_000, "backupCount": 10, "formatter": "std"},
    "errors": {"class": _ROTATING, "filename": LOG_DIR / "errors.log", "maxBytes": 10_000_000, "backupCount": 10, "formatter": "std", "level": "ERROR"},
}
if ADMINS:
    _LOG_HANDLERS["mail_admins"] = {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR", "include_html": False}
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(name)s %(levelname)s %(message)s"}},
    "handlers": _LOG_HANDLERS,
    "root": {"handlers": ["file", "errors"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["mail_admins"] if ADMINS else [], "level": "ERROR", "propagate": True}},  # 5xx → file + errors (+ email)
}
# Pelacakan error eksternal (opsional, mis. Sentry/GlitchTip self-hosted): pasang `pip install sentry-sdk` lalu isi SENTRY_DSN. Tanpa data pribadi (tanpa badan
# permintaan, tanpa cookie/header pengguna). Tidak aktif saat menjalankan tes.
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN and "test" not in sys.argv:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN, environment=env("SENTRY_ENVIRONMENT", default="production"), release=APP_VERSION, send_default_pii=False,
                        max_request_body_size="never", traces_sample_rate=env.float("SENTRY_TRACES_RATE", 0.0))
    except ImportError:
        import logging as _logging
        _logging.getLogger("hris.monitoring").warning("SENTRY_DSN diisi tetapi paket sentry-sdk belum terpasang; pelacakan error eksternal tidak aktif.")
# Health check & alarm (lihat apps/core/health.py, scripts/cron-hris.example, scripts/backup.sh)
HEALTH_MIN_FREE_MB = env.int("HEALTH_MIN_FREE_MB", 500)                                   # ruang disk bebas minimum di folder media/
BACKUP_STATUS_FILE = env("BACKUP_STATUS_FILE", default="/var/backups/hris/status.json")   # ditulis scripts/backup.sh; kosong = pemantauan backup mati
BACKUP_MAX_AGE_HOURS = env.int("BACKUP_MAX_AGE_HOURS", 26)                                # backup harian: >26 jam = anggap tidak berjalan
DRILL_STATUS_FILE = env("DRILL_STATUS_FILE", default="/var/backups/hris/restore_drill.json")   # ditulis scripts/restore_drill.sh; kosong = pemantauan latihan restore mati
DRILL_MAX_AGE_DAYS = env.int("DRILL_MAX_AGE_DAYS", 35)                                            # latihan restore harus berhasil minimal sebulan sekali
HEALTH_REQUIRE_DRILL = env.bool("HEALTH_REQUIRE_DRILL", False)                                    # True di produksi: tanpa catatan latihan restore = GAGAL
HEALTH_REQUIRE_BACKUP = env.bool("HEALTH_REQUIRE_BACKUP", False)                          # True di produksi: tanpa catatan backup = GAGAL
HEALTH_STATE_FILE = env("HEALTH_STATE_FILE", default=str(BASE_DIR / ".health_state.json"))  # status alarm terakhir (anti-banjir)
ALERT_WEBHOOK_URL = env("ALERT_WEBHOOK_URL", default="")                                  # Slack/Mattermost/Discord (JSON {text, content})
ALERT_EMAILS = env.list("ALERT_EMAILS", default=[])                                       # penerima alarm via email
ALERT_REPEAT_HOURS = env.int("ALERT_REPEAT_HOURS", 6)                                     # ulangi alarm selama masalah belum teratasi
LOGIN_URL = "/login/"; LOGIN_REDIRECT_URL = "/"; LOGOUT_REDIRECT_URL = "/login/"

# Kebijakan cuti/jadwal (asumsi bawaan; sesuaikan dengan peraturan perusahaan, lihat docs/PROGRESS.md §4)
ANNUAL_LEAVE_DAYS = 12            # jatah cuti tahunan (hari kerja)
ANNUAL_LEAVE_MIN_MONTHS = 12      # masa kerja minimum sebelum berhak jatah
REGULAR_OFF_WEEKDAYS = (6,)       # hari libur reguler (0=Senin … 6=Minggu); tidak dihitung sebagai hari cuti


# --- Company Profile & Lamar Kerja (putaran 48, Tahap 11). Semua opsional; angka = usulan awal.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")           # mis. https://wadimor.co.id ; dipakai untuk tautan di email pelamar & sitemap
PUBLIC_PREVIEW = env.bool("PUBLIC_PREVIEW", True)               # di mode internal: pratinjau situs di /situs/ (hanya login HRD/Superadmin; draf ikut tampil)
PRIVATE_MEDIA_ROOT = env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media"))  # CV pelamar (terenkripsi); JANGAN diarahkan ke folder yang disajikan Nginx
CAREER_MAX_UPLOAD_MB = env.int("CAREER_MAX_UPLOAD_MB", 3)
CAREER_RETENTION_MONTHS = env.int("CAREER_RETENTION_MONTHS", 12)  # lamaran tidak diterima dianonimkan setelah N bulan (x2 bila pelamar setuju talent pool)
CAREER_REQUIRE_EMAIL_VERIFY = env.bool("CAREER_REQUIRE_EMAIL_VERIFY", True)  # lamaran baru terlihat HRD setelah pelamar membuka tautan di email
CAREER_MIN_FILL_SECONDS = env.int("CAREER_MIN_FILL_SECONDS", 4)  # formulir terkirim lebih cepat dari ini dianggap bot
CAREER_UNVERIFIED_DAYS = env.int("CAREER_UNVERIFIED_DAYS", 7)    # lamaran yang tak pernah diverifikasi dihapus setelah N hari
PUBLIC_THROTTLE = {"apply": (6, 3600), "contact": (5, 3600), "lookup": (5, 3600), "search": (60, 60)}  # {zona: (jumlah, detik)} per IP
if "test" in sys.argv: PUBLIC_THROTTLE = {}
CLAMAV_ADDRESS = env("CLAMAV_ADDRESS", default="")             # "unix:/var/run/clamav/clamd.ctl" atau "tcp:127.0.0.1:3310"; kosong = tanpa pemindai (ditandai "belum dipindai")
CAREER_AV_REQUIRED = env.bool("CAREER_AV_REQUIRED", True)      # bila pemindai dikonfigurasi tetapi tidak menjawab: tolak unggahan (jangan loloskan diam-diam)
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost"); EMAIL_PORT = env.int("EMAIL_PORT", 25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default=""); EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@localhost")
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)  # pengirim email traceback (ADMIN_EMAILS) dan alarm
# CSP situs publik: tanpa 'unsafe-inline' (semua gaya/skrip berupa berkas statis). Diterapkan oleh view publik (apps/company/public_views.py).
PUBLIC_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
              "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; frame-src https://www.google.com")  # putaran 54: peta Google tersemat, hanya host ini

# --- Sinkronisasi HRIS <-> situs publik (apps/company/sync.py). Isi SAMA di kedua host; WAJIB >= 32 karakter acak, BEDA dari DJANGO_SECRET_KEY.
SYNC_SHARED_SECRET = env("SYNC_SHARED_SECRET", default="")
SYNC_STATE_FILE = env("SYNC_STATE_FILE", default=str(BASE_DIR / ".sync_serial"))        # publik: serial bundle terakhir (anti-replay)
PULL_STATE_FILE = env("PULL_STATE_FILE", default=str(BASE_DIR / ".pull_cursor"))        # HRIS: penanda lamaran terakhir yang ditarik
