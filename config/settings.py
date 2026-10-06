from pathlib import Path
import environ
BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.core", "apps.hr", "apps.poli",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RateLimitLoginMiddleware",
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
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
LANGUAGE_CODE = "id"; TIME_ZONE = "Asia/Jakarta"; USE_TZ = True
STATIC_URL = "static/"; STATIC_ROOT = BASE_DIR / "static_root"
MEDIA_ROOT = BASE_DIR / "media"  # dokumen karyawan: sajikan lewat view ber-permission, bukan langsung
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_RATE_LIMIT = (5, 300)  # 5 percobaan / 5 menit per IP
LOGGING = {"version": 1, "disable_existing_loggers": False,
  "handlers": {"file": {"class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR / "app.log", "maxBytes": 10_000_000, "backupCount": 10}},
  "root": {"handlers": ["file"], "level": "INFO"}}
LOGIN_URL = "/login/"; LOGIN_REDIRECT_URL = "/"; LOGOUT_REDIRECT_URL = "/login/"
