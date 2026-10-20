"""Accept/reject cases for the test-database guard.

These tests exercise only the guard's URL classification; they never open a
connection and never drop anything, so they are safe to run anywhere.
"""

import unittest

from tests.database_guard import is_dedicated_test_url, require_test_database


ACCEPTED = [
    # PostgreSQL: "test" as its own underscore-separated segment.
    "postgresql+psycopg://zengenai_app@localhost:5432/zengenai_test",
    "postgresql+psycopg://zengenai_app@localhost:5432/test_zengenai",
    # SQLite: an explicit test filename, whatever the parent path looks like.
    "sqlite:////tmp/scratch-xyz/test.db",
    "sqlite:////tmp/scratch-xyz/zengenai_test.db",
    "sqlite:///test.db",
    # In-memory SQLite is ephemeral and safe to drop.
    "sqlite:///:memory:",
]

REJECTED = [
    # PostgreSQL development names: "test" must be a separate segment, so a
    # substring inside another word never qualifies.
    "postgresql+psycopg://zengenai_app@localhost:5432/zengenai",
    "postgresql+psycopg://zengenai_app@localhost:5432/contest",
    "postgresql+psycopg://zengenai_app@localhost:5432/mytest",
    "postgresql+psycopg://zengenai_app@localhost:5432/latest",
    # SQLite: only the filename is validated, and it must be an explicit test
    # file. A development database under a folder named "tests" is rejected.
    "sqlite:////projects/tests/dev.db",
    "sqlite:////projects/tests/contest.db",
    "sqlite:///contest.db",
    "sqlite:///mytest.db",
    "sqlite:////tmp/scratch-xyz/dev.db",
]


class DedicatedTestUrlTests(unittest.TestCase):
    def test_accepts_dedicated_test_databases(self):
        for url in ACCEPTED:
            with self.subTest(url=url):
                self.assertTrue(is_dedicated_test_url(url))

    def test_rejects_development_databases(self):
        for url in REJECTED:
            with self.subTest(url=url):
                self.assertFalse(is_dedicated_test_url(url))

    def test_require_test_database_accepts_test_url(self):
        # Must not raise.
        require_test_database("sqlite:///test.db")

    def test_require_test_database_rejects_development_url(self):
        with self.assertRaises(RuntimeError):
            require_test_database("postgresql+psycopg://zengenai_app@localhost:5432/zengenai")

    def test_temp_sqlite_database_url_is_accepted(self):
        # Mirrors the URL tests/__init__.py generates for the per-run temp DB.
        self.assertTrue(is_dedicated_test_url("sqlite:////tmp/zengenai-tests-abc123/test.db"))


if __name__ == "__main__":
    unittest.main()