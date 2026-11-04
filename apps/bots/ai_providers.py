# apps/bots/ai_providers.py
"""
KIS Bot Platform (Phase 3) — calling a bot owner's OWN AI provider
credential. This module never uses any KIS-paid API key: every call
here is made with the bot owner's own, encrypted-at-rest credential
(apps.bots.models.BotAIConnection). This is deliberately separate from
apps.ai_integration, which is KIS's own AI usage for unrelated features
(translation, moderation, etc.) paid for by KIS, not bot owners — the
two must never be conflated, per the explicit requirement that the Bot
Platform must not become a KIS-owned AI service.

Every provider function returns the same normalized shape:
    {"text": str, "prompt_tokens": int | None, "completion_tokens": int | None}
and raises AIProviderError on any failure (bad credential, timeout, non-
2xx response, malformed response body) - callers (apps.bots.ai_decision)
treat this uniformly as "the AI call failed", never executing anything
as a result.

Implemented via plain HTTP (requests) against each provider's REST API
rather than their SDKs, since none of those SDKs are a hard dependency
of this project (apps.ai_integration already degrades gracefully when
they're absent) - this keeps bot-owner AI connections working
regardless of what happens to be installed.
"""
from __future__ import annotations

import requests

from common.url_safety import is_safe_external_url

from .models import BotAIConnection, BotAIProvider

REQUEST_TIMEOUT_SECONDS = 20


class AIProviderError(Exception):
    """Raised for any failure calling an AI provider - network error,
    timeout, non-2xx response, or a response shape we can't parse.
    Callers must treat this as 'no decision was made', never partially
    act on it."""


def _call_anthropic(connection: BotAIConnection, api_key: str, *, system_prompt: str, user_prompt: str, max_tokens: int) -> dict:
    model = connection.model or "claude-3-5-haiku-latest"
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AIProviderError(f"Anthropic request failed: {exc}") from exc
    if resp.status_code != 200:
        raise AIProviderError(f"Anthropic returned HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
        text = "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
        usage = body.get("usage") or {}
        return {
            "text": text,
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
        }
    except (ValueError, AttributeError, TypeError) as exc:
        raise AIProviderError(f"Could not parse Anthropic response: {exc}") from exc


def _call_openai_compatible(url: str, api_key: str, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int, provider_label: str) -> dict:
    """Shared by OpenAI itself and any 'custom' endpoint, since the
    overwhelming majority of OpenAI-compatible third-party/self-hosted
    endpoints (vLLM, Ollama, OpenRouter, etc.) speak this exact
    /chat/completions shape."""
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AIProviderError(f"{provider_label} request failed: {exc}") from exc
    if resp.status_code != 200:
        raise AIProviderError(f"{provider_label} returned HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return {
            "text": text,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(f"Could not parse {provider_label} response: {exc}") from exc


def _call_gemini(connection: BotAIConnection, api_key: str, *, system_prompt: str, user_prompt: str, max_tokens: int) -> dict:
    model = connection.model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    try:
        resp = requests.post(
            url,
            headers={"content-type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AIProviderError(f"Gemini request failed: {exc}") from exc
    if resp.status_code != 200:
        raise AIProviderError(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        usage = body.get("usageMetadata") or {}
        return {
            "text": text,
            "prompt_tokens": usage.get("promptTokenCount"),
            "completion_tokens": usage.get("candidatesTokenCount"),
        }
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(f"Could not parse Gemini response: {exc}") from exc


def call_ai_provider(connection: BotAIConnection, api_key: str, *, system_prompt: str, user_prompt: str, max_tokens: int = 512) -> dict:
    """Dispatches to the right provider based on connection.provider.
    api_key is passed in already-decrypted (never persisted or logged
    by this function) by the caller, which is the only place that holds
    the dedicated KEK."""
    if connection.provider == BotAIProvider.ANTHROPIC:
        return _call_anthropic(connection, api_key, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens)
    if connection.provider == BotAIProvider.OPENAI:
        return _call_openai_compatible(
            "https://api.openai.com/v1/chat/completions", api_key,
            model=connection.model or "gpt-4o-mini", system_prompt=system_prompt, user_prompt=user_prompt,
            max_tokens=max_tokens, provider_label="OpenAI",
        )
    if connection.provider == BotAIProvider.GEMINI:
        return _call_gemini(connection, api_key, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens)
    if connection.provider == BotAIProvider.CUSTOM:
        if not connection.custom_endpoint_url:
            raise AIProviderError("Custom provider is selected but no custom_endpoint_url is configured.")
        if not is_safe_external_url(connection.custom_endpoint_url):
            # SSRF guard: custom_endpoint_url is owner-supplied but fetched
            # by the KIS backend itself, same risk/same fix as
            # apps.partners.services' webhook URL guard.
            raise AIProviderError("Custom provider endpoint is not an allowed external address.")
        return _call_openai_compatible(
            connection.custom_endpoint_url, api_key,
            model=connection.model or "", system_prompt=system_prompt, user_prompt=user_prompt,
            max_tokens=max_tokens, provider_label="Custom provider",
        )
    raise AIProviderError(f"Unsupported AI provider '{connection.provider}'.")
