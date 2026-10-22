import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models.specialist_access import SpecialistAccess
from app.models.user import User
from tests.database_guard import require_test_database


class GeneralChatTests(unittest.TestCase):
    def setUp(self):
        require_test_database(engine)
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            self.user = User(name="Chat Tester", email="chat@example.com", password_hash="unused")
            db.add(self.user)
            db.flush()
            db.add(SpecialistAccess(user_id=self.user.id, specialist_key="finance_analyst", enabled=True))
            db.commit()
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.general = patch("app.services.ai_service.generate_general_model_reply", return_value=("A useful general answer.", "available")).start()
        self.finance = patch("app.services.ai_service.generate_local_model_reply", return_value=(None, "disabled")).start()

    def tearDown(self):
        patch.stopall()
        app.dependency_overrides.pop(get_current_user, None)
        self.client.close()

    def send(self, message, **kwargs):
        return self.client.post("/api/v1/chat/send", json={"message": message, **kwargs})

    def test_new_chat_defaults_to_general_and_keeps_history(self):
        first = self.send("Why is the sky blue?")
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["metadata"]["engine"], "ollama_local_general_model")
        self.assertEqual(body["suggested_questions"], [])
        self.assertEqual(self.general.call_args.args[:2], ("general", "Why is the sky blue?"))
        second = self.send("Explain that more simply", conversation_id=body["conversation_id"])
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self.general.call_args.args[2]), 2)
        self.finance.assert_not_called()

    def test_general_questions_in_existing_finance_chat_use_general_model(self):
        created = self.client.post("/api/v1/chat/conversations", json={"domain": "finance_analyst"}).json()
        for question in ["Why is the sky blue?", "Recommend a laptop for college", "I feel lonely today", "Write a Python function to sort a list", "What is inflation?"]:
            with self.subTest(question=question):
                response = self.send(question, conversation_id=created["id"])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["metadata"]["engine"], "ollama_local_general_model")
                self.assertEqual(response.json()["suggested_questions"], [])
                self.assertEqual(self.general.call_args.args[1], question)
        self.finance.assert_not_called()

    def test_topic_change_after_finance_answer_does_not_repeat_finance(self):
        first = self.send("What is my total income?", domain="finance_analyst")
        self.assertEqual(first.status_code, 200, first.text)
        self.general.assert_not_called()
        response = self.send("What is the capital of France?", conversation_id=first.json()["conversation_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metadata"]["engine"], "ollama_local_general_model")

    def test_unavailable_model_returns_retry_error_without_saving_fake_reply(self):
        self.general.return_value = (None, "unavailable")
        response = self.send("Explain gravity")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.client.get("/api/v1/chat/conversations").json(), [])

    def test_existing_conversation_access_is_checked_when_domain_omitted(self):
        created = self.client.post("/api/v1/chat/conversations", json={"domain": "finance_analyst"}).json()
        with SessionLocal() as db:
            access = db.query(SpecialistAccess).filter_by(user_id=self.user.id).one()
            access.enabled = False
            db.commit()
        response = self.send("Hi", conversation_id=created["id"])
        self.assertEqual(response.status_code, 403, response.text)
        self.general.assert_not_called()

    def test_create_conversation_defaults_to_general(self):
        response = self.client.post("/api/v1/chat/conversations", json={"title": "Everyday questions"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["domain"], "general")
