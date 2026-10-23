"""Local Finance Analyst: trained intent classification + deterministic finance tools."""

from __future__ import annotations

import re

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR
from app.models.chat import ChatConversation, ChatMessage
from app.models.finance import FinanceDocument, FinanceWorkspace
from app.models.onboarding import BusinessProfile
from app.models.records import FinanceRecord
from app.models.user import User
from app.services.finance_intent_model import finance_intent_model, tokenize
from app.services.finance_intelligence import FinanceIntelligence
from app.services.finance_advanced import answer_advanced_finance_workspace
from app.services.finance_business_type import answer_business_type_finance
from app.services.finance_learning import FinanceLearningAnswer, answer_finance_learning, answer_finance_workspace_limit
from app.services.finance_people_costs import answer_finance_people_costs
from app.services.finance_payroll_workspace import answer_finance_payroll_workspace
from app.services.local_finance_model import generate_reply as generate_local_model_reply
from app.services.local_general_model import generate_reply as generate_general_model_reply
from app.services.local_general_model import LocalModelUnavailable, unavailable_message
from app.services.live_information import LiveAnswer, answer_live_question


class AIService:
    """Runs entirely locally; financial facts come only from this user's records."""

    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

    def generate_reply(
        self,
        conversation_id: str | None,
        user_message: str,
        attach_document_id: str | None = None,
        domain: str = "general",
    ) -> dict[str, Any]:
        if domain != "finance_analyst":
            return self._generate_general_domain_reply(conversation_id, user_message, domain)
        if attach_document_id is None:
            live_answer = answer_live_question(user_message)
            if live_answer:
                return self._generate_general_domain_reply(conversation_id, user_message, domain, live_answer)

        conversation = self._get_or_create_conversation(conversation_id, domain)
        previous_assistant = self.db.scalar(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        prior_count = self.db.scalar(
            select(ChatMessage.id).where(ChatMessage.conversation_id == conversation.id).limit(1)
        )

        user_msg = ChatMessage(conversation_id=conversation.id, role="user", content=user_message)
        self.db.add(user_msg)
        self.db.flush()
        history_rows = list(self.db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation.id,
                ChatMessage.id != user_msg.id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(6)
        ).all())
        conversation_history = [
            {"role": item.role, "content": item.content[:1600]}
            for item in reversed(history_rows)
            if item.role in {"user", "assistant"}
        ]

        previous_metadata = (previous_assistant.message_metadata or {}) if previous_assistant else {}
        previous_documents = previous_metadata.get("supporting_documents") or []
        message_words = set(tokenize(user_message))
        document_reference = bool(message_words.intersection({"it", "that", "this", "doc", "docs", "document", "file", "attachment"}))
        previous_intent = previous_metadata.get("intent")
        intent, confidence = finance_intent_model.predict(user_message)
        # The finance classifier was trained only on finance examples. Require
        # topical evidence before treating an unrelated question as a finance intent.
        finance_words = {"income", "expenses", "expense", "revenue", "profit", "profits", "profitability",
                         "finances", "financial", "invoice", "invoices", "bill", "bills", "supplier",
                         "suppliers", "spending", "cost", "costs", "receivables", "payments", "unpaid",
                         "overdue", "earned", "earn", "money", "break", "records", "documents", "document",
                         "receivable", "payable", "payables", "cash", "flow", "working", "capital", "margin",
                         "margins", "category", "categories", "refund", "credit"}
        if intent not in {"greeting", "small_talk"} and not message_words.intersection(finance_words):
            intent, confidence = "unknown", 0.0
        if re.match(r"^(what (is|are)|define|explain)\b", user_message.strip(), re.IGNORECASE) and not message_words.intersection({"my", "our", "this", "that", "uploaded", "document", "invoice", "invoices", "bills"}):
            intent, confidence = "unknown", 0.0
        document_terms = {"doc", "docs", "document", "documents", "file", "files", "pdf", "statement", "receipt", "attachment", "paper"}
        document_actions = {"explain", "summarize", "summary", "read", "show", "tell", "details", "detail", "contain", "contents", "say", "written"}
        invoice_bill_terms = {"invoice", "invoices", "bill", "bills"}
        document_descriptions = {"explain", "about", "describe", "details", "detail", "say", "read", "summarize", "summary"}
        generic_finance_concept = answer_finance_learning(user_message) if attach_document_id is None else None
        if (generic_finance_concept and generic_finance_concept.intent == "finance_education"
                and not message_words.intersection({"my", "our", "this", "that", "the", "uploaded", "attached", "latest"})
                and intent == "document_question"):
            intent, confidence = "unknown", 0.0
        specific_invoice_reference = any(any(character.isdigit() for character in word) for word in message_words)
        asks_for_invoice_document = bool(message_words.intersection(invoice_bill_terms)) and (
            (bool(message_words.intersection(document_descriptions))
             and bool(message_words.intersection({"my", "our", "this", "that", "the", "uploaded", "attached", "latest"})))
            or specific_invoice_reference
            or (bool(message_words.intersection({"all", "list"})) and bool(message_words.intersection({"show", "list"})))
        )
        asks_about_document = intent == "document_question" or (
            bool(message_words.intersection(document_terms)) and bool(message_words.intersection(document_actions))
        ) or (document_reference and bool(message_words.intersection(document_actions))) or (
            previous_intent == "document_question" and document_reference
        ) or asks_for_invoice_document
        partial_workspace_query = bool(re.search(r"\bpartial(?:ly)?(?:\s+paid|\s+payment)?\b", user_message.lower())) and bool(
            message_words.intersection({"my", "our", "which", "show", "list"})
        ) and bool(message_words.intersection({"invoice", "invoices", "bill", "bills", "receivables", "payables"}))
        balance_workspace_query = bool(re.search(r"\b(?:remaining|balance|still owe|left to pay|left to collect)\b", user_message.lower())) and bool(
            message_words.intersection({"my", "our", "which", "show", "how"})
        ) and bool(message_words.intersection({"invoice", "invoices", "bill", "bills", "receivables", "payables"}))
        if (partial_workspace_query or balance_workspace_query) and not document_reference:
            asks_about_document = False
        effective_question = user_message
        clarification_reply = None
        clarification_metadata = None
        clarification = previous_metadata.get("document_clarification") or {}
        is_date_choice = clarification.get("type") == "upload_date" and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", user_message.strip()))
        asks_about_document = asks_about_document or is_date_choice

        if is_date_choice:
            effective_question = clarification.get("original_question") or user_message
            dated_documents = self._workspace_documents_for_date(user_message.strip())
            if len(dated_documents) > 1:
                clarification_reply, clarification_metadata = self._document_choice_prompt(
                    dated_documents, effective_question
                )
            elif len(dated_documents) == 1:
                attach_document_id = dated_documents[0].id
        elif asks_about_document and document_reference and not previous_documents and not any(
            any(character.isdigit() for character in word) for word in message_words
        ) and not message_words.intersection({"latest", "recent", "newest", "last"}):
            simple_reference = message_words.intersection({"this", "that", "it", "doc", "document", "file", "attachment"})
            if simple_reference:
                dated_documents = self._workspace_documents()
                upload_dates = sorted({document.created_at.date().isoformat() for document in dated_documents if document.created_at}, reverse=True)
                if len(upload_dates) > 1:
                    date_options = [
                        {"label": datetime.strptime(upload_date, "%Y-%m-%d").strftime("%b %d, %Y"), "value": upload_date}
                        for upload_date in upload_dates[:8]
                    ]
                    clarification_reply = "I can explain it. When did you upload the document? Choose a date below and I’ll find the file(s) from that day."
                    clarification_metadata = self._clarification_payload(
                        intent="document_question",
                        original_question=user_message,
                        prompt="When did you upload it?",
                        choices=date_options,
                        clarification_type="upload_date",
                    )
                elif dated_documents:
                    latest_date = dated_documents[0].created_at.date().isoformat()
                    same_day_documents = [document for document in dated_documents if document.created_at.date().isoformat() == latest_date]
                    if len(same_day_documents) > 1:
                        clarification_reply, clarification_metadata = self._document_choice_prompt(
                            same_day_documents, user_message
                        )
                    else:
                        attach_document_id = same_day_documents[0].id
        if not attach_document_id and asks_about_document and document_reference and previous_intent == "document_question" and previous_documents:
            attach_document_id = previous_documents[0].get("id")

        context = self._build_context(
            attach_document_id,
            document_query=effective_question if asks_about_document and clarification_reply is None else None,
        )
        advanced_answer = (
            answer_advanced_finance_workspace(self.db, self.user.id, user_message, context["currency"])
            if attach_document_id is None and not message_words.intersection({"document", "documents", "file", "files", "pdf", "attachment"})
            else None
        )
        if advanced_answer is not None:
            asks_about_document = False
            clarification_reply = None
        learning_answer = advanced_answer
        if learning_answer is None and partial_workspace_query and not asks_about_document:
            learning_answer = self._partial_payment_status(user_message, context["currency"])
        if learning_answer is None and balance_workspace_query and not asks_about_document:
            learning_answer = self._open_balance_status(user_message, context["currency"])
        if learning_answer is None and attach_document_id is None and not asks_about_document and clarification_reply is None:
            people_answer = answer_finance_people_costs(user_message, context["business_profile"])
            explicit_people_inputs = bool(re.search(
                r"\b(?:base salary|monthly salary|salary|hourly rate|bonus amount|unpaid leave days?|taxable pay|approved claim amount)\b\s*(?:is|=|:)?\s*(?:(?:USD|INR|EUR|GBP)\s*)?[$₹€£]?\s*\d",
                user_message, re.I,
            ))
            learning_answer = (
                self._cash_receipts_scenario(user_message, context["overview"], context["currency"])
                or (people_answer if people_answer and (people_answer.intent == "finance_people_calculation" or explicit_people_inputs) else None)
                or answer_finance_payroll_workspace(self.db, context["workspace_id"], user_message)
                or people_answer
                or answer_business_type_finance(user_message, context["business_profile"])
                or generic_finance_concept
                or answer_finance_workspace_limit(user_message)
            )
        follow_up = message_words.intersection({"it", "that", "those", "them"}) and len(message_words) <= 6
        if asks_about_document:
            intent, confidence = "document_question", max(confidence, 0.92)
        elif intent == "unknown" and follow_up and previous_intent not in {None, "greeting", "small_talk", "unknown", "general"}:
            intent = previous_intent
            confidence = max(confidence, 0.5)
        if learning_answer is not None:
            intent = learning_answer.intent
            reply, message_type = learning_answer.reply, "text"
            metadata = {
                "domain": domain,
                "intent": intent,
                "engine": "local_finance_tools" if intent in {"finance_scenario", "finance_aging", "finance_due_horizon", "finance_concentration", "finance_payment_status", "finance_payroll_records"} else "finance_calculator" if intent in {"finance_calculation", "finance_business_type_calculation", "finance_people_calculation"} else "finance_reference",
                "supporting_figures": list(learning_answer.figures),
                "supporting_documents": [],
                "assumptions": list(learning_answer.assumptions),
                "sources": list(learning_answer.sources),
                "needs_more_info": learning_answer.needs_more_info,
            }
            local_model_status = "not_needed"
            metadata["local_model_status"] = local_model_status
        elif clarification_reply is not None:
            reply, message_type, metadata = clarification_reply, "question", clarification_metadata
            local_model_status = "not_used_for_clarification"
            metadata["engine"] = "finance_clarification_rules"
            metadata["local_model_status"] = local_model_status
        else:
            local_model_status = "not_used"
            general_question = intent in {"unknown", "greeting", "small_talk"}
            if general_question:
                live_answer = answer_live_question(user_message)
                if live_answer:
                    reply, local_model_status = live_answer.reply, "not_needed"
                else:
                    reply, local_model_status = generate_general_model_reply(domain, user_message, conversation_history)
                    if not reply:
                        raise LocalModelUnavailable(unavailable_message(local_model_status))
                message_type = "text"
                metadata = {"domain": domain, "engine": live_answer.engine if live_answer else "ollama_local_general_model", "intent": "general"}
                if live_answer:
                    metadata["sources"] = live_answer.sources
            else:
                reply, message_type, metadata = self._answer(intent, confidence, effective_question, context)
            # These intents already have trusted, complete answers from finance
            # calculations. Avoid waiting on Ollama for simple factual queries.
            use_language_model = intent in {"profit_guidance", "document_question"}
            if use_language_model:
                model_reply, local_model_status = generate_local_model_reply(
                    question=effective_question,
                    context=context,
                    verified_application_answer=reply,
                    conversation_history=conversation_history,
                )
                if model_reply:
                    reply = model_reply
                    metadata["engine"] = "ollama_local_language_model"
                    metadata["model"] = "local"
                else:
                    metadata["engine"] = "local_finance_tools"
            elif not general_question:
                local_model_status = "not_used_for_deterministic_answer"
                metadata["engine"] = "local_finance_tools"
            metadata["local_model_status"] = local_model_status

        suggested_questions = (
            [] if metadata.get("needs_more_info") or metadata.get("engine") == "ollama_local_general_model" else self._suggested_questions(intent, context)
        )
        metadata["suggested_questions"] = suggested_questions
        metadata["suggestion_version"] = 2
        metadata["follow_up_questions"] = metadata.get("follow_up_questions", [])
        assistant_msg = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            message_type=message_type,
            message_metadata=metadata,
        )
        self.db.add(assistant_msg)
        conversation.updated_at = datetime.now(timezone.utc)
        if not prior_count:
            conversation.title = user_message.strip()[:190] or "Finance conversation"
        self.db.commit()
        self.db.refresh(assistant_msg)
        return {
            "conversation_id": conversation.id,
            "message_id": assistant_msg.id,
            "reply": reply,
            "message_type": message_type,
            "metadata": metadata,
            "suggested_questions": suggested_questions,
            "needs_more_info": metadata.get("needs_more_info", False),
            "follow_up_questions": metadata.get("follow_up_questions", []),
        }

    def _generate_general_domain_reply(
        self, conversation_id: str | None, user_message: str, domain: str,
        live_answer: LiveAnswer | None = None,
    ) -> dict[str, Any]:
        conversation = self._get_or_create_conversation(conversation_id, domain)
        prior_count = self.db.scalar(
            select(ChatMessage.id).where(ChatMessage.conversation_id == conversation.id).limit(1)
        )
        history_rows = list(self.db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(10)
        ).all())
        history = [
            {"role": item.role, "content": item.content[:3000]}
            for item in reversed(history_rows)
            if item.role in {"user", "assistant"}
        ]
        user_msg = ChatMessage(conversation_id=conversation.id, role="user", content=user_message)
        self.db.add(user_msg)
        live_answer = live_answer or answer_live_question(user_message)
        if live_answer:
            reply, model_status = live_answer.reply, "not_needed"
        else:
            reply, model_status = generate_general_model_reply(domain, user_message, history)
            if not reply:
                raise LocalModelUnavailable(unavailable_message(model_status))
        metadata = {"domain": domain, "engine": live_answer.engine if live_answer else "ollama_local_general_model", "local_model_status": model_status}
        if live_answer:
            metadata["sources"] = live_answer.sources
        assistant_msg = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            message_metadata=metadata,
        )
        self.db.add(assistant_msg)
        conversation.updated_at = datetime.now(timezone.utc)
        if not prior_count:
            conversation.title = user_message.strip()[:190] or "New conversation"
        self.db.commit()
        self.db.refresh(assistant_msg)
        return {
            "conversation_id": conversation.id,
            "message_id": assistant_msg.id,
            "reply": reply,
            "message_type": "text",
            "metadata": metadata,
            "suggested_questions": [],
            "needs_more_info": False,
            "follow_up_questions": [],
        }

    def _get_or_create_conversation(self, conversation_id: str | None, domain: str) -> ChatConversation:
        if conversation_id:
            conversation = self.db.scalar(
                select(ChatConversation).where(
                    ChatConversation.id == conversation_id,
                    ChatConversation.user_id == self.user.id,
                )
            )
            if not conversation:
                raise LookupError("Conversation not found.")
            if conversation.domain != domain:
                raise LookupError("This conversation uses a different AI focus. Start a new conversation to switch focus.")
            return conversation
        conversation = ChatConversation(user_id=self.user.id, title="New conversation", domain=domain)
        self.db.add(conversation)
        self.db.flush()
        return conversation

    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(ChatConversation)
            .where(ChatConversation.user_id == self.user.id)
            .order_by(ChatConversation.updated_at.desc())
        ).all()
        return [{"id": row.id, "title": row.title, "updated_at": row.updated_at.isoformat()} for row in rows]

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.conversation.has(user_id=self.user.id),
            )
            .order_by(ChatMessage.created_at.asc())
        ).all()
        return [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "message_type": row.message_type,
                "metadata": row.message_metadata,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    def delete_conversation(self, conversation_id: str) -> bool:
        conversation = self.db.scalar(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == self.user.id,
            )
        )
        if conversation is None:
            return False
        self.db.delete(conversation)
        self.db.commit()
        return True

    def _workspace_documents(self) -> list[FinanceDocument]:
        workspace = self.db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == self.user.id))
        if not workspace:
            return []
        return list(self.db.scalars(
            select(FinanceDocument)
            .where(FinanceDocument.workspace_id == workspace.id)
            .order_by(FinanceDocument.created_at.desc())
        ).all())

    def _workspace_documents_for_date(self, upload_date: str) -> list[FinanceDocument]:
        return [
            document for document in self._workspace_documents()
            if document.created_at and document.created_at.date().isoformat() == upload_date
        ]

    @staticmethod
    def _clarification_payload(
        intent: str,
        original_question: str,
        prompt: str,
        choices: list[dict[str, str]],
        clarification_type: str,
    ) -> dict[str, Any]:
        return {
            "intent": intent,
            "intent_confidence": 1.0,
            "engine": "local_multinomial_naive_bayes",
            "supporting_figures": [],
            "supporting_documents": [],
            "assumptions": [],
            "scenarios": [],
            "next_steps": [],
            "follow_up_questions": [{
                "id": f"document-{clarification_type}",
                "question": prompt,
                "choices": choices,
                "original_question": original_question,
            }],
            "needs_more_info": True,
            "document_clarification": {
                "type": clarification_type,
                "original_question": original_question,
            },
        }

    def _document_choice_prompt(
        self, documents: list[FinanceDocument], original_question: str
    ) -> tuple[str, dict[str, Any]]:
        choices = [
            {
                "label": document.original_name,
                "document_id": document.id,
                "uploaded_on": document.created_at.date().isoformat(),
            }
            for document in documents
        ]
        reply = f"I found {len(choices)} finance documents uploaded that day. Which one should I use?"
        payload = self._clarification_payload(
            intent="document_question",
            original_question=original_question,
            prompt="Choose the document to continue:",
            choices=choices,
            clarification_type="document_choice",
        )
        return reply, payload

    def _build_context(self, attach_document_id: str | None, document_query: str | None = None) -> dict[str, Any]:
        """Build a user-scoped context and retrieve relevant finance documents automatically."""
        workspace = self.db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == self.user.id))
        profile = self.db.scalar(select(BusinessProfile).where(BusinessProfile.user_id == self.user.id))
        overview = FinanceIntelligence(self.db, self.user).compute_overview()
        documents: list[dict[str, Any]] = []

        if attach_document_id:
            candidates = list(self.db.scalars(
                select(FinanceDocument)
                .join(FinanceWorkspace, FinanceDocument.workspace_id == FinanceWorkspace.id)
                .where(
                    FinanceDocument.id == attach_document_id,
                    FinanceWorkspace.owner_id == self.user.id,
                )
            ).all())
            if not candidates:
                raise LookupError("Document not found.")
        elif document_query and workspace:
            candidates = list(self.db.scalars(
                select(FinanceDocument)
                .where(FinanceDocument.workspace_id == workspace.id)
                .order_by(FinanceDocument.created_at.desc())
                .limit(30)
            ).all())
        else:
            candidates = []

        if candidates:
            candidate_ids = [document.id for document in candidates]
            record_rows = self.db.scalars(
                select(FinanceRecord)
                .where(
                    FinanceRecord.workspace_id == workspace.id,
                    FinanceRecord.document_id.in_(candidate_ids),
                )
                .order_by(FinanceRecord.created_at.asc())
            ).all()
            records_by_document: dict[str, list[FinanceRecord]] = {}
            for record in record_rows:
                records_by_document.setdefault(record.document_id, []).append(record)

            indexed_documents = []
            query_tokens = set(tokenize(document_query or "")) - {
                "the", "a", "an", "is", "are", "what", "about", "please", "can", "you", "me", "my",
                "this", "that", "these", "those", "document", "documents", "file", "files", "pdf",
                "explain", "summarize", "summary", "tell", "show", "read", "details", "detail", "in", "of",
            }
            for document in candidates:
                record_context = [
                    {
                        "record_type": record.record_type,
                        "status": record.status,
                        "counterparty": record.counterparty,
                        "reference_number": record.reference_number,
                        "issue_date": record.issue_date.isoformat() if record.issue_date else None,
                        "due_date": record.due_date.isoformat() if record.due_date else None,
                        "currency": record.currency,
                        "subtotal": float(record.subtotal) if record.subtotal is not None else None,
                        "tax_total": float(record.tax_total) if record.tax_total is not None else None,
                        "total_amount": float(record.total_amount) if record.total_amount is not None else None,
                        "amount_paid": float(record.amount_paid or 0),
                        "balance_due": float(record.balance_due) if record.balance_due is not None else None,
                        "payment_status": record.payment_status,
                        "review_notes": record.review_notes or [],
                    }
                    for record in records_by_document.get(document.id, [])
                ]
                text = self._read_document_text(document)
                search_index = " ".join([
                    document.original_name,
                    text,
                    *(str(value) for record in record_context for value in record.values() if value is not None),
                ]).lower()
                score = sum(1 for token in query_tokens if token in search_index)
                indexed_documents.append((score, document.created_at, {
                    "id": document.id,
                    "original_name": document.original_name,
                    "status": document.status,
                    "extension": document.extension,
                    "text": text,
                    "records": record_context,
                }))

            if attach_document_id:
                documents = [item[2] for item in indexed_documents]
            elif indexed_documents:
                words = set(tokenize(document_query or ""))
                wants_multiple = bool(words.intersection({"all", "these", "both", "list", "compare", "documents", "invoices", "bills"}))
                ranked = sorted(indexed_documents, key=lambda item: (item[0], item[1]), reverse=True)
                if ranked[0][0] == 0:
                    # A vague reference such as "explain this" means the most recently uploaded file.
                    documents = [ranked[0][2]]
                elif wants_multiple:
                    documents = [item[2] for item in ranked if item[0] > 0][:5]
                else:
                    documents = [ranked[0][2]]

        return {
            "workspace_id": workspace.id if workspace else None,
            "currency": (profile.currency if profile else None) or (workspace.currency if workspace else None) or overview.get("currency") or "",
            "business_name": workspace.business_name if workspace else None,
            "business_profile": {
                "business_type": (workspace.business_type if workspace and workspace.business_type else None) or (profile.business_type if profile else None),
                "industry": profile.industry if profile else (workspace.industry if workspace else None),
                "country": profile.country if profile else None,
                "revenue_model": profile.revenue_model if profile else None,
                "what_they_sell": profile.what_they_sell if profile else None,
                "main_goal": profile.main_goal if profile else None,
                "biggest_challenge": profile.biggest_challenge if profile else None,
            },
            "overview": overview,
            "documents": documents,
        }

    @staticmethod
    def _read_document_text(document: FinanceDocument) -> str:
        """Read bounded selectable PDF text from a file already scoped to its owner."""
        if document.extension.lower() != ".pdf":
            return ""
        path = (UPLOAD_DIR / document.storage_name).resolve()
        if UPLOAD_DIR not in path.parents or not path.is_file():
            return ""
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                return ""
            return "\n".join((page.extract_text() or "") for page in reader.pages[:20])[:12000]
        except Exception:
            return ""

    @staticmethod
    def _money(value: Any, currency: str) -> str:
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            amount = 0
        return f"{currency} {amount:,.2f}".strip()

    @staticmethod
    def _money_by_currency(items: list[dict[str, Any]], default_currency: str) -> str:
        totals: dict[str, Decimal] = {}
        for item in items:
            code = str(item.get("currency") or default_currency).upper()
            try:
                amount = Decimal(str(item.get("amount") or 0))
            except InvalidOperation:
                continue
            totals[code] = totals.get(code, Decimal(0)) + amount
        return ", ".join(f"{code} {amount:,.2f}" for code, amount in sorted(totals.items())) or f"{default_currency} 0.00"

    def _cash_receipts_scenario(
        self, question: str, overview: dict[str, Any], currency: str
    ) -> FinanceLearningAnswer | None:
        lower = question.lower()
        if not (re.search(r"\b(?:what if|scenario|if)\b", lower)
                and re.search(r"\binvoices?\b", lower)
                and re.search(r"\bpaid\b", lower)
                and re.search(r"\bnext month\b", lower)):
            return None
        if not re.search(r"\b(?:all|every)\b", lower):
            return FinanceLearningAnswer(
                "Which confirmed invoices should I include? Give their references, or ask 'What if all unpaid invoices are paid next month?' "
                "I need a defined set before calculating possible receipts.",
                intent="finance_scenario", needs_more_info=True,
            )
        overdue = overview.get("overdue_invoices") or []
        outstanding = overview.get("outstanding_invoices") or []
        invoices = overdue if "overdue" in lower else overdue + outstanding
        if not invoices:
            return FinanceLearningAnswer(
                "I found no confirmed unpaid invoices in the selected group to use for this scenario.",
                intent="finance_scenario", needs_more_info=True,
            )
        totals = self._money_by_currency(invoices, currency)
        references = ", ".join(str(item.get("reference") or item.get("id")) for item in invoices[:5])
        if len(invoices) > 5:
            references += f", and {len(invoices) - 5} more"
        return FinanceLearningAnswer(
            f"If all {len(invoices)} selected confirmed invoices are paid in full next month, potential customer receipts would be {totals}. "
            f"Records used: {references}. This is a conditional cash-in scenario, not a forecast of bank balance or additional revenue. "
            "This uses remaining balances after recorded partial payments. Disputes and payment timing are not tracked, so verify those before relying on it.",
            intent="finance_scenario",
            figures=tuple({
                "label": str(item.get("reference") or item.get("id")),
                "value": self._money(item.get("amount"), item.get("currency") or currency),
                "source": f"confirmed invoice record {item.get('id')}",
            } for item in invoices[:20]),
            assumptions=("All selected invoices are paid in full next month; no fees, refunds, or disputes are deducted.",),
        )

    def _partial_payment_status(self, question: str, currency: str) -> FinanceLearningAnswer:
        lower = question.lower()
        record_type = "expense" if re.search(r"\b(?:bills?|payables?)\b", lower) else "invoice"
        label = "supplier bills" if record_type == "expense" else "customer invoices"
        records = list(self.db.scalars(
            select(FinanceRecord)
            .join(FinanceWorkspace, FinanceRecord.workspace_id == FinanceWorkspace.id)
            .where(
                FinanceWorkspace.owner_id == self.user.id,
                FinanceRecord.status == "confirmed",
                FinanceRecord.record_type == record_type,
                FinanceRecord.payment_status == "partial",
            )
            .order_by(FinanceRecord.due_date.asc())
        ).all())
        if not records:
            return FinanceLearningAnswer(f"I found no confirmed partially paid {label} in your workspace.", intent="finance_payment_status")
        items = [{"amount": float(record.balance_due or 0), "currency": record.currency or currency} for record in records]
        figures = tuple({
            "label": record.reference_number or record.counterparty or record.id,
            "value": self._money(record.balance_due, record.currency or currency),
            "source": f"confirmed {record_type} record {record.id}; total {self._money(record.total_amount, record.currency or currency)}, recorded paid {self._money(record.amount_paid, record.currency or currency)}",
        } for record in records[:20])
        return FinanceLearningAnswer(
            f"I found {len(records)} partially paid {label}. Their remaining balance by currency is {self._money_by_currency(items, currency)}. "
            "See the figures below for each record. These are user-recorded cumulative payments, not bank-reconciled cash movements.",
            intent="finance_payment_status", figures=figures,
            assumptions=("Payments are cumulative amounts entered in the finance workspace; disputes are not tracked.",),
        )

    def _open_balance_status(self, question: str, currency: str) -> FinanceLearningAnswer:
        record_type = "expense" if re.search(r"\b(?:bills?|payables?)\b", question.lower()) else "invoice"
        label = "supplier bills" if record_type == "expense" else "customer invoices"
        records = list(self.db.scalars(
            select(FinanceRecord)
            .join(FinanceWorkspace, FinanceRecord.workspace_id == FinanceWorkspace.id)
            .where(
                FinanceWorkspace.owner_id == self.user.id,
                FinanceRecord.status == "confirmed",
                FinanceRecord.record_type == record_type,
            )
            .order_by(FinanceRecord.due_date.asc())
        ).all())
        open_records = [record for record in records if record.balance_due is not None and record.balance_due > 0]
        if not open_records:
            return FinanceLearningAnswer(f"I found no confirmed {label} with a recorded remaining balance.", intent="finance_payment_status")
        items = [{"amount": float(record.balance_due or 0), "currency": record.currency or currency} for record in open_records]
        figures = tuple({
            "label": record.reference_number or record.counterparty or record.id,
            "value": self._money(record.balance_due, record.currency or currency),
            "source": f"confirmed {record_type} record {record.id}; total {self._money(record.total_amount, record.currency or currency)}, recorded paid {self._money(record.amount_paid, record.currency or currency)}",
        } for record in open_records[:20])
        return FinanceLearningAnswer(
            f"Across {len(open_records)} confirmed {label}, the remaining balance is {self._money_by_currency(items, currency)}. "
            "This subtracts user-recorded cumulative payments from each record's total; it is not bank-reconciled cash.",
            intent="finance_payment_status", figures=figures,
            assumptions=("Records without a confirmed total are excluded. Disputes are not tracked.",),
        )

    def _answer(
        self, intent: str, confidence: float, user_message: str, context: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        overview = context["overview"]
        currency = context["currency"]
        basis = overview.get("basis_note") or "Only explicitly confirmed finance records are included."
        figures: list[dict[str, Any]] = []
        assumptions = [basis]
        follow_up: list[dict[str, Any]] = []
        needs_more = False
        response_type = "fact_lookup"
        documents = context.get("documents") or []

        if intent == "document_question" and not documents:
            response_type = "business_guidance"
            needs_more = True
            reply = "I couldn't find a finance document in your workspace that matches this question. Upload it to the Finance Analyst workspace and ask again. I can currently read selectable PDF text and extracted finance fields; scanned image-only PDFs still need OCR."
            follow_up = []
        elif intent == "document_question" and documents:
            response_type = "fact_lookup"
            reply, figures, assumptions = self._explain_documents(documents, currency, user_message)
            if not any(document.get("text") or document.get("records") for document in documents):
                needs_more = True
        elif intent == "greeting":
            response_type = "business_guidance"
            reply = (
                f"Hi {self.user.name or 'there'}! I am your Finance Analyst AI. I can summarize confirmed income and expenses, "
                "check invoices and bills, review spending trends, or help you think through profit improvements. What would you like to look at?"
            )
        elif intent == "small_talk":
            response_type = "business_guidance"
            reply = "You're welcome. What finance question should we look at next? I can summarize your records, check overdue invoices or bills, review expenses, or help plan ways to improve profit."
        elif intent == "financial_summary":
            income, expenses, net = (overview.get("total_income", 0), overview.get("total_expenses", 0), overview.get("net_profit", 0))
            query_words = set(tokenize(user_message))
            target_month = date.today().strftime("%Y-%m")
            period_label = "all time"
            if "month" in query_words and query_words.intersection({"this", "current"}):
                period_label = "this month"
            elif "month" in query_words and query_words.intersection({"last", "previous"}):
                current = date.today()
                target_month = date(current.year - (current.month == 1), current.month - 1 if current.month > 1 else 12, 1).strftime("%Y-%m")
                period_label = "last month"
            if period_label != "all time":
                monthly = next((row for row in overview.get("monthly_revenue_and_costs", []) if row.get("month") == target_month), None)
                income = monthly.get("revenue", 0) if monthly else 0
                expenses = monthly.get("costs", 0) if monthly else 0
                net = income - expenses
            reply = (
                f"For {period_label}, confirmed records show {self._money(income, currency)} in issued income, "
                f"{self._money(expenses, currency)} in issued expenses, and a difference of {self._money(net, currency)}. "
                "These are document issue-date totals, not cash received or verified business profit. "
                "Standalone payroll records are excluded unless also entered as confirmed expenses."
            )
            completeness = overview.get("data_completeness_note") or ""
            if "other currencies" in completeness:
                reply += f" {completeness}"
            figures = [
                {"label": f"{label} ({period_label})", "value": self._money(value, currency), "source": "confirmed finance records by issue date"}
                for label, value in (("Recorded income", income), ("Recorded expenses", expenses), ("Income minus expenses", net))
            ]
            if not overview.get("total_records"):
                needs_more = True
                reply = "I donâ€™t have confirmed finance records to calculate this yet. Upload your invoices and bills, review the extracted details, and confirm their classifications; then ask me again."
        elif intent == "invoice_status":
            outstanding = overview.get("outstanding_invoices", [])
            overdue = overview.get("overdue_invoices", [])
            total_overdue = self._money_by_currency(overdue, currency)
            total_open = self._money_by_currency(outstanding + overdue, currency)
            partial_count = sum(item.get("payment_status") == "partial" for item in outstanding + overdue)
            reply = f"There are {len(outstanding)} confirmed customer invoice(s) not overdue or undated and {len(overdue)} overdue. The total remaining balance by currency is {total_open}; overdue balance is {total_overdue}. {partial_count} of these invoice(s) are partially paid."
            figures = [
                {"label": "Outstanding invoices", "value": len(outstanding), "source": "confirmed records"},
                {"label": "Overdue invoices", "value": len(overdue), "source": "confirmed records"},
                {"label": "Total remaining balance by currency", "value": total_open, "source": "confirmed records after recorded payments"},
                {"label": "Overdue remaining balance by currency", "value": total_overdue, "source": "confirmed records after recorded payments"},
            ]
            if overdue:
                reply += " Overdue items: " + "; ".join(
                    f"{item.get('name', 'Unknown')} ({item.get('reference') or 'no reference'}, {self._money(item.get('amount'), item.get('currency') or currency)})"
                    for item in overdue[:5]
                ) + "."
            else:
                reply += " I found no confirmed overdue customer invoices."
        elif intent == "bills_due":
            bills = overview.get("bills_due_soon", [])
            overdue_bills = overview.get("overdue_bills", [])
            due_total = self._money_by_currency(bills, currency)
            overdue_total = self._money_by_currency(overdue_bills, currency)
            reply = (
                f"I found {len(bills)} confirmed supplier bill(s) due within 7 days (remaining balance {due_total}) "
                f"and {len(overdue_bills)} overdue bill(s) (remaining balance {overdue_total})."
            )
            figures = [
                {"label": "Bills due within 7 days", "value": len(bills), "source": "confirmed records"},
                {"label": "Overdue bills", "value": len(overdue_bills), "source": "confirmed records"},
            ]
            items = (overdue_bills + bills)[:5]
            if items:
                reply += " Items: " + "; ".join(
                    f"{item.get('name', 'Unknown')} due {item.get('due_date') or 'date unavailable'} ({self._money(item.get('amount'), item.get('currency') or currency)})"
                    for item in items
                ) + "."
            else:
                reply += " I found no confirmed bills in those due-date groups."
        elif intent == "expense_trend":
            months = overview.get("monthly_revenue_and_costs", [])
            if not months:
                reply = "There are not enough confirmed, dated records to show an expense trend yet. Add and confirm records with issue dates first."
                needs_more = True
            else:
                reply = "Confirmed expenses by issue month: " + "; ".join(
                    f"{row['month']}: {self._money(row['costs'], currency)}" for row in months
                ) + ". These are document issue-date totals, not payment dates."
                completeness = overview.get("data_completeness_note") or ""
                if "other currencies" in completeness:
                    reply += f" {completeness}"
                figures = [{"label": f"Expenses {row['month']}", "value": self._money(row["costs"], currency), "source": "confirmed record issue dates"} for row in months]
                if len(months) >= 2:
                    previous, current = months[-2:]
                    previous_date = date.fromisoformat(f"{previous['month']}-01")
                    current_date = date.fromisoformat(f"{current['month']}-01")
                    consecutive = (
                        current_date.year * 12 + current_date.month
                        == previous_date.year * 12 + previous_date.month + 1
                    )
                    if consecutive:
                        change = Decimal(str(current["costs"])) - Decimal(str(previous["costs"]))
                        prior = Decimal(str(previous["costs"]))
                        percentage = f" ({change / prior * 100:+.2f}%)" if prior else " (percentage undefined because the previous month was zero)"
                        reply += f" Latest month-over-month change: {self._money(change, currency)}{percentage}. Check one-time bills, missing records, and category changes before treating a jump as unusual."
                        figures.append({"label": f"Expense change {current['month']} vs {previous['month']}", "value": self._money(change, currency), "source": "confirmed record issue dates"})
                    else:
                        reply += " The latest recorded months are not consecutive, so I cannot label their difference month-over-month."
        elif intent == "profit_guidance":
            response_type = "business_guidance"
            profile = context["business_profile"]
            income = float(overview.get("total_income") or 0)
            expenses = float(overview.get("total_expenses") or 0)
            overdue = overview.get("overdue_invoices", [])
            overdue_amount = self._money_by_currency(overdue, currency)
            monthly = overview.get("monthly_revenue_and_costs", [])
            reply = "Here is a starting point based on records I can verify. "
            if overview.get("total_records"):
                reply += f"Confirmed issued records show {self._money(income, currency)} income and {self._money(expenses, currency)} expenses (difference: {self._money(income - expenses, currency)}). "
                if overdue:
                    reply += f"You have {len(overdue)} overdue customer invoice(s) totaling {overdue_amount} by currency; following up on these is a concrete collection action. "
                if monthly:
                    reply += f"The latest issue-month expense total is {self._money(monthly[-1].get('costs'), currency)}. Review its largest supplier costs for avoidable or negotiable spending. "
            else:
                reply += "There are no confirmed finance records yet, so I can't identify which lever matters most. "
            business_type = str(profile.get("business_type") or "").strip().lower()
            if business_type == "retail":
                reply += "For retail, compare product selling prices with verified cost of goods sold, then review POS settlements, returns, and stock movement when those records are available. I need SKU sales, inventory costs, and operating costs before estimating product margins. Which area should we investigate first: pricing and margins, stock and returns, costs, or overdue collections?"
            elif business_type == "corporate":
                reply += "For a corporate review, compare collections and supplier obligations first. Department budgets, contracts, approvals, and entity ledgers are needed before estimating group profit or a budget variance. Which area should we investigate first: collections, supplier costs, budgets, or contracts?"
            else:
                reply += "Compare selling prices with per-unit purchase and operating costs, then focus on the products with the strongest verified margin. I need unit prices, quantities, and variable costs before estimating a profit increase. Which area should we investigate first: pricing and margins, costs, or overdue collections?"
            if profile.get("biggest_challenge"):
                reply += f" Your setup lists this challenge: {profile['biggest_challenge']}."
            areas = (["Pricing and margins", "Stock and returns", "Reduce costs", "Collect overdue invoices"] if business_type == "retail" else
                     ["Collect overdue invoices", "Supplier costs", "Budgets", "Contracts"] if business_type == "corporate" else
                     ["Pricing and margins", "Reduce operating costs", "Collect overdue invoices", "Something else"])
            follow_up = [{"id": "profit-area", "question": "Which area do you want to review first?", "suggested_answers": areas, "allow_custom": True}]
            needs_more = True
            assumptions.append("No estimated savings or profit increase is claimed without supporting business data.")
        elif intent == "break_even":
            response_type = "business_guidance"
            reply = "I can calculate break-even units when you provide fixed costs, sale price per unit, and variable cost per unit. Those inputs arenâ€™t available in your confirmed records yet. What are those three values?"
            follow_up = [{"id": "break-even-inputs", "question": "Share fixed costs, price per unit, and variable cost per unit.", "suggested_answers": [], "allow_custom": True}]
            needs_more = True
        elif intent == "record_review":
            records = overview.get("records_needing_attention", [])
            reply = f"There are {len(records)} finance record(s) waiting for your review. Open the Overviewâ€™s Needs your attention section to compare each extraction with its original document and classify it."
            figures = [{"label": "Records needing review", "value": len(records), "source": "finance workspace"}]
        else:
            response_type = "business_guidance"
            needs_more = True
            reply = (
                "I can answer questions about your confirmed income and expenses, overdue invoices, supplier bills, "
                "spending by month, and profit planning. I didnâ€™t confidently identify this request; could you rephrase it or choose one of those topics?"
            )
            follow_up = [{"id": "topic", "question": "What would you like to review?", "suggested_answers": ["Summarize income and expenses", "Check overdue invoices", "Check bills due", "Review expense trends", "Improve profit"], "allow_custom": True}]

        metadata = {
            "intent": intent,
            "intent_confidence": round(confidence, 3),
            "engine": "local_multinomial_naive_bayes",
            "supporting_figures": figures,
            "supporting_documents": [
                {"id": document["id"], "original_name": document["original_name"], "status": document["status"]}
                for document in documents
            ],
            "assumptions": assumptions,
            "scenarios": [],
            "next_steps": [],
            "follow_up_questions": follow_up,
            "needs_more_info": needs_more,
        }
        return reply, response_type, metadata

    def _explain_documents(
        self, documents: list[dict[str, Any]], currency: str, query: str
    ) -> tuple[str, list[dict[str, Any]], list[str]]:
        """Answer from retrieved user-owned files without exposing a raw PDF text dump."""
        figures: list[dict[str, Any]] = []
        assumptions = ["I searched this user's finance workspace and used matching saved records and locally readable PDF text."]
        parts = []
        query_tokens = set(tokenize(query)) - {
            "the", "a", "an", "is", "are", "what", "about", "please", "can", "you", "me", "my",
            "this", "that", "document", "documents", "file", "files", "pdf", "explain", "summarize", "summary",
            "tell", "show", "read", "details", "detail", "in", "of",
        }
        has_unconfirmed = False
        for document in documents:
            records = document.get("records") or []
            parts.append(f"File: {document['original_name']} ({document.get('status', 'unknown status')})")
            text_records = self._summarize_pdf_invoices(document.get("text") or "")
            if text_records:
                record_status = {str(record.get("reference_number") or "").lower(): record.get("status", "needs review") for record in records}
                parts.append(f"I found {len(text_records)} invoice/bill section(s) in its PDF text:")
                for record in text_records[:20]:
                    status = record_status.get(record["reference"].lower(), "not yet saved as a separate finance record")
                    fields = [f"reference {record['reference']}"]
                    if record.get("issue_date"):
                        fields.append(f"issued {record['issue_date']}")
                    if record.get("due_date"):
                        fields.append(f"due {record['due_date']}")
                    if record.get("amount") is not None:
                        amount = self._money(record["amount"], record.get("currency") or currency)
                        fields.append(f"total {amount}")
                        figures.append({"label": record["reference"], "value": amount, "source": document["original_name"]})
                    fields.append(status.replace("_", " "))
                    parts.append("- " + "; ".join(fields))
                if len(text_records) > len(records):
                    assumptions.append(f"{document['original_name']} has invoice sections that are not yet saved as separate finance records. Those PDF text values are unconfirmed suggestions.")
            elif records:
                parts.append(f"I found {len(records)} extracted finance record(s):")
                for record in records[:20]:
                    fields = []
                    if record.get("reference_number"):
                        fields.append(f"reference {record['reference_number']}")
                    if record.get("counterparty"):
                        fields.append(str(record["counterparty"]))
                    if record.get("record_type") and record["record_type"] != "unclassified":
                        fields.append("customer invoice" if record["record_type"] == "invoice" else "supplier bill/expense")
                    if record.get("total_amount") is not None:
                        amount = self._money(record["total_amount"], record.get("currency") or currency)
                        fields.append(f"total {amount}")
                        figures.append({"label": record.get("reference_number") or "Extracted total", "value": amount, "source": document["original_name"]})
                    if record.get("issue_date"):
                        fields.append(f"issued {record['issue_date']}")
                    if record.get("due_date"):
                        fields.append(f"due {record['due_date']}")
                    status = record.get("status", "unknown status")
                    fields.append(status.replace("_", " "))
                    parts.append("- " + "; ".join(fields))
                    has_unconfirmed |= status != "confirmed"
            else:
                highlights = self._document_highlights(document.get("text") or "", query_tokens)
                if highlights:
                    parts.append("Key document details:")
                    parts.extend(f"- {line}" for line in highlights)
                else:
                    parts.append("The file has no saved extracted finance records or readable matching text.")
        if has_unconfirmed or any("not yet saved" in line for line in parts):
            assumptions.append("Needs-review, unclassified, or text-derived values must be checked against the original file before relying on them.")
        if any(not document.get("text") and not document.get("records") for document in documents):
            assumptions.append("Scanned image-only PDFs cannot be read yet because OCR is not configured.")
        if len(documents) == 1:
            assumptions.append(f"Source file: {documents[0]['original_name']}.")
        else:
            assumptions.append(f"Retrieved {len(documents)} matching files from this finance workspace.")
        return "\n".join(parts), figures, assumptions

    @staticmethod
    def _summarize_pdf_invoices(text: str) -> list[dict[str, Any]]:
        """Extract compact invoice summaries from all invoice sections in local PDF text."""
        matches = list(re.finditer(
            r"\b(?:invoice|bill)\s*(?:number|no\.?|#|id)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{1,})",
            text,
            re.IGNORECASE,
        ))
        summaries = []
        for index, match in enumerate(matches[:50]):
            section = text[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            rupee = chr(0x20B9)
            amount_match = re.search(
                r"\b(?:grand total|invoice total|amount due|total due|total amount)\s*[:=-]?\s*(?:" + re.escape(rupee) + r"|INR)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
                section,
                re.IGNORECASE,
            )
            issue_match = re.search(r"\b(?:invoice date|bill date|date of invoice)\s*[:=-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})", section, re.IGNORECASE)
            due_match = re.search(r"\b(?:due date|payment due)\s*[:=-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})", section, re.IGNORECASE)
            amount = None
            if amount_match:
                try:
                    amount = float(amount_match.group(1).replace(",", ""))
                except ValueError:
                    pass
            summaries.append({
                "reference": match.group(1).strip(),
                "issue_date": " ".join(issue_match.group(1).split()) if issue_match else None,
                "due_date": " ".join(due_match.group(1).split()) if due_match else None,
                "amount": amount,
                "currency": "INR" if rupee in section or re.search(r"\bINR\b", section, re.IGNORECASE) else None,
            })
        return summaries

    @staticmethod
    def _document_highlights(text: str, query_tokens: set[str]) -> list[str]:
        financial_terms = ("invoice", "bill", "total", "amount", "date", "due", "supplier", "customer", "payment", "balance")
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        ranked = []
        for index, line in enumerate(lines):
            lowered = line.lower()
            tokens = set(tokenize(lowered))
            relevance = sum(token in tokens for token in query_tokens)
            financial_relevance = sum(term in lowered for term in financial_terms)
            if relevance or financial_relevance >= 2:
                ranked.append((relevance * 3 + financial_relevance, -index, line[:220]))
        ranked.sort(reverse=True)
        return list(dict.fromkeys(item[2] for item in ranked[:5]))

    @staticmethod
    def _suggested_questions(intent: str, context: dict[str, Any]) -> list[str]:
        """Offer a few relevant next questions for the answer just given."""
        overview = context.get("overview") or {}
        if intent == "greeting":
            return [
                "Summarize my confirmed income and expenses",
                "Which customer invoices are overdue?",
                "What supplier bills are due soon?",
            ]
        if intent == "financial_summary":
            questions = []
            if overview.get("overdue_invoices"):
                questions.append("Which customers have overdue invoices?")
            if overview.get("overdue_bills") or overview.get("bills_due_soon"):
                questions.append("Show me the bills I need to pay next")
            questions.append("Compare my income and expenses by month")
            return questions[:3]
        if intent == "invoice_status":
            return [
                "Show my outstanding customer invoices",
                "Which invoices are due in the next 7 days?",
            ]
        if intent == "bills_due":
            return [
                "Which supplier bills are overdue?",
                "Show bills due in the next 7 days",
            ]
        if intent == "expense_trend":
            return [
                "Which suppliers account for the most spending?",
                "Compare this month's bills with last month",
            ]
        if intent == "profit_guidance":
            return [
                "Build a practical 30-day improvement plan",
                "Which confirmed expenses should I review first?",
            ]
        if intent == "document_question":
            return [
                "List the important amounts and dates in this document",
                "Is there a payment due date in this document?",
            ]
        if intent == "record_review":
            return [
                "Which records still need my review?",
                "How do unconfirmed records affect my totals?",
            ]
        return []
