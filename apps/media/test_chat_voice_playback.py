import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.internal_signing import sign_internal_request

from .models import MediaAsset

URL = "/api/v1/media/internal/chat-voice/sign/"


def _signed_internal_headers(method: str, path: str, body=None, secret: str = "real-token"):
    headers = sign_internal_request(method, path, body, secret=secret)
    return {f"HTTP_{key.upper().replace('-', '_')}": value for key, value in headers.items()}


def _voice_asset(owner, **overrides):
    defaults = dict(
        owner=owner,
        type="audio",
        bucket_key="uploads/abc/voice.m4a",
        mime_type="audio/mp4",
        bytes=4096,
        status="ready",
        metadata={"context": "chat", "original_name": "voice.m4a"},
    )
    defaults.update(overrides)
    return MediaAsset.objects.create(**defaults)


class ChatVoicePlaybackSignAuthTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM")
        self.asset = _voice_asset(self.owner)

    def test_missing_internal_token_is_rejected(self):
        res = self.client.post(URL, {"mediaAssetId": str(self.asset.id)}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_invalid_internal_token_is_rejected(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.post(
                URL,
                {"mediaAssetId": str(self.asset.id)},
                format="json",
                HTTP_X_INTERNAL_AUTH="wrong-token",
            )
        self.assertEqual(res.status_code, 401)

    def test_valid_trusted_request_signs_eligible_chat_voice_media(self):
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.post(
                URL,
                {"mediaAssetId": str(self.asset.id)},
                format="json",
                HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("url", body)
        self.assertIn("expiresAt", body)
        self.assertIn("expiresInSeconds", body)
        self.assertTrue(body["url"])

    def test_no_django_user_session_or_ownership_is_required(self):
        # The whole point: Nest already verified conversation membership.
        # A trusted request must succeed even though this test makes no
        # attempt to authenticate any Django user at all.
        with patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"}):
            res = self.client.post(
                URL,
                {"mediaAssetId": str(self.asset.id)},
                format="json",
                HTTP_X_INTERNAL_AUTH="real-token",
            )
        self.assertEqual(res.status_code, 200)

    def test_production_mode_rejects_a_token_only_request_without_a_signature(self):
        # INTERNAL_SIGNATURE_REQUIRED=True is what production actually runs
        # with (see verify_deployment_security) — a bare X-Internal-Auth
        # token, with no HMAC signature/timestamp/nonce, must NOT be enough
        # in that mode. This is the check that actually matters for "can
        # this endpoint be abused externally": the token alone is not a
        # sufficient secret if it were ever guessed/leaked without also
        # forging a fresh, correctly-signed request.
        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "1"},
        ):
            res = self.client.post(
                URL,
                {"mediaAssetId": str(self.asset.id)},
                format="json",
                HTTP_X_INTERNAL_AUTH="real-token",
            )
        # require_internal_auth raises AuthenticationFailed, which this view
        # (no WWW-Authenticate-capable authenticator configured) surfaces as
        # 401 — the important assertion is that it is rejected at all.
        self.assertEqual(res.status_code, 401)

    def test_production_mode_accepts_a_correctly_signed_request_and_rejects_replay(self):
        body = {"mediaAssetId": str(self.asset.id)}
        headers = _signed_internal_headers("POST", URL, body)

        with patch.dict(
            os.environ,
            {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "1"},
        ):
            res = self.client.post(URL, body, format="json", **headers)
            self.assertEqual(res.status_code, 200)

            replay = self.client.post(URL, body, format="json", **headers)
            self.assertEqual(replay.status_code, 401)


class ChatVoicePlaybackSignEligibilityTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670004002", password="TestPass123!", country="CM")
        self.env = patch.dict(os.environ, {"DJANGO_INTERNAL_TOKEN": "real-token", "INTERNAL_SIGNATURE_REQUIRED": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _post(self, body):
        return self.client.post(URL, body, format="json", HTTP_X_INTERNAL_AUTH="real-token")

    def test_requires_media_asset_id(self):
        res = self._post({})
        self.assertEqual(res.status_code, 400)

    def test_unknown_media_asset_id_is_rejected(self):
        res = self._post({"mediaAssetId": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(res.status_code, 404)

    def test_a_mismatched_object_key_is_rejected_as_not_found(self):
        asset = _voice_asset(self.owner)
        res = self._post({"mediaAssetId": str(asset.id), "objectKey": "uploads/some/other/key.m4a"})
        self.assertEqual(res.status_code, 404)

    def test_matching_object_key_still_succeeds(self):
        asset = _voice_asset(self.owner)
        res = self._post({"mediaAssetId": str(asset.id), "objectKey": asset.bucket_key})
        self.assertEqual(res.status_code, 200)

    def test_deleted_asset_is_rejected(self):
        asset = _voice_asset(self.owner, is_deleted=True)
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 404)

    def test_quarantined_asset_is_rejected(self):
        from .models import MediaModerationState

        asset = _voice_asset(self.owner, moderation_state=MediaModerationState.QUARANTINED)
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 404)

    def test_pending_review_asset_is_rejected(self):
        # Legacy multipart uploads reflect quarantine/requires-review into
        # `status`, not `moderation_state` — see UploadFileView.
        asset = _voice_asset(self.owner, status="pending")
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 403)

    def test_asset_with_no_bucket_key_is_rejected(self):
        asset = _voice_asset(self.owner, bucket_key="")
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 404)

    def test_non_chat_upload_context_cannot_use_this_endpoint(self):
        # e.g. a profile avatar or a marketplace complaint attachment — this
        # endpoint is scoped to messaging-eligible uploads only.
        asset = _voice_asset(self.owner, metadata={"context": "profile"})
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 403)

    def test_non_audio_media_cannot_use_this_endpoint(self):
        asset = _voice_asset(self.owner, mime_type="image/jpeg", type="image")
        res = self._post({"mediaAssetId": str(asset.id)})
        self.assertEqual(res.status_code, 403)

    def test_dm_and_group_upload_contexts_are_both_eligible(self):
        for context in ("dm", "group"):
            asset = _voice_asset(self.owner, metadata={"context": context})
            res = self._post({"mediaAssetId": str(asset.id)})
            self.assertEqual(res.status_code, 200, f"context={context!r} should be eligible")


class ChatVoicePlaybackSignTtlTests(APITestCase):
    def test_default_ttl_is_short(self):
        from .signing import MEDIA_SIGNED_URL_TTL_SECONDS
        from .services.chat_voice_playback import CHAT_VOICE_PLAYBACK_TTL_SECONDS

        # Not the old 10-day workaround — a real refresh path exists now.
        self.assertLessEqual(MEDIA_SIGNED_URL_TTL_SECONDS, 3600)
        self.assertLessEqual(CHAT_VOICE_PLAYBACK_TTL_SECONDS, 3600)
