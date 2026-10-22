"""Private local model adapter for cross-specialist chat domains."""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from app.core.config import LOCAL_LLM_ENABLED, LOCAL_LLM_MODEL, GENERAL_LLM_TIMEOUT_SECONDS, LOCAL_LLM_URL

logger = logging.getLogger(__name__)


class LocalModelUnavailable(RuntimeError):
    """A model failure that callers can retry without saving a fake answer."""


def unavailable_message(status: str) -> str:
    if status == "timeout":
        return "Zengen AI took too long to respond. Please try again; the local model may still be loading."
    return "Zengen AI is temporarily unavailable. Please try again once the local AI service is running."

DOMAIN_GUIDANCE = {
    "general": "Be a versatile, thoughtful general assistant across everyday and work topics.",
    "personal_assistant": "Be a warm, practical personal assistant. Help organize ideas, plans, writing, and next steps.",
    "hr_executive": "Offer practical people-operations guidance. Ask for location or policy context when employment rules matter; do not present legal advice as fact.",
    "sales_analyst": "Help with sales strategy, messaging, pipeline thinking, and commercial analysis. Label assumptions and ask for data when needed.",
}


def generate_reply(domain: str, question: str, history: list[dict[str, str]]) -> tuple[str | None, str]:
    """Generate a domain-focused answer through the configured loopback Ollama model."""
    if not LOCAL_LLM_ENABLED:
        return None, "disabled"
    try:
        parsed = urlsplit(LOCAL_LLM_URL)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None, "unsafe_or_invalid_local_url"
        if not LOCAL_LLM_MODEL:
            return None, "model_not_configured"

        messages = [{
            "role": "system",
            "content": (
                "You are Zengen AI, a friendly, capable general assistant. "
                "Answer questions about everyday life, science, history, learning, coding, writing, personal concerns, "
                "and product recommendations as well as business. A specialist focus is optional context, never a topic restriction. "
                "Answer the actual question directly; do not redirect unrelated questions to finance or ask users to choose finance topics. "
                f"Current focus: {DOMAIN_GUIDANCE.get(domain, DOMAIN_GUIDANCE['general'])} "
                "Use only information the user provides and general knowledge. State uncertainty, ask concise follow-ups when useful, "
                "and never claim to have accessed their workspace data unless it was explicitly provided in this conversation. "
                "You are an AI assistant powered by a local machine-learning language model. Never claim you are merely backend logic. "
                "The app has separate sourced weather, exchange-rate, and place lookups, but this model has no live web access. "
                "For other current facts, do not invent prices, news, availability, dates, or citations. "
                "For uncertain geography or unfamiliar names, state uncertainty instead of inventing a district, distance, or history. "
                "Respond in the user's language and use conversation context for follow-up questions. "
                "Keep responses clear and natural, with enough detail to help."
            ),
        }]
        # Bound context for the installed small local model, keeping the newest turns.
        recent = []
        remaining = 6000
        for item in reversed(history[-10:]):
            if item.get("role") not in {"user", "assistant"}:
                continue
            content = item["content"][:3000]
            if len(content) > remaining:
                break
            recent.append({"role": item["role"], "content": content})
            remaining -= len(content)
        messages.extend(reversed(recent))
        messages.append({"role": "user", "content": question[:4000]})
        body = json.dumps({
            "model": LOCAL_LLM_MODEL,
            "stream": False,
            "keep_alive": "5m",
            "messages": messages,
            "options": {"temperature": 0.2, "num_predict": 700, "num_ctx": 4096},
        }).encode("utf-8")
        request = Request(
            f"http://{parsed.netloc}/api/chat",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=max(1.0, min(GENERAL_LLM_TIMEOUT_SECONDS, 120.0))) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        content = str(payload.get("message", {}).get("content", "")).strip()
        if not content or len(content) > 8000:
            return None, "invalid_model_response"
        return content, "available"
    except TimeoutError:
        logger.warning("Local Zengen AI model timed out.")
        return None, "timeout"
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
        logger.info("Local Zengen AI model unavailable (%s).", type(error).__name__)
        return None, "unavailable"
