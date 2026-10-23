"""Small, locally trained Multinomial Naive Bayes model for finance chat intents.

The model classifies the user's request. Finance answers are then calculated by
trusted application code from that user's confirmed records; no external model
or service receives the message or finance data.
"""

from __future__ import annotations

import math
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

TRAINING_EXAMPLES: dict[str, tuple[str, ...]] = {
    "greeting": (
        "hello hi hey good morning good evening",
        "what can you help me with", "who are you", "good afternoon", "hey bro", "hi bro",
        "hello bro", "hey there", "what's up", "good to see you",
    ),
    "small_talk": (
        "thanks", "thank you", "thanks a lot", "okay", "ok got it", "great that helps",
        "you are helpful", "bye", "goodbye", "understood",
    ),
    "financial_summary": (
        "how much money did I make", "show my total income and expenses", "what is my net profit",
        "summarize my finances", "how much revenue do I have", "what are my total costs",
        "tell me my financial position", "income expense summary", "how much have I earned",
        "how much money did I get this month", "tell me what my business made",
        "show confirmed invoice and bill totals", "what is my issued income minus issued expenses",
        "summarize my confirmed sales invoices and supplier bills",
    ),
    "invoice_status": (
        "which customer invoices are overdue", "how much money do customers owe me",
        "show unpaid invoices", "list my outstanding receivables", "which invoices are late",
        "who has not paid their invoice", "how many overdue customer payments",
        "show all unpaid customer invoices", "list overdue accounts receivable",
        "what confirmed invoices remain unpaid",
    ),
    "bills_due": (
        "what supplier bills are due soon", "which bills do I need to pay this week",
        "show upcoming expenses due", "any overdue bills I owe", "list supplier payments due",
        "what do I owe suppliers", "which expenses are overdue",
        "show unpaid supplier bills", "list overdue accounts payable",
        "which supplier bills are due next week",
    ),
    "expense_trend": (
        "which costs increased the most", "show my monthly expenses trend", "where am I spending money",
        "compare my costs over time", "what are my biggest expenses", "which expenses are rising",
        "how have supplier costs changed", "analyze spending trends",
        "compare this month's expenses with last month", "show month over month expense changes",
        "did my issued expenses increase this month",
    ),
    "profit_guidance": (
        "how can I increase profit", "ways to improve my profit margin", "help me make more profit",
        "how do I reduce costs and grow revenue", "give me ideas to grow my business profitably",
        "what should I do to improve profitability", "help me earn more from my business",
        "profit plan", "make a plan to increase profit", "plan to make more profit",
    ),
    "break_even": (
        "how many units must I sell to break even", "calculate my break even point",
        "what sales volume covers my costs", "how much revenue do I need to cover expenses",
        "when will my business break even", "break even analysis",
    ),
    "record_review": (
        "what records need my attention", "which documents are waiting for review",
        "show unconfirmed invoices", "what finance records should I check", "list documents to review",
    ),
    "document_question": (
        "can you explain this document", "please explain that document to me",
        "i shared a document can you explain what is in it", "summarize this file for me",
        "what does this invoice say", "tell me about this attachment", "explain the uploaded pdf",
        "read this document and tell me the important details", "what is written in this file",
        "can you explain me about that document", "i shared one document with you explain it",
        "what are the details in this invoice", "show me the information from this bill",
        "please summarize the attached document", "explain this paper",
    ),
}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower())


class FinanceIntentModel:
    """Multinomial Naive Bayes trained on bundled, synthetic example phrases."""

    def __init__(self, examples: dict[str, tuple[str, ...]] = TRAINING_EXAMPLES):
        self.labels = sorted(examples)
        self.class_counts: Counter[str] = Counter()
        self.word_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.vocabulary: set[str] = set()
        for label, phrases in examples.items():
            for phrase in phrases:
                words = tokenize(phrase)
                self.class_counts[label] += 1
                self.word_counts[label].update(words)
                self.vocabulary.update(words)
        self.total_examples = sum(self.class_counts.values())

    def save(self, path: Path) -> None:
        payload = {
            "format": 1,
            "algorithm": "multinomial_naive_bayes",
            "classes": self.labels,
            "class_counts": dict(self.class_counts),
            "word_counts": {label: dict(counts) for label, counts in self.word_counts.items()},
            "vocabulary": sorted(self.vocabulary),
            "total_examples": self.total_examples,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "FinanceIntentModel":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("format") != 1 or payload.get("algorithm") != "multinomial_naive_bayes":
            raise ValueError("Unsupported finance intent model format")
        model = cls.__new__(cls)
        model.labels = payload["classes"]
        model.class_counts = Counter(payload["class_counts"])
        model.word_counts = defaultdict(Counter, {
            label: Counter(counts) for label, counts in payload["word_counts"].items()
        })
        model.vocabulary = set(payload["vocabulary"])
        model.total_examples = int(payload["total_examples"])
        return model

    def predict(self, text: str) -> tuple[str, float]:
        words = tokenize(text)
        if not words:
            return "unknown", 0.0
        if len(words) <= 4 and any(word in {"hi", "hello", "hey", "morning", "afternoon", "evening"} for word in words):
            return "greeting", 0.99
        if len(words) <= 5 and any(word in {"thanks", "thank", "okay", "ok", "great", "bye", "goodbye"} for word in words):
            return "small_talk", 0.96
        if any(word in words for word in ("document", "attachment", "attached", "pdf", "file", "invoice", "bill")) and any(
            word in words for word in ("explain", "summarize", "summary", "read", "details", "say", "written", "about")
        ):
            return "document_question", 0.95
        denominator_extra = len(self.vocabulary)
        scores: dict[str, float] = {}
        for label in self.labels:
            prior = math.log(self.class_counts[label] / self.total_examples)
            token_total = sum(self.word_counts[label].values())
            score = prior
            for word in words:
                score += math.log((self.word_counts[label][word] + 1) / (token_total + denominator_extra))
            scores[label] = score
        best = max(scores, key=scores.get)
        max_score = scores[best]
        exp_scores = {label: math.exp(score - max_score) for label, score in scores.items()}
        confidence = exp_scores[best] / sum(exp_scores.values())
        if confidence < 0.34:
            return "unknown", confidence
        return best, confidence


MODEL_PATH = Path(__file__).with_name("finance_intent_model.json")
try:
    finance_intent_model = FinanceIntentModel.load(MODEL_PATH)
except (OSError, ValueError, KeyError, json.JSONDecodeError):
    # A fresh checkout can still run. Run scripts/train_finance_intent_model.py
    # to create the reproducible bundled model artifact.
    finance_intent_model = FinanceIntentModel()
