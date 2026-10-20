"""Guard rails so the test suites can never drop tables on a real database.

The finance-flow test modules call ``Base.metadata.drop_all`` in ``setUp``.
Before that can happen they call :func:`require_test_database`, which refuses
to run unless the configured database is clearly a dedicated test database:

* SQLite in memory, or a file whose filename is an explicit test database
  such as ``test.db`` or ``zengenai_test.db`` (``test`` must be its own
  underscore-separated segment of the filename; the parent path is never
  considered), or
* PostgreSQL whose database name contains ``test`` as its own
  underscore-separated segment (for example ``zengenai_test`` or
  ``test_zengenai``).

Development names such as ``zengenai`` or ``contest`` can therefore never
pass, and neither can a SQLite file that merely lives under a folder named
``tests``. This keeps a misconfigured shell (a backend ``.env`` pointing at
the development database, or a stray ``ZENGENAI_DATABASE_URL``) from having
its tables dropped by the test suite. See ``tests/test_database_guard.py``
for the exact accept/reject cases.
"""

from sqlalchemy.engine import make_url


def _has_test_segment(name: str) -> bool:
    """True when ``test`` is one of the underscore-separated segments of *name*.

    Substrings never count: ``contest`` and ``mytest`` have no ``test``
    segment, while ``zengenai_test`` and ``test_zengenai`` do.
    """
    return "test" in (name or "").lower().split("_")


def is_dedicated_test_url(url: object) -> bool:
    """Return True when *url* points at a database that is safe to drop."""
    parsed = make_url(str(url))
    backend = parsed.get_backend_name()
    database = parsed.database or ""
    if backend == "sqlite":
        # In-memory databases are ephemeral and safe to drop.
        if database in (":memory:", ""):
            return True
        # Validate the filename, not the parent path: .../tests/dev.db is a
        # development database, while .../tests/test.db is an explicit test DB.
        filename = database.replace("\\", "/").rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        return _has_test_segment(stem)
    if backend in ("postgresql", "postgres"):
        # zengenai_test / test_zengenai pass; zengenai / contest / mytest do not.
        return _has_test_segment(database)
    return False


def require_test_database(engine) -> None:
    """Raise RuntimeError unless *engine* points at a dedicated test database."""
    url = getattr(engine, "url", None) or make_url(str(engine))
    if is_dedicated_test_url(url):
        return
    raise RuntimeError(
        f"Refusing to run tests against {url.render_as_string(hide_password=True)}: "
        "it does not look like a dedicated test database, and the finance-flow "
        "suites drop and recreate tables in setUp. Run the suite through the "
        "tests package (which configures a temporary SQLite database), or set "
        "ZENGENAI_TEST_DATABASE_URL to a disposable database: SQLite with a "
        "test filename such as test.db, or PostgreSQL with 'test' as its own "
        "segment such as zengenai_test."
    )