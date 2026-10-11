"""
P0.5 security review finding: ChannelContentTranscriptWebhookView accepted
unauthenticated calls with zero signature verification, despite being a
provider-pipeline webhook - any caller who could guess/observe a
provider_job_id could overwrite that video's transcript/subtitle text or
flip a pending job to "failed". Fixed to use the same shared-secret header
convention as the app's other provider webhooks
(ChannelLiveStreamWebhookView, ChannelLiveStreamArchiveView).

Run:
  python3 manage.py test apps.broadcasts.test_security_webhooks --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from .models import BroadcastChannel, ChannelContent, ChannelContentTranscript

User = get_user_model()


class TranscriptWebhookAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        owner = User.objects.create_user(phone="+2349130000001", password="pw123456", country="NG")
        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=owner.id, owner_user=owner,
            handle="transcript-channel", display_name="Transcript Channel", is_public=True,
        )
        content = ChannelContent.objects.create(
            channel=channel, content_type="video", title="Sermon", created_by=owner,
        )
        self.transcript = ChannelContentTranscript.objects.create(
            content=content, provider="deepgram", provider_job_id="job-abc123",
            status=ChannelContentTranscript.Status.PENDING,
        )

    @override_settings(TRANSCRIPT_WEBHOOK_SECRET="shh-its-a-secret")
    def test_rejects_missing_secret_when_one_is_configured(self):
        res = self.client.post(
            "/api/v1/broadcasts/transcripts/webhook/",
            {"job_id": "job-abc123", "text": "attacker-controlled transcript"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.transcript.refresh_from_db()
        self.assertEqual(self.transcript.status, ChannelContentTranscript.Status.PENDING)
        self.assertEqual(self.transcript.text_plain, "")

    @override_settings(TRANSCRIPT_WEBHOOK_SECRET="shh-its-a-secret")
    def test_accepts_correct_secret(self):
        res = self.client.post(
            "/api/v1/broadcasts/transcripts/webhook/",
            {"job_id": "job-abc123", "text": "real transcript text"},
            format="json",
            HTTP_X_TRANSCRIPT_WEBHOOK_SECRET="shh-its-a-secret",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.transcript.refresh_from_db()
        self.assertEqual(self.transcript.status, ChannelContentTranscript.Status.READY)
        self.assertEqual(self.transcript.text_plain, "real transcript text")

    def test_still_works_unauthenticated_when_no_secret_is_configured(self):
        """No TRANSCRIPT_WEBHOOK_SECRET set (e.g. not yet provisioned in an
        environment) must not hard-break the existing provider integration -
        same fail-open-on-unconfigured-secret behavior as the other
        provider webhooks in this file."""
        res = self.client.post(
            "/api/v1/broadcasts/transcripts/webhook/",
            {"job_id": "job-abc123", "text": "real transcript text"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
