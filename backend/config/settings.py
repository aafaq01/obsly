"""Django settings, driven entirely by environment variables.

Deliberately a single module rather than a base/dev/prod package. The environments differ by a
handful of values, not by structure, and a single file makes the whole configuration greppable.
Split it when two environments genuinely need different INSTALLED_APPS, not before.
"""

from pathlib import Path

import django_stubs_ext
import environ

# Makes ModelAdmin[Model] and friends subscriptable at runtime. Without it those annotations are
# a TypeError on import, and dropping them instead would fail mypy's strict disallow_any_generics.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_CORS_ORIGINS=(list, ["http://localhost:5173"]),
)
# overwrite=False so a real environment variable always beats the .env file. Tests set their
# environment before Django loads and must not have it silently replaced by a developer's .env.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# --- Core -------------------------------------------------------------------

# No default: a missing key must fail loudly at boot, never silently fall back to a shared one.
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # For the trigram index that makes log substring search an index lookup.
    "django.contrib.postgres",
    "rest_framework",
    "apps.projects",
    "apps.events",
    "apps.issues",
    "apps.tracing",
    "apps.logs",
    "apps.ingest",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files straight from gunicorn, so the container needs no separate
    # static file server. Must sit directly after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------

# Postgres in every environment including tests. Event payloads use JSONB and the ingest path
# will lean on Postgres-specific behaviour, so testing against SQLite would test a different
# system than the one that ships.
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth -------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N / static ----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Obsly -------------------------------------------------------------------

# The origin SDKs should send to, used to render DSNs. Not derivable from a request: the host an
# operator browses the admin on is not necessarily the host their services can reach.
OBSLY_INGEST_ORIGIN = env("OBSLY_INGEST_ORIGIN", default="http://localhost:8081")

# Hard ceiling on a single envelope. The endpoint is public and authenticated only by a
# write-only key, so an unbounded body is an unbounded memory allocation for anyone holding one.
# nginx enforces its own client_max_body_size above this; both limits are deliberate.
OBSLY_MAX_ENVELOPE_BYTES = env.int("OBSLY_MAX_ENVELOPE_BYTES", default=1_000_000)

# --- REST framework ---------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_RATES": {
        # Login is the one anonymous write endpoint, so it is the one worth guarding
        # against credential stuffing. Generous enough that a person mistyping twice is
        # unaffected, tight enough that a script is not.
        "login": env("OBSLY_LOGIN_RATE", default="10/min"),
    },
}

# --- Security ---------------------------------------------------------------

# Transport-independent. Always on, in every environment.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HTTPS-dependent hardening. Defaults on whenever DEBUG is off, so a production deploy cannot
# forget it. The local docker stack serves plain HTTP and opts out explicitly — without that,
# SSL redirect would bounce every request and Secure cookies would never reach the browser,
# so nobody could log in. Never set DJANGO_HTTPS=False on anything internet-facing.
HTTPS_ENABLED = env.bool("DJANGO_HTTPS", default=not DEBUG)

if HTTPS_ENABLED:
    SECURE_SSL_REDIRECT = True
    # Load balancer and Kubernetes probes reach the pod over plain HTTP inside the cluster;
    # redirecting them to HTTPS makes every probe fail. Health exposes no sensitive data.
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Logging ----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
