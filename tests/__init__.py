"""Shared test bootstrap.

The application configuration module reads its environment variables at import
time, so every test module has to share a single set-up. Keeping it in the
package ``__init__`` guarantees the environment is configured exactly once,
before any ``app.*`` module is imported, and lets the test modules run
together in one process without clobbering each other's database or upload
directory.
"""

import os
import tempfile
from pathlib import Path

from .database_guard import is_dedicated_test_url

TEST_ROOT = Path(tempfile.mkdtemp(prefix="zengenai-tests-"))

# Tests must run against a dedicated test database only: the finance-flow
# suites drop and recreate tables in setUp. CI can point
# ZENGENAI_TEST_DATABASE_URL at a disposable database (SQLite with a test
# filename such as test.db, or PostgreSQL with 'test' as its own name segment
# such as zengenai_test); otherwise a throwaway SQLite file per run is used.
# The development database is never used, and the guard in each suite's setUp
# re-checks this right before any drop_all.
_test_url = os.getenv("ZENGENAI_TEST_DATABASE_URL", "").strip()
if _test_url:
    if not is_dedicated_test_url(_test_url):
        raise RuntimeError(
            f"ZENGENAI_TEST_DATABASE_URL={_test_url!r} does not look like a dedicated "
            "test database (SQLite with a test filename such as test.db, or "
            "PostgreSQL with 'test' as its own name segment such as "
            "zengenai_test). Refusing to run tests against it."
        )
    os.environ["ZENGENAI_DATABASE_URL"] = _test_url
else:
    os.environ["ZENGENAI_DATABASE_URL"] = f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}"
os.environ["ZENGENAI_JWT_SECRET"] = "unit-test-secret-that-is-longer-than-32-characters"
os.environ["ZENGENAI_UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["ZENGENAI_MAX_UPLOAD_MB"] = "1"

# Never send real email from the test suite. The auth endpoints send a welcome
# email on registration, and ``load_dotenv()`` does not override variables that
# already exist in the environment, so blanking these here stops
# ``app.services.email._send`` from reaching a real SMTP server.
for _smtp_var in (
    "ZENGENAI_SMTP_HOST",
    "ZENGENAI_SMTP_FROM_EMAIL",
    "ZENGENAI_SMTP_USERNAME",
    "ZENGENAI_SMTP_PASSWORD",
):
    os.environ[_smtp_var] = ""
os.environ["ZENGENAI_SMTP_PORT"] = "587"
os.environ["ZENGENAI_SMTP_USE_SSL"] = "false"

# Defence in depth: make any SMTP connection attempt fail loudly instead of
# reaching a real mail server. ``app.services.email._send`` only swallows
# OSError/SMTPException, so this keeps a stray email from ever being sent.
import smtplib as _smtplib


def _blocked_smtp(*args, **kwargs):
    raise _smtplib.SMTPException("SMTP is disabled during tests.")


_smtplib.SMTP = _blocked_smtp
_smtplib.SMTP_SSL = _blocked_smtp
