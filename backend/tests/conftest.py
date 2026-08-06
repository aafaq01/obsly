"""Test environment defaults.

Imported by pytest before Django is configured, so this is the one place that can supply the
settings a fresh clone needs without a `.env` file. Real values still come from the environment
when present — CI sets them explicitly.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-key-not-used-outside-tests")
os.environ.setdefault("DATABASE_URL", "postgres://obsly:obsly@localhost:5432/obsly")
os.environ.setdefault("DJANGO_DEBUG", "False")
