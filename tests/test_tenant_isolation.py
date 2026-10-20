import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models.finance import FinanceDocument, FinanceWorkspace
from app.models.records import FinanceRecord
from app.models.specialist_access import SpecialistAccess
from app.models.user import User
from tests.database_guard import require_test_database


class TenantIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        engine.dispose()

    def setUp(self):
        # Refuse to run (and therefore never drop tables) unless the engine
        # points at a dedicated test database.
        require_test_database(engine)
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        Path(os.environ["ZENGENAI_UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
        for file_path in Path(os.environ["ZENGENAI_UPLOAD_DIR"]).iterdir():
            if file_path.is_file():
                file_path.unlink()

        self.tokens = {}
        for suffix in ("a", "b"):
            response = self.client.post(
                "/api/v1/auth/register",
                json={"name": f"User {suffix.upper()}", "email": f"zengen-test-{suffix}@example.com", "password": f"Secure-Password-{suffix}-123"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.tokens[suffix] = response.json()["access_token"]
            # Finance routes are gated behind the finance_analyst specialist grant.
            with SessionLocal() as session:
                user_id = session.query(User).filter_by(
                    email=f"zengen-test-{suffix}@example.com"
                ).one().id
                session.add(
                    SpecialistAccess(
                        user_id=user_id,
                        specialist_key="finance_analyst",
                        enabled=True,
                    )
                )
                session.commit()
            workspace_response = self.client.put(
                "/api/v1/finance/workspace",
                headers=self.headers(suffix),
                json={"business_name": f"Business {suffix.upper()}", "currency": "USD"},
            )
            self.assertEqual(workspace_response.status_code, 200, workspace_response.text)

        uploaded = self.client.post(
            "/api/v1/finance/documents",
            headers=self.headers("b"),
            files={"file": ("private.png", b"\x89PNG\r\n\x1a\n" + b"safe image bytes", "image/png")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.document = uploaded.json()
        self.storage_file = next(Path(os.environ["ZENGENAI_UPLOAD_DIR"]).iterdir())
        with SessionLocal() as session:
            workspace = session.query(FinanceWorkspace).filter_by(business_name="Business B").one()
            record = FinanceRecord(
                workspace_id=workspace.id,
                document_id=self.document["id"],
                record_type="invoice",
                counterparty="Private Vendor",
                status="needs_review",
            )
            session.add(record)
            session.commit()
            self.record_id = record.id

    def headers(self, user):
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def test_user_a_cannot_read_download_process_delete_or_modify_user_b_document(self):
        document_id = self.document["id"]
        self.assertEqual(self.client.get(f"/api/v1/finance/documents/{document_id}", headers=self.headers("a")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/finance/documents/{document_id}/download", headers=self.headers("a")).status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/finance/documents/{document_id}/process", headers=self.headers("a")).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/finance/documents/{document_id}", headers=self.headers("a")).status_code, 404)
        self.assertTrue(self.storage_file.exists())
        self.assertEqual(self.client.get(f"/api/v1/finance/documents/{document_id}", headers=self.headers("b")).status_code, 200)
        owner_download = self.client.get(f"/api/v1/finance/documents/{document_id}/download", headers=self.headers("b"))
        self.assertEqual(owner_download.status_code, 200)
        self.assertEqual(owner_download.headers["x-content-type-options"], "nosniff")

    def test_record_must_match_both_current_workspace_and_document_workspace(self):
        with SessionLocal() as session:
            workspace_a = session.query(FinanceWorkspace).filter_by(business_name="Business A").one()
            misplaced = FinanceRecord(
                workspace_id=workspace_a.id,
                document_id=self.document["id"],
                record_type="invoice",
                counterparty="Mislinked Vendor",
                status="needs_review",
            )
            session.add(misplaced)
            session.commit()
            misplaced_id = misplaced.id

        self.assertEqual(self.client.get(f"/api/v1/finance/records/{misplaced_id}", headers=self.headers("a")).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/finance/records", headers=self.headers("a")).json(), [])

    def test_user_a_cannot_read_or_modify_user_b_finance_record(self):
        record_url = f"/api/v1/finance/records/{self.record_id}"
        self.assertEqual(self.client.get(record_url, headers=self.headers("a")).status_code, 404)
        self.assertEqual(
            self.client.patch(record_url + "/confirm", headers=self.headers("a"), json={"counterparty": "Changed"}).status_code,
            404,
        )
        self.assertEqual(self.client.get(record_url, headers=self.headers("b")).json()["counterparty"], "Private Vendor")
        self.assertEqual(self.client.get("/api/v1/finance/records", headers=self.headers("a")).json(), [])

    def test_upload_requires_real_file_signature_and_generated_storage_name(self):
        bad_file = self.client.post(
            "/api/v1/finance/documents",
            headers=self.headers("a"),
            files={"file": ("invoice.pdf", b"this is not a PDF", "application/pdf")},
        )
        self.assertEqual(bad_file.status_code, 415)
        self.assertNotEqual(self.storage_file.name, "private.png")
        self.assertNotIn("storage_name", self.document)

    def test_upload_enforces_size_limit(self):
        too_large = self.client.post(
            "/api/v1/finance/documents",
            headers=self.headers("a"),
            files={"file": ("large.pdf", b"%PDF-" + b"x" * (1024 * 1024), "application/pdf")},
        )
        self.assertEqual(too_large.status_code, 413)


if __name__ == "__main__":
    unittest.main()
