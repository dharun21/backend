"""Small, source-backed tools for questions the local model cannot verify."""

from __future__ import annotations

import json
import logging
import os
import re
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

USER_AGENT = os.getenv("ZENGENAI_PUBLIC_USER_AGENT", "ZengenAI/0.1 (https://dharun21.github.io/Ai-workforce-workspace/)")
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
MET_API = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
FRANKFURTER_API = "https://api.frankfurter.dev/v2/rate"
TIRUVARUR_OFFICIAL = "https://tiruvarur.nic.in/about-district/"
_cache: dict[str, tuple[datetime, dict, str | None]] = {}
_cache_lock = Lock()


@dataclass(frozen=True)
class LiveAnswer:
    reply: str
    engine: str
    sources: list[dict[str, str]]


def _get_json(url: str) -> dict:
    now = datetime.now(timezone.utc)
    with _cache_lock:
        cached = _cache.get(url)
    if cached and cached[0] > now:
        return cached[1]
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if cached and cached[2]:
        headers["If-Modified-Since"] = cached[2]
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read(2_000_000).decode("utf-8"))
            expires = response.headers.get("Expires")
            try:
                expiry = parsedate_to_datetime(expires).astimezone(timezone.utc) if expires else now + timedelta(minutes=10)
            except (TypeError, ValueError):
                expiry = now + timedelta(minutes=10)
            if expiry <= now:
                expiry = now + timedelta(minutes=5)
            modified = response.headers.get("Last-Modified")
    except HTTPError as error:
        if error.code == 304 and cached:
            with _cache_lock:
                _cache[url] = (now + timedelta(minutes=10), cached[1], cached[2])
            return cached[1]
        raise
    with _cache_lock:
        if len(_cache) >= 512:
            _cache.clear()
        _cache[url] = (expiry, data, modified)
    return data


def _source(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url}


def _wiki_page(title: str) -> dict | None:
    params = urlencode({
        "action": "query", "prop": "extracts|coordinates", "exintro": "1",
        "explaintext": "1", "titles": title, "redirects": "1",
        "format": "json", "formatversion": "2",
    })
    pages = _get_json(f"{WIKIPEDIA_API}?{params}").get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or pages[0].get("ns") != 0:
        return None
    return pages[0]


def _place_question(question: str) -> str | None:
    patterns = (
        r"(?:do you know|tell me about|what do you know about)\s+(.+?)[?.!]*$",
        r"(?:where is|where's)\s+(.+?)[?.!]*$",
        r"(?:what is|what's|define)\s+(.+?)[?.!]*$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, question.strip(), re.IGNORECASE)
        if match:
            subject = re.sub(r"^(?:the city of|the town of|about)\s+", "", match.group(1), flags=re.I).strip()
            if subject.lower() in {"me", "you", "my name", "your name", "it", "this", "that", "anything", "something"} or re.search(r"\b(?:my|our|your|today|todays|current|latest|now)\b", subject, re.I):
                return None
            if 2 <= len(subject) <= 70 and len(subject.split()) <= 6:
                return subject
    return None


def _fact_answer(subject: str) -> LiveAnswer | None:
    tiruvarur = bool(re.fullmatch(r"(?:thir[ui]|tir[ui])varur", subject, re.I))
    if tiruvarur:
        return LiveAnswer(
            "Yes. Thiruvarur (Tiruvarur) is a municipality in Tamil Nadu and the headquarters "
            "of its own district, Tiruvarur. It is not in Tirunelveli district. "
            "The district was created on 1 January 1997.",
            "government_place_fact",
            [_source("Tiruvarur District Government", TIRUVARUR_OFFICIAL)],
        )
    page = _wiki_page(subject)
    if not page:
        return None
    title = page.get("title", subject)
    # Exact title or redirect resolution only. A broad search result may be a
    # different place/person and would repeat the model's original mistake.
    extract = re.sub(r"\s+", " ", page.get("extract", "")).strip()
    if not extract:
        return None
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", extract)
    summary = " ".join(sentences[:2])[:650].strip()
    url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    sources = [_source(f"Wikipedia: {title}", url)]
    return LiveAnswer(summary, "sourced_encyclopedia", sources)


CURRENCY_ALIASES = {
    "usd": "USD", "dollar": "USD", "dollars": "USD",
    "inr": "INR", "rupee": "INR", "rupees": "INR",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
    "jpy": "JPY", "yen": "JPY",
    "cad": "CAD", "aud": "AUD", "sgd": "SGD", "aed": "AED",
}


def _currency_pair(question: str) -> tuple[str, str, float] | None:
    words = re.findall(r"\b[a-z]{3,7}\b", question.lower())
    codes = [CURRENCY_ALIASES[word] for word in words if word in CURRENCY_ALIASES]
    if len(codes) < 2 or codes[0] == codes[1]:
        return None
    if not re.search(r"\b(?:rate|exchange|convert|conversion|worth|value|today|todays|current|latest|to|in)\b", question, re.I):
        return None
    amount_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:usd|inr|eur|gbp|jpy|cad|aud|sgd|aed|dollars?|rupees?|euros?|pounds?|yen)\b", question, re.I)
    return codes[0], codes[1], float(amount_match.group(1)) if amount_match else 1.0


def _currency_answer(base: str, quote_code: str, amount: float) -> LiveAnswer:
    url = f"{FRANKFURTER_API}/{base}/{quote_code}"
    data = _get_json(url)
    if data.get("base") != base or data.get("quote") != quote_code or not isinstance(data.get("rate"), (int, float)):
        raise ValueError("Invalid exchange rate response")
    rate = float(data["rate"])
    rate_date = str(data.get("date", ""))
    datetime.fromisoformat(rate_date)  # Reject a missing or invalid source date.
    conversion = f"{amount:g} {base} ≈ {amount * rate:,.2f} {quote_code}. " if amount != 1 else ""
    reply = (f"{conversion}1 {base} ≈ {rate:,.4f} {quote_code} using the latest available "
             f"reference rate dated {rate_date}. This is a reference rate, not a live trading or card rate.")
    return LiveAnswer(reply, "frankfurter_exchange_rate", [_source("Frankfurter exchange rates", url)])


def _weather_place(question: str) -> str | None:
    if not re.search(r"\b(?:weather|forecast|temperature|rainfall|raining)\b", question, re.I):
        return None
    match = re.search(r"\b(?:in|at|for)\s+([A-Za-z][A-Za-z .'-]{1,65})", question, re.I)
    if not match:
        match = re.match(r"^([A-Za-z][A-Za-z .'-]{1,65}?)\s+(?:weather|forecast)\b", question, re.I)
    if not match:
        return ""
    place = re.split(r"\b(?:today|tomorrow|this week|full day|right now|now|please)\b", match.group(1), flags=re.I)[0]
    return place.strip(" .,'?!")


def _condition(code: str) -> str:
    value = code.replace("_day", "").replace("_night", "").replace("_polartwilight", "")
    return value.replace("partlycloudy", "partly cloudy").replace("cloudy", "cloudy").replace("rainshowers", "rain showers").replace("fair", "fair").replace("clearsky", "clear").replace("heavyrain", "heavy rain").replace("lightrain", "light rain").replace("snowshowers", "snow showers").replace("thunder", " thunder").strip()


def _weather_answer(place: str, question: str) -> LiveAnswer:
    page = _wiki_page(place) or (_wiki_page(place.split(",", 1)[0].strip()) if "," in place else None)
    if not page or not page.get("coordinates"):
        return LiveAnswer(f"I couldn't find reliable coordinates for {place}. Try a city and country, such as 'Chennai, India'.", "weather_location_unavailable", [])
    coords = page["coordinates"][0]
    lat, lon = float(coords["lat"]), float(coords["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid location coordinates")
    title = page.get("title", place)
    url = f"{MET_API}?lat={lat:.3f}&lon={lon:.3f}"
    data = _get_json(url)
    timeseries = data.get("properties", {}).get("timeseries", [])
    # India is unambiguous; elsewhere UTC is shown explicitly instead of
    # guessing a local time zone and silently shifting the user's day.
    india = bool(re.search(r"\b(?:India|Tamil Nadu)\b", page.get("extract", "")))
    zone = timezone(timedelta(hours=5, minutes=30)) if india else timezone.utc
    zone_label = "IST" if india else "UTC"
    now = datetime.now(zone)
    target = now.date() + timedelta(days=1 if re.search(r"\btomorrow\b", question, re.I) else 0)
    rows = []
    for item in timeseries:
        moment = datetime.fromisoformat(item["time"].replace("Z", "+00:00")).astimezone(zone)
        if moment.date() != target:
            continue
        details = item.get("data", {}).get("instant", {}).get("details", {})
        if not isinstance(details.get("air_temperature"), (int, float)):
            continue
        next_hour = item.get("data", {}).get("next_1_hours", {})
        rows.append((moment, float(details["air_temperature"]), next_hour.get("summary", {}).get("symbol_code", ""), next_hour.get("details", {}).get("precipitation_amount")))
    if not rows:
        return LiveAnswer(f"I couldn't get a {target.isoformat()} forecast for {title} right now. Please try again shortly.", "weather_unavailable", [])
    # Show roughly four-hourly snapshots, including the first and last available hours.
    samples = rows[::4]
    if samples[-1] != rows[-1]:
        samples.append(rows[-1])
    snapshots = "; ".join(f"{moment.strftime('%I %p').lstrip('0')}: {temp:g}°C, {_condition(symbol) or 'conditions unavailable'}" for moment, temp, symbol, _ in samples[:8])
    temps = [row[1] for row in rows]
    rain = [float(row[3]) for row in rows if isinstance(row[3], (int, float))]
    rain_note = f" Estimated precipitation over available hourly periods: {sum(rain):.1f} mm." if rain else ""
    period = "remaining hours of today" if target == now.date() else "tomorrow"
    reply = (f"Forecast for {title} on {target.isoformat()} ({zone_label}, {period}): "
             f"{min(temps):g}–{max(temps):g}°C across the available forecast hours. "
             f"{snapshots}.{rain_note} Forecasts can change.")
    wiki_url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    return LiveAnswer(reply + " Data: MET Norway (summary adapted).", "met_norway_weather", [
        _source("MET Norway forecast", url),
        _source("MET Norway • CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
        _source(f"Location: {title}", wiki_url),
    ])


def answer_live_question(question: str) -> LiveAnswer | None:
    """Return a deterministic sourced answer, or None for ordinary conversation."""
    pair = _currency_pair(question)
    if pair:
        try:
            return _currency_answer(*pair)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logger.warning("Exchange-rate lookup failed", exc_info=True)
            return LiveAnswer("I can't verify the latest exchange rate right now. Please try again shortly.", "exchange_rate_unavailable", [])
    place = _weather_place(question)
    if place is not None:
        if not place:
            return LiveAnswer("Which city should I check the weather for?", "weather_needs_location", [])
        try:
            return _weather_answer(place, question)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logger.warning("Weather lookup failed", exc_info=True)
            return LiveAnswer("I can't verify the latest weather forecast right now. Please try again shortly.", "weather_unavailable", [])
    if re.search(r"\b(?:thir[ui]|tir[ui])varur\b", question, re.I) and re.search(r"\b(?:district|tirunelveli|thirunelveli|where|located|wrong|incorrect)\b", question, re.I):
        return _fact_answer("Thiruvarur")
    subject = _place_question(question)
    if subject:
        try:
            return _fact_answer(subject)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            logger.warning("Encyclopedia lookup failed", exc_info=True)
            return LiveAnswer("I can't verify that fact from a source right now. Please try again shortly.", "source_unavailable", [])
    return None
