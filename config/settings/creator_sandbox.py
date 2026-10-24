"""Isolated, disposable settings for the KIS "creator ecosystem" sandbox.

Used only for building/seeding the four synthetic creator channels and
exercising Channel Studio / broadcast workflows against them. Never used
by the app in normal dev/production — activate explicitly with:

    DJANGO_SETTINGS_MODULE=config.settings.creator_sandbox python manage.py <command>

Deliberately overrides DATABASES *after* `from .base import *` (base.py
already applied DATABASE_URL from .env by the time this runs) so this
settings module can never resolve to the real dev database, regardless
of what's in .env. Points at a dedicated local Postgres database instead
(`kis_creator_sandbox`, created once via `createdb`, same local server/
credentials as kis_local but a completely separate database — Postgres
databases share nothing with each other) — the schema uses Postgres-only
features (SearchVectorField/to_tsvector, GIN indexes), so plain SQLite
can't run these migrations at all.
"""
from .base import *  # noqa: F401,F403

DATABASES["default"] = {  # noqa: F405
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "kis_creator_sandbox",
    "USER": "nigel",
    "HOST": "localhost",
    "PORT": "5432",
}

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Seeding/demo work here must never reach a real mail provider or a shared
# Celery broker (video processing, notification fan-out, etc.) - mirrors
# config/settings/local.py's IS_TEST_RUN safety behavior.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# .env sets OBJECT_STORAGE_PROVIDER=s3, which base.py (imported above)
# already turned into STORAGES pointing at the REAL production S3 bucket
# (apps.media.storage_backends.S3MediaStorage). Seeded sandbox media must
# never land there — force plain local-filesystem storage under
# MEDIA_ROOT instead, unconditionally, regardless of .env.
OBJECT_STORAGE_PROVIDER = ""
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

SECURE_SSL_REDIRECT = False

# Matches the port this sandbox's verification server runs on (see
# `manage.py runserver 127.0.0.1:8111` in the seeding workflow) — public
# broadcasts serializers run avatar/banner/thumbnail/asset URLs through
# apps.core.public_web.safe_public_media_url, which requires an absolute
# http(s) URL and silently blanks anything schemeless/relative, so seeded
# media URLs must be absolute against this same host to render at all.
SITE_URL = "http://127.0.0.1:8111"
API_BASE_URL = "http://127.0.0.1:8111"
