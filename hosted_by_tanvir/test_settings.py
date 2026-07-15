import os

# Set safe test env values BEFORE importing base settings.
# This prevents base settings from pulling in production requirements
# during test startup.
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("CLOUDINARY_CLOUD_NAME", "test-cloud")
os.environ.setdefault("CLOUDINARY_API_KEY", "test-key")
os.environ.setdefault("CLOUDINARY_API_SECRET", "test-secret")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

from .settings import *  # noqa: F401,F403


DEBUG = True
SECRET_KEY = "test-secret-key-not-for-production"

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CSRF_TRUSTED_ORIGINS = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Disable production-style HTTPS/security behavior during tests.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_PROXY_SSL_HEADER = None

# Use simple local storage in tests.
DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

MEDIA_ROOT = BASE_DIR / "test_media"
MEDIA_URL = "/media/"

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": "test-cloud",
    "API_KEY": "test-key",
    "API_SECRET": "test-secret",
}

# Keep test side effects in memory where possible.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
NOTIFICATIONS_USE_CELERY = False
