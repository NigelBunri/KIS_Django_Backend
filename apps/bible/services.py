import json
import os
import random
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from groq import Groq
except ImportError:  # pragma: no cover - optional dependency
    Groq = None


BLOCKED_TOPICS = {
    "math",
    "physics",
    "chemistry",
    "coding",
    "programming",
    "stocks",
    "crypto",
    "trading",
}

SCRIPTURE_SNIPPETS = [
    "Psalm 23:1 - The Lord is my shepherd; I shall not want.",
    "Proverbs 3:5 - Trust in the Lord with all your heart.",
    "John 3:16 - For God so loved the world...",
    "Romans 8:28 - All things work together for good.",
    "Philippians 4:6 - Do not be anxious about anything.",
]

PRAYER_LINES = [
    "Lord, steady my heart and guide my steps.",
    "Father, teach me to walk in wisdom and humility.",
    "Holy Spirit, renew my mind and strengthen my faith.",
    "Jesus, help me love others as You have loved me.",
]

logger = logging.getLogger(__name__)


def is_bible_safe_prompt(text: str) -> bool:
    lowered = (text or "").lower()
    return not any(keyword in lowered for keyword in BLOCKED_TOPICS)


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 12) -> dict | None:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as exc:
        logger.warning("Bible AI HTTP error: %s", exc)
    except URLError as exc:
        logger.warning("Bible AI URL error: %s", exc)
    except json.JSONDecodeError as exc:
        logger.warning("Bible AI JSON decode error: %s", exc)
    except Exception as exc:
        logger.exception("Bible AI unexpected error: %s", exc)
        return None
    return None


def _call_groq(messages: list[dict]) -> str | None:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        logger.info("Groq key missing; skipping Groq provider.")
        return None
    if Groq is None:
        logger.warning("Groq SDK not installed; cannot use Groq provider.")
        return None
    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.7,
            max_tokens=400,
        )
        return completion.choices[0].message.content
    except Exception as exc:
        logger.warning("Groq SDK error: %s", exc)
        return None


def _call_gemini(prompt: str) -> str | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.info("Gemini key missing; skipping Gemini provider.")
        return None
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }
    data = _post_json(url, payload)
    if not data:
        logger.warning("Gemini response empty or invalid.")
        return None
    candidates = data.get("candidates") or []
    if not candidates:
        logger.warning("Gemini response missing candidates.")
        return None
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        logger.warning("Gemini response missing content parts.")
        return None
    return parts[0].get("text")


def _ai_generate(prompt: str, system: str) -> str | None:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    response = _call_groq(messages)
    if response:
        logger.info("Bible AI used provider: groq")
        return response
    response = _call_gemini(f"{system}\n\n{prompt}")
    if response:
        logger.info("Bible AI used provider: gemini")
        return response
    logger.warning("Bible AI: no provider available, returning None.")
    return None


def build_meditation(topic: str) -> dict:
    topic_label = topic or "Faithfulness"
    system = (
        "You are a Bible study assistant. Only respond with faith-based content rooted in Scripture. "
        "Return a short meditation and a short prayer."
    )
    prompt = (
        f"Topic: {topic_label}. "
        "Provide a title, a meditation (2-4 sentences), and a prayer (1-2 sentences). "
        "Return JSON with keys: title, content, prayer_text."
    )
    ai_text = _ai_generate(prompt, system)
    if ai_text:
        try:
            parsed = json.loads(ai_text.strip().strip("`"))
            return {
                "title": parsed.get("title") or f"Meditation on {topic_label}",
                "content": parsed.get("content") or "",
                "prayer_text": parsed.get("prayer_text") or "",
                "date": date.today(),
            }
        except json.JSONDecodeError:
            return {
                "title": f"Meditation on {topic_label}",
                "content": ai_text.strip(),
                "prayer_text": "",
                "date": date.today(),
            }
    return {
        "title": f"Meditation on {topic_label}",
        "content": "AI is currently unavailable. Please try again in a moment.",
        "prayer_text": "",
        "date": date.today(),
    }


def build_bot_reply(message: str) -> str:
    if not is_bible_safe_prompt(message):
        return (
            "I can only help with Bible study and faith-related questions. "
            "Ask me about Scripture, prayer, or Christian living."
        )
    system = (
        "You are a warm, human-like Christian mentor. You only answer Bible study, prayer, "
        "and Christian living questions. Respond with empathy, encouragement, and clarity. "
        "Always include at least one Bible verse reference in your response. "
        "Do not answer questions about math, physics, or programming."
    )
    prompt = (
        f"User question: {message}\n"
        "Answer in a friendly, conversational paragraph. "
        "Include a Bible verse reference and a short follow-up question."
    )
    ai_text = _ai_generate(prompt, system)
    if ai_text:
        return ai_text.strip()
    return "AI is currently unavailable. Please try again in a moment."
