from .base import *  # noqa
import dj_database_url

DEBUG = os.environ.get("DEBUG", "True").lower() in ("1", "true", "yes", "on")

SECURE_SSL_REDIRECT = False

if os.environ.get("ALLOW_ALL_HOSTS", "False").lower() in ("1", "true", "yes", "on"):
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,10.0.2.2").split(",") if host.strip()]
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

# Local DB: leave default sqlite unless DATABASE_URL provided.
DATABASE_URL = os.environ.get("DATABASE_URL", None)
if DATABASE_URL:
    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=int(os.environ.get("PG_CONN_MAX_AGE", "600")),
    )

DATABASES["default"].setdefault("TEST", {})
DATABASES["default"]["TEST"].setdefault("NAME", "kis_test")
DATABASES["default"]["TEST"].setdefault("MIRROR", "default")

# In local, make email backend console
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
