# apps/bots/test_ai_providers.py
"""Phase 3 — the HTTP-calling layer for each AI provider. Mocks
requests.post so these never make a real network call; a genuine live
call against a real provider is exercised separately in production
verification with a real, owner-supplied key."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from .ai_providers import AIProviderError, call_ai_provider
from .models import BotAIConnection, BotAIProvider


def _connection(provider, **overrides):
    conn = BotAIConnection(provider=provider, model=overrides.get("model", ""), custom_endpoint_url=overrides.get("custom_endpoint_url", ""))
    return conn


class AIProviderCallTests(TestCase):
    @patch("apps.bots.ai_providers.requests.post")
    def test_anthropic_success_parses_text_and_usage(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            "content": [{"type": "text", "text": '{"selected": [0]}'}],
            "usage": {"input_tokens": 120, "output_tokens": 8},
        })
        result = call_ai_provider(_connection(BotAIProvider.ANTHROPIC), "key", system_prompt="sys", user_prompt="usr")
        self.assertEqual(result["text"], '{"selected": [0]}')
        self.assertEqual(result["prompt_tokens"], 120)
        self.assertEqual(result["completion_tokens"], 8)
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(call_kwargs["headers"]["x-api-key"], "key")

    @patch("apps.bots.ai_providers.requests.post")
    def test_anthropic_non_200_raises(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, text="unauthorized")
        with self.assertRaises(AIProviderError):
            call_ai_provider(_connection(BotAIProvider.ANTHROPIC), "bad-key", system_prompt="sys", user_prompt="usr")

    @patch("apps.bots.ai_providers.requests.post")
    def test_openai_success_parses_chat_completion_shape(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            "choices": [{"message": {"content": '{"selected": []}'}}],
            "usage": {"prompt_tokens": 90, "completion_tokens": 5},
        })
        result = call_ai_provider(_connection(BotAIProvider.OPENAI), "key", system_prompt="sys", user_prompt="usr")
        self.assertEqual(result["text"], '{"selected": []}')
        self.assertEqual(result["prompt_tokens"], 90)

    @patch("apps.bots.ai_providers.requests.post")
    def test_gemini_success_parses_candidates_shape(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            "candidates": [{"content": {"parts": [{"text": '{"selected": [1]}'}]}}],
            "usageMetadata": {"promptTokenCount": 70, "candidatesTokenCount": 12},
        })
        result = call_ai_provider(_connection(BotAIProvider.GEMINI), "key", system_prompt="sys", user_prompt="usr")
        self.assertEqual(result["text"], '{"selected": [1]}')
        self.assertEqual(result["completion_tokens"], 12)

    def test_custom_provider_rejects_unsafe_endpoint(self):
        conn = _connection(BotAIProvider.CUSTOM, custom_endpoint_url="http://169.254.169.254/latest/meta-data/")
        with self.assertRaises(AIProviderError):
            call_ai_provider(conn, "key", system_prompt="sys", user_prompt="usr")

    def test_custom_provider_requires_endpoint_url(self):
        conn = _connection(BotAIProvider.CUSTOM, custom_endpoint_url="")
        with self.assertRaises(AIProviderError):
            call_ai_provider(conn, "key", system_prompt="sys", user_prompt="usr")

    @patch("apps.bots.ai_providers.requests.post")
    def test_malformed_response_raises_rather_than_crashing(self, mock_post):
        # "content" present but shaped unlike anything Anthropic actually
        # sends (a bare None entry) - breaks the .get()-chain parse path
        # rather than silently degrading to empty text.
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"content": [None]})
        with self.assertRaises(AIProviderError):
            call_ai_provider(_connection(BotAIProvider.ANTHROPIC), "key", system_prompt="sys", user_prompt="usr")

    @patch("apps.bots.ai_providers.requests.post", side_effect=TimeoutError("boom"))
    def test_network_timeout_raises_ai_provider_error(self, mock_post):
        import requests

        mock_post.side_effect = requests.Timeout("timed out")
        with self.assertRaises(AIProviderError):
            call_ai_provider(_connection(BotAIProvider.OPENAI), "key", system_prompt="sys", user_prompt="usr")
