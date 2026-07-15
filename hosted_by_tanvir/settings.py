from pathlib import Path
import sys
import cloudinary
from decouple import config
import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .env import (
    initialize_environment,
    env_bool,
    env_first,
    env_list,
    require_env,
    require_non_placeholder,
    resolve_allowed_hosts,
)

# Load layered env files before the first config(...) access.
initialize_environment()

BASE_DIR = Path(__file__).resolve().parent.parent

IS_TEST = "test" in sys.argv
DEBUG = env_bool(config, "DEBUG", default=False)
if DEBUG:
    SECRET_KEY = config(
        "SECRET_KEY",
        default="local-dev-secret-key-local-dev-secret-key-local-dev-secret-key",
    )
else:
    SECRET_KEY = require_non_placeholder(config, "SECRET_KEY")
    if len(SECRET_KEY) < 50:
        raise ImproperlyConfigured("SECRET_KEY must be at least 50 characters when DEBUG is False.")

ALLOWED_HOSTS = resolve_allowed_hosts(config, debug=DEBUG)
CSRF_TRUSTED_ORIGINS = env_list(config, "CSRF_TRUSTED_ORIGINS", default="")
render_hostname = config("RENDER_EXTERNAL_HOSTNAME", default="").strip()
if render_hostname:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_hostname}")

CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS))

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool(config, "SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Custom user model — must be set before first migration
AUTH_USER_MODEL = "accounts.Player"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",  # serves static in dev too
    "django.contrib.staticfiles",
    # Third-party
    "cloudinary",
    "cloudinary_storage",
    "django_htmx",
    # Local apps
    "accounts.apps.AccountsConfig",
    "tournament.apps.TournamentConfig",
    "standings.apps.StandingsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # right after SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "hosted_by_tanvir.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tournament.context_processors.pending_results",
                "tournament.context_processors.notification_badge",
                "tournament.context_processors.site_links",
            ],
        },
    },
]

WSGI_APPLICATION = "hosted_by_tanvir.wsgi.application"

# Database — Supabase PostgreSQL via DATABASE_URL
if IS_TEST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test_db.sqlite3",
        }
    }
else:
    database_url = require_env(config, "DATABASE_URL").strip()
    parse_kwargs = {
        "conn_max_age": 600,
        "conn_health_checks": True,
    }
    if not database_url.lower().startswith("sqlite"):
        parse_kwargs["ssl_require"] = not DEBUG

    DATABASES = {
        "default": dj_database_url.parse(
            database_url,
            **parse_kwargs,
        )
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

# Static files — WhiteNoise
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Cloudinary — media (team logos, player avatars, result screenshots)
DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
if DEBUG:
    cloudinary_cloud_name = config("CLOUDINARY_CLOUD_NAME", default="local-dev-cloud")
    cloudinary_api_key = config("CLOUDINARY_API_KEY", default="local-dev-key")
    cloudinary_api_secret = config("CLOUDINARY_API_SECRET", default="local-dev-secret")
else:
    cloudinary_cloud_name = require_non_placeholder(config, "CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key = require_non_placeholder(config, "CLOUDINARY_API_KEY")
    cloudinary_api_secret = require_non_placeholder(config, "CLOUDINARY_API_SECRET")

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": cloudinary_cloud_name,
    "API_KEY": cloudinary_api_key,
    "API_SECRET": cloudinary_api_secret,
}

cloudinary.config(
    cloud_name=cloudinary_cloud_name,
    api_key=cloudinary_api_key,
    api_secret=cloudinary_api_secret,
    secure=True,
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "tournament:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Email — password reset flow foundation
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env_bool(config, "EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env_bool(config, "EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=10, cast=int)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="webmaster@localhost")
SERVER_EMAIL = config("SERVER_EMAIL", default="root@localhost")
PASSWORD_RESET_TIMEOUT = config("PASSWORD_RESET_TIMEOUT", default=259200, cast=int)

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True.")

if not DEBUG and EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    require_non_placeholder(config, "EMAIL_HOST")
    require_non_placeholder(config, "EMAIL_HOST_USER")
    require_non_placeholder(config, "EMAIL_HOST_PASSWORD")
    DEFAULT_FROM_EMAIL = require_non_placeholder(config, "DEFAULT_FROM_EMAIL")

DISCORD_LINK = env_first(config, "DISCORD_LINK", "discord_link", default="")

# Celery / Redis task queue foundation.
# Redis stores task messages only; PostgreSQL/Supabase remains the source of truth.
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="").strip() or None
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = env_bool(config, "CELERY_TASK_ALWAYS_EAGER", default=IS_TEST)
CELERY_TASK_EAGER_PROPAGATES = env_bool(config, "CELERY_TASK_EAGER_PROPAGATES", default=IS_TEST)

# Phase 2.5 keeps current workflows synchronous by default. Future code can opt in
# to Celery-backed notification dispatch once the worker is part of the runtime.
NOTIFICATIONS_USE_CELERY = env_bool(config, "NOTIFICATIONS_USE_CELERY", default=False)
