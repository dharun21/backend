import os
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models.finance import FinanceDocument, FinanceWorkspace
from app.models.records import FinanceRecord
from app.models.specialist_access import SpecialistAccess
from app.models.user import User
from tests.database_guard import require_test_database


class FinanceAnalystFlowTests(unittest.TestCase):
    """End-to-end coverage for onboarding, chat and intelligence endpoints."""

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

        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "name": "Analyst User",
                "email": "zengen-finance@example.com",
                "password": "Secure-Password-123",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self._grant_finance_analyst_access()

    def _user_id(self):
        with SessionLocal() as session:
            return session.query(User).filter_by(email="zengen-finance@example.com").one().id

    def _grant_finance_analyst_access(self):
        """Finance Analyst routes require the finance_analyst specialist grant."""
        with SessionLocal() as session:
            user_id = session.query(User).filter_by(email="zengen-finance@example.com").one().id
            session.add(
                SpecialistAccess(
                    user_id=user_id,
                    specialist_key="finance_analyst",
                    enabled=True,
                )
            )
            session.commit()

    def _seed_confirmed_records(self):
        with SessionLocal() as session:
            workspace = FinanceWorkspace(owner_id=self._user_id(), business_name="Coffee Co", currency="INR")
            session.add(workspace)
            session.commit()
            workspace_id = workspace.id
            document = FinanceDocument(
                workspace_id=workspace_id,
                original_name="invoice.pdf",
                storage_name="seed.pdf",
                extension=".pdf",
                size_bytes=10,
                status="confirmed",
            )
            session.add(document)
            session.commit()
            today = date.today()
            session.add(
                FinanceRecord(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    record_type="invoice",
                    counterparty="Acme Corp",
                    reference_number="INV-1",
                    issue_date=today - timedelta(days=10),
                    due_date=today + timedelta(days=5),
                    currency="INR",
                    total_amount=5000,
                    status="confirmed",
                )
            )
            session.add(
                FinanceRecord(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    record_type="expense",
                    counterparty="Bean Supplier",
                    reference_number="BILL-1",
                    issue_date=today - timedelta(days=8),
                    due_date=today - timedelta(days=1),
                    currency="INR",
                    total_amount=2000,
                    status="confirmed",
                )
            )
            session.commit()


    # ------------------------------------------------------------------ #
    # Onboarding
    # ------------------------------------------------------------------ #
    def test_onboarding_session_start_and_completion(self):
        session = self.client.get("/api/v1/onboarding/session", headers=self.headers)
        self.assertEqual(session.status_code, 200, session.text)
        self.assertFalse(session.json()["completed"])

        start = self.client.get("/api/v1/onboarding/start", headers=self.headers)
        self.assertEqual(start.status_code, 200, start.text)
        body = start.json()
        self.assertEqual(body["current_step"], 0)
        self.assertEqual(body["question"]["id"], "business_type")
        self.assertTrue(body["question"]["options"])

        answers = [
            ("business_type", "Retail"),
            ("industry", "Food & Beverage"),
            ("country", "India"),
            ("currency", "INR"),
            ("financial_year", "April - March"),
            ("what_they_sell", "Coffee beans"),
            ("revenue_model", "One-time sales"),
            ("business_stage", "Growing"),
            ("main_goal", "Improve profit"),
            ("biggest_challenge", "Rising costs"),
            ("targets", "100000 monthly revenue"),
        ]
        for step, (question_id, value) in enumerate(answers):
            start = self.client.get("/api/v1/onboarding/start", headers=self.headers)
            current = start.json()
            self.assertEqual(current["current_step"], step)
            self.assertEqual(current["question"]["id"], question_id)

            submit = self.client.post(
                "/api/v1/onboarding/submit",
                headers=self.headers,
                json={"question_id": question_id, "answer": value, "custom_answer": None},
            )
            self.assertEqual(submit.status_code, 200, submit.text)
            result = submit.json()
            if result["completed"]:
                self.assertTrue(result["completed"])
            else:
                self.assertEqual(result["current_step"], step + 1)
                self.assertIsNotNone(result["next_question"])

        session = self.client.get("/api/v1/onboarding/session", headers=self.headers)
        self.assertTrue(session.json()["completed"])

        profile = self.client.get("/api/v1/onboarding/profile", headers=self.headers)
        self.assertEqual(profile.status_code, 200, profile.text)
        profile_body = profile.json()
        self.assertEqual(profile_body["business_type"], "Retail")
        self.assertEqual(profile_body["currency"], "INR")
        self.assertEqual(profile_body["main_goal"], "Improve profit")
        self.assertEqual(profile_body["targets"], [{"text": "100000 monthly revenue"}])

    def test_onboarding_skipped_question_advances_the_session(self):
        self.client.post(
            "/api/v1/onboarding/submit",
            headers=self.headers,
            json={"question_id": "business_type", "answer": None, "custom_answer": None},
        )
        session = self.client.get("/api/v1/onboarding/session", headers=self.headers)
        self.assertEqual(session.json()["current_step"], 1)

    def test_onboarding_requires_authentication(self):
        self.assertEqual(self.client.get("/api/v1/onboarding/session").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/onboarding/profile").status_code, 401)

    def test_routes_require_finance_analyst_specialist_access(self):
        """A user without the finance_analyst grant must be rejected."""
        with SessionLocal() as session:
            grant = session.query(SpecialistAccess).filter_by(
                user_id=self._user_id(), specialist_key="finance_analyst"
            ).one()
            grant.enabled = False
            session.commit()

        self.assertEqual(self.client.get("/api/v1/onboarding/session", headers=self.headers).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/onboarding/profile", headers=self.headers).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/v1/onboarding/submit",
                headers=self.headers,
                json={"question_id": "business_type", "answer": "Retail", "custom_answer": None},
            ).status_code,
            403,
        )

    def test_profile_update_saves_fields(self):
        updated = self.client.put(
            "/api/v1/onboarding/profile",
            headers=self.headers,
            json={"business_type": "Consulting", "currency": "GBP"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["business_type"], "Consulting")
        self.assertEqual(updated.json()["currency"], "GBP")


    # ------------------------------------------------------------------ #
    # Chat
    # ------------------------------------------------------------------ #
    def test_chat_send_creates_conversation_and_persists_messages(self):
        response = self.client.post(
            "/api/v1/chat/send",
            headers=self.headers,
            json={"message": "What is my total income?", "domain": "finance_analyst"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["conversation_id"])
        self.assertIn("reply", body)
        self.assertTrue(body["needs_more_info"])
        self.assertEqual(body["suggested_questions"], [])  # No confirmed records yet.

        messages = self.client.get(
            f"/api/v1/chat/conversations/{body['conversation_id']}/messages",
            headers=self.headers,
        )
        self.assertEqual(messages.status_code, 200, messages.text)
        payload = messages.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["role"], "user")
        self.assertEqual(payload[1]["role"], "assistant")
        # `metadata` must be serialised (not the SQLAlchemy MetaData object).
        self.assertIsInstance(payload[1]["metadata"], dict)

        conversations = self.client.get("/api/v1/chat/conversations", headers=self.headers)
        self.assertEqual(conversations.status_code, 200, conversations.text)
        self.assertEqual(len(conversations.json()), 1)

    def test_chat_replies_grounded_in_confirmed_records(self):
        self._seed_confirmed_records()
        response = self.client.post(
            "/api/v1/chat/send",
            headers=self.headers,
            json={"message": "How much is my total income and profit?", "domain": "finance_analyst"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("5,000.00", body["reply"])
        self.assertIn("3,000.00", body["reply"])
        labels = [f["label"] for f in body["metadata"]["supporting_figures"]]
        self.assertIn("Recorded income (all time)", labels)
        self.assertIn("Income minus expenses (all time)", labels)

    def test_chat_bills_due_soon_branch_returns_a_reply(self):
        self._seed_confirmed_records()
        response = self.client.post(
            "/api/v1/chat/send",
            headers=self.headers,
            json={"message": "Which bills are due soon?", "domain": "finance_analyst"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["message_type"], "fact_lookup")
        self.assertIn("bill", body["reply"].lower())

    def test_finance_education_and_explicit_calculation_use_reviewed_routes(self):
        concept = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "What is the difference between cash received and revenue recorded?", "domain": "finance_analyst"},
        )
        self.assertEqual(concept.status_code, 200, concept.text)
        self.assertEqual(concept.json()["metadata"]["engine"], "finance_reference")
        self.assertTrue(concept.json()["metadata"]["sources"])

        generic_invoice = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "Explain a sales invoice", "domain": "finance_analyst"},
        )
        self.assertEqual(generic_invoice.status_code, 200, generic_invoice.text)
        self.assertEqual(generic_invoice.json()["metadata"]["engine"], "finance_reference")
        self.assertIn("customer payment", generic_invoice.json()["reply"])

        calculation = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "Calculate break even: fixed costs 1001, price per unit 10, variable cost per unit 6", "domain": "finance_analyst"},
        )
        self.assertEqual(calculation.status_code, 200, calculation.text)
        self.assertEqual(calculation.json()["metadata"]["engine"], "finance_calculator")
        self.assertIn("251 whole units", calculation.json()["reply"])

    def test_cash_scenario_uses_confirmed_invoices_and_clarifies_ambiguous_group(self):
        self._seed_confirmed_records()
        scenario = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "What if all unpaid invoices are paid next month?", "domain": "finance_analyst"},
        )
        self.assertEqual(scenario.status_code, 200, scenario.text)
        self.assertEqual(scenario.json()["metadata"]["engine"], "local_finance_tools")
        self.assertIn("INR 5,000.00", scenario.json()["reply"])
        self.assertIn("INV-1", scenario.json()["reply"])

        ambiguous = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "What if these invoices are paid next month?", "domain": "finance_analyst"},
        )
        self.assertEqual(ambiguous.status_code, 200, ambiguous.text)
        self.assertTrue(ambiguous.json()["needs_more_info"])

    def test_mixed_currency_records_are_not_added_to_workspace_totals(self):
        self._seed_confirmed_records()
        with SessionLocal() as session:
            workspace = session.query(FinanceWorkspace).filter_by(owner_id=self._user_id()).one()
            document = session.query(FinanceDocument).filter_by(workspace_id=workspace.id).one()
            session.add(FinanceRecord(
                workspace_id=workspace.id, document_id=document.id,
                record_type="invoice", reference_number="USD-1", currency="USD",
                issue_date=date.today(), due_date=None, total_amount=100,
                status="confirmed", payment_status="unpaid",
            ))
            session.commit()
        overview = self.client.get("/api/v1/intelligence/overview", headers=self.headers)
        self.assertEqual(overview.status_code, 200, overview.text)
        self.assertEqual(overview.json()["total_income"], 5000.0)
        self.assertEqual(len(overview.json()["outstanding_invoices"]), 2)
        self.assertIn("other currencies", overview.json()["data_completeness_note"])

        scenario = self.client.post(
            "/api/v1/chat/send", headers=self.headers,
            json={"message": "What if all unpaid invoices are paid next month?", "domain": "finance_analyst"},
        )
        self.assertEqual(scenario.status_code, 200, scenario.text)
        self.assertIn("INR 5,000.00", scenario.json()["reply"])
        self.assertIn("USD 100.00", scenario.json()["reply"])

    def test_chat_delete_conversation(self):
        created = self.client.post("/api/v1/chat/conversations", headers=self.headers, json={"title": "Temp"})
        self.assertEqual(created.status_code, 200, created.text)
        conversation_id = created.json()["id"]
        deleted = self.client.delete(f"/api/v1/chat/conversations/{conversation_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        remaining = self.client.get("/api/v1/chat/conversations", headers=self.headers)
        self.assertEqual(remaining.json(), [])

    def test_chat_requires_authentication(self):
        self.assertEqual(self.client.get("/api/v1/chat/conversations").status_code, 401)
        self.assertEqual(
            self.client.post("/api/v1/chat/send", json={"message": "hi"}).status_code,
            401,
        )

    # ------------------------------------------------------------------ #
    # Intelligence
    # ------------------------------------------------------------------ #
    def test_intelligence_overview_reflects_confirmed_records(self):
        self._seed_confirmed_records()
        response = self.client.get("/api/v1/intelligence/overview", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total_income"], 5000.0)
        self.assertEqual(body["total_expenses"], 2000.0)
        self.assertEqual(body["net_profit"], 3000.0)
        # Due lists follow the record's classification: the confirmed invoice due
        # in 5 days is outstanding, and the confirmed bill that passed its due
        # date is an overdue bill, not an overdue customer invoice.
        self.assertEqual(len(body["outstanding_invoices"]), 1)
        self.assertEqual(len(body["overdue_invoices"]), 0)
        self.assertEqual(len(body["overdue_bills"]), 1)
        self.assertEqual(len(body["bills_due_soon"]), 0)

    def test_intelligence_break_even_and_gross_margin(self):
        break_even = self.client.get(
            "/api/v1/intelligence/break-even",
            headers=self.headers,
            params={"fixed_costs": 1000, "price_per_unit": 10, "variable_cost_per_unit": 6},
        )
        self.assertEqual(break_even.status_code, 200, break_even.text)
        self.assertEqual(break_even.json()["break_even_units"], 250.0)

        margin = self.client.get(
            "/api/v1/intelligence/gross-margin",
            headers=self.headers,
            params={"revenue": 200, "cogs": 120},
        )
        self.assertEqual(margin.status_code, 200, margin.text)
        self.assertEqual(margin.json()["gross_margin_percent"], 40.0)

    def test_intelligence_requires_authentication(self):
        self.assertEqual(self.client.get("/api/v1/intelligence/overview").status_code, 401)


if __name__ == "__main__":
    unittest.main()
