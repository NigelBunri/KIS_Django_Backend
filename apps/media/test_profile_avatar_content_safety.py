# apps/media/test_profile_avatar_content_safety.py
"""
Closes a real coverage gap found while auditing AI-moderation coverage:
apps.media.upload_intent's generic presigned-upload confirm flow (used by
profile_avatar/profile_cover, among others) created every canonical
MediaAsset as NOT_SCANNED and never called into the explicit-content scan
pipeline at all - confirmed via MediaModerationState's own docstring
("Wiring this field to real decisions is Phase 2+ work") and by grepping
every real call site of scan_uploaded_object_task/scan_video_and_resolve_task
across the repo, none of which touch apps.accounts or this module.

These tests mock the scan boundary (apps.media.safety.
scan_saved_upload_for_explicit_content) with canned MediaSafetyDecision
verdicts - no real or simulated explicit image content is used anywhere
here, matching this project's fixture-safety requirement. Reuses the exact
S3-mocking pattern apps/media/test_phase2.py already established.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.models import Profile
from apps.media.safety import MediaSafetyDecision

from .models import MediaAsset, MediaModerationState, MediaSafetyScan
from .tests import _mock_s3_client

INITIATE_URL = "/api/v1/media/uploads/initiate/"


def _confirm_url(upload_id):
    return f"/api/v1/media/uploads/{upload_id}/confirm/"


def _passed_decision():
    return MediaSafetyDecision(
        status="passed", quarantine=False, provider="nudenet",
        reason="nudenet_clean", user_message="Upload accepted.", requires_review=False, score=0.0,
    )


def _pending_review_decision():
    return MediaSafetyDecision(
        status="pending_review", quarantine=True, provider="nudenet",
        reason="nudenet_low_confidence:TEST_LABEL", user_message="Your upload is under review.",
        requires_review=True, score=0.4,
    )


def _blocked_decision():
    return MediaSafetyDecision(
        status="blocked", quarantine=True, provider="nudenet",
        reason="nudenet_explicit:TEST_LABEL", user_message="This upload was not accepted.",
        requires_review=False, score=0.95,
    )


@patch("apps.media.storage_backends.S3MediaStorage._client")
class ProfileAvatarContentSafetyTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(phone="+237670103001", password="TestPass123!", country="CM")

    def _initiate(self, mock_client, *, context="profile_avatar"):
        mock_client.return_value = _mock_s3_client()
        self.client.force_authenticate(self.owner)
        initiate = self.client.post(
            INITIATE_URL,
            {"context": context, "filename": "avatar.jpg", "content_type": "image/jpeg", "size_bytes": 1_000_000},
            format="json",
        )
        assert initiate.status_code == 201, initiate.data
        return initiate.data["uploadId"]

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_clearly_safe_avatar_attaches_and_is_marked_passed(self, mock_scan, mock_client):
        mock_scan.return_value = _passed_decision()
        upload_id = self._initiate(mock_client)

        response = self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        asset = MediaAsset.objects.get(id=response.data["assetId"])
        self.assertEqual(asset.moderation_state, MediaModerationState.PASSED)
        profile = Profile.objects.get(user=self.owner)
        self.assertTrue(profile.avatar_file.name)
        scan = MediaSafetyScan.objects.get(upload_id=upload_id)
        self.assertEqual(scan.status, "passed")

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_ambiguous_avatar_still_attaches_but_is_flagged_for_review(self, mock_scan, mock_client):
        # Matches the platform-wide permissive-until-reviewed pattern this
        # audit found already in place elsewhere (statuses/broadcasts) -
        # pending_review is not a hard block, but must be visible to a
        # human reviewer, which is what the MediaSafetyScan + alert prove.
        mock_scan.return_value = _pending_review_decision()
        upload_id = self._initiate(mock_client)

        response = self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        asset = MediaAsset.objects.get(id=response.data["assetId"])
        self.assertEqual(asset.moderation_state, MediaModerationState.PENDING_REVIEW)
        profile = Profile.objects.get(user=self.owner)
        self.assertTrue(profile.avatar_file.name)
        scan = MediaSafetyScan.objects.get(upload_id=upload_id)
        self.assertTrue(scan.requires_review)

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_clearly_prohibited_avatar_is_rejected_not_attached(self, mock_scan, mock_client):
        mock_scan.return_value = _blocked_decision()
        upload_id = self._initiate(mock_client)

        response = self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        profile, _ = Profile.objects.get_or_create(user=self.owner)
        self.assertFalse(profile.avatar_file.name)
        scan = MediaSafetyScan.objects.get(upload_id=upload_id)
        self.assertEqual(scan.status, "blocked")

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_blocked_scan_audit_row_survives_the_rejected_transaction(self, mock_scan, mock_client):
        # The regression this whole fix depends on: confirm_upload_intent
        # raises the handler's ValidationError from OUTSIDE its
        # transaction.atomic() block (see the try/except around the
        # handler call) - if that ever regresses back to an in-block
        # raise, this MediaSafetyScan row (and the asset's moderation
        # state) would roll back and vanish, silently destroying the only
        # audit trail of a blocked upload.
        mock_scan.return_value = _blocked_decision()
        upload_id = self._initiate(mock_client)

        self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertTrue(MediaSafetyScan.objects.filter(upload_id=upload_id, status="blocked").exists())
        asset = MediaAsset.objects.get(owner=self.owner)
        self.assertEqual(asset.moderation_state, MediaModerationState.QUARANTINED)

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_replaying_an_already_confirmed_avatar_does_not_rescan(self, mock_scan, mock_client):
        mock_scan.return_value = _passed_decision()
        upload_id = self._initiate(mock_client)
        first = self.client.post(_confirm_url(upload_id), {}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        mock_scan.reset_mock()

        replay = self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertEqual(replay.status_code, 200, replay.data)
        mock_scan.assert_not_called()

    @patch("apps.media.safety.scan_saved_upload_for_explicit_content")
    def test_profile_cover_is_also_scanned(self, mock_scan, mock_client):
        mock_scan.return_value = _blocked_decision()
        upload_id = self._initiate(mock_client, context="profile_cover")

        response = self.client.post(_confirm_url(upload_id), {}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        profile, _ = Profile.objects.get_or_create(user=self.owner)
        self.assertFalse(profile.cover_file.name)
