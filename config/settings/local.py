from .base import *  # noqa
import dj_database_url
import sys

DEBUG = os.environ.get("DEBUG", "True").lower() in ("1", "true", "yes", "on")
LOCAL_DEV_HOST = os.environ.get("DEV_SERVER_HOST", "10.14.20.99").strip() or "10.14.20.99"
LOCAL_DEV_PORT = os.environ.get("DEV_SERVER_PORT", "8000").strip() or "8000"

SECURE_SSL_REDIRECT = False

if os.environ.get("ALLOW_ALL_HOSTS", "False").lower() in ("1", "true", "yes", "on"):
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [
        os.path.expandvars(host.strip())
        for host in os.environ.get("ALLOWED_HOSTS", f"{LOCAL_DEV_HOST},10.0.2.2").split(",")
        if os.path.expandvars(host.strip())
    ]
    if DEBUG and os.environ.get("KIS_STRICT_LOCAL_ALLOWED_HOSTS", "False").lower() not in ("1", "true", "yes", "on"):
        ALLOWED_HOSTS.extend(["localhost", "127.0.0.1", "0.0.0.0", LOCAL_DEV_HOST])
if LOCAL_DEV_HOST not in ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
    ALLOWED_HOSTS.append(LOCAL_DEV_HOST)
ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

SITE_URL = os.environ.get("SITE_URL", f"http://{LOCAL_DEV_HOST}:{LOCAL_DEV_PORT}").rstrip("/")
API_BASE_URL = os.environ.get("API_BASE_URL", SITE_URL).rstrip("/")

# In local development the app host changes frequently between loopback/LAN.
# Do not hard-fail JWT validation on issuer/audience drift for already-issued tokens.
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "ISSUER": None,
    "AUDIENCE": None,
}

IS_TEST_RUN = len(sys.argv) > 1 and sys.argv[1] == "test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", None)

# Without this, a test that calls some_task.delay() silently enqueues to
# whatever real broker CELERY_BROKER_URL points at and passes regardless of
# whether the task logic actually works — nothing in the test process ever
# consumes it. Eager mode runs the task body synchronously and in-process
# instead, and EAGER_PROPAGATES surfaces a real task exception as a test
# failure rather than swallowing it. Local/CI test runs only — production
# must never run tasks synchronously in the request/response cycle.
if IS_TEST_RUN:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# Local DB: leave default sqlite unless DATABASE_URL provided.
DATABASE_URL = os.environ.get("DATABASE_URL", None)
if IS_TEST_RUN and not TEST_DATABASE_URL:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "test_db.sqlite3"),
    }
elif TEST_DATABASE_URL:
    DATABASES["default"] = dj_database_url.parse(
        TEST_DATABASE_URL,
        conn_max_age=int(os.environ.get("PG_CONN_MAX_AGE", "600")),
    )
elif DATABASE_URL:
    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL,
        # In local dev, use short-lived connections (60 s) instead of the
        # production 600 s default. With the dev server running in threaded
        # mode each thread holds a connection for the full conn_max_age, so
        # 600 s × N threads can quickly exhaust PostgreSQL's max_connections.
        conn_max_age=int(os.environ.get("PG_CONN_MAX_AGE", "60")),
    )

# CONN_HEALTH_CHECKS: before reusing a persistent connection Django sends a
# cheap "SELECT 1" ping. If the server has closed it (e.g. idle-timeout,
# restart) Django opens a fresh one instead of getting an OperationalError.
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DATABASES["default"].setdefault("TEST", {})
if DATABASES["default"].get("ENGINE") == "django.db.backends.sqlite3":
    DATABASES["default"]["TEST"].setdefault("NAME", str(BASE_DIR / "test_db.sqlite3"))
else:
    DATABASES["default"]["TEST"].setdefault("NAME", "kis_test")

test_mirror = os.environ.get("TEST_DATABASE_MIRROR", "").strip()
if test_mirror:
    DATABASES["default"]["TEST"].setdefault("MIRROR", test_mirror)

# In local, make email backend console
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
