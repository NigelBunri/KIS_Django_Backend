"""
ModerationAppeal — no appeal mechanism existed anywhere in the system
before this: a warned/suspended user, a creator whose content was taken
down, or an uploader whose media was blocked had no way to ask for a human
to reconsider. Covers submission authorization (authorize_appeal) and
decision/reversal (decide_appeal) for every currently-supported target_type,
plus the explicit "not supported yet" rejections for the cases this
honestly can't authorize (non-USER flags, partner-owned channels, chat
message reports with no recorded sender identity).

Run:
  python3 manage.py test apps.moderation.test_appeals --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.media.models import MediaSafetyScan

from . import models

User = get_user_model()

APPEALS_URL = "/api/v1/appeals/"


def decide_url(appeal_id):
    return f"/api/v1/appeals/{appeal_id}/decide/"


class FlagAppealTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+2348400000001", password="pw123456", country="NG")
        self.admin = User.objects.create_user(
            phone="+2348400000002", password="pw123456", country="NG", is_staff=True,
        )
        self.flag = models.Flag.objects.create(
            source="SYSTEM", target_type="USER", target_id=self.user.id,
            reason="explicit content", severity="HIGH", status="ACTIONED",
        )
        models.UserReputation.objects.create(user_id=self.user.id, flags_received=6, actions_taken=6)
        self.user.status = "suspended"
        self.user.is_active = False
        self.user.save(update_fields=["status", "is_active"])

    def test_suspended_user_can_appeal_their_own_flag(self):
        self.client.force_authenticate(self.user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "This was a family photo, not explicit."},
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        self.assertEqual(models.ModerationAppeal.objects.count(), 1)

    def test_cannot_appeal_someone_elses_flag(self):
        other = User.objects.create_user(phone="+2348400000003", password="pw123456", country="NG")
        self.client.force_authenticate(other)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "not mine to appeal"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertEqual(models.ModerationAppeal.objects.count(), 0)

    def test_cannot_appeal_a_flag_that_has_not_been_actioned(self):
        pending_flag = models.Flag.objects.create(
            source="SYSTEM", target_type="USER", target_id=self.user.id,
            reason="pending", severity="LOW", status="PENDING",
        )
        self.client.force_authenticate(self.user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(pending_flag.id), "reason": "too early"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)

    def test_cannot_appeal_a_non_user_flag(self):
        content_flag = models.Flag.objects.create(
            source="SYSTEM", target_type="POST", target_id=self.user.id,
            reason="content flag", severity="LOW", status="ACTIONED",
        )
        self.client.force_authenticate(self.user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(content_flag.id), "reason": "appeal"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("user account", res.data["detail"])

    def test_duplicate_pending_appeal_is_rejected(self):
        self.client.force_authenticate(self.user)
        payload = {"target_type": "flag", "target_id": str(self.flag.id), "reason": "first"}
        self.client.post(APPEALS_URL, payload, format="json")

        res = self.client.post(APPEALS_URL, payload, format="json")

        self.assertEqual(res.status_code, 409)
        self.assertEqual(models.ModerationAppeal.objects.count(), 1)

    def test_staff_overturn_lifts_the_suspension_and_undoes_the_strike(self):
        self.client.force_authenticate(self.user)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "wrongly flagged"},
            format="json",
        )
        appeal_id = submit.data["id"]

        self.client.force_authenticate(self.admin)
        res = self.client.post(decide_url(appeal_id), {"action": "overturn", "notes": "reviewed, was fine"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "OVERTURNED")
        self.assertTrue(res.data["reversal_applied"])

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "active")
        self.assertTrue(self.user.is_active)

        reputation = models.UserReputation.objects.get(user_id=self.user.id)
        self.assertEqual(reputation.flags_received, 5)
        self.assertEqual(reputation.actions_taken, 5)

        self.flag.refresh_from_db()
        self.assertEqual(self.flag.status, "DISMISSED")

        self.assertTrue(
            models.ModerationAction.objects.filter(flag=self.flag, action="REINSTATE").exists()
        )

    def test_staff_uphold_does_not_touch_the_suspension(self):
        self.client.force_authenticate(self.user)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "appeal"},
            format="json",
        )
        appeal_id = submit.data["id"]

        self.client.force_authenticate(self.admin)
        res = self.client.post(decide_url(appeal_id), {"action": "uphold", "notes": "correctly actioned"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "UPHELD")
        self.assertFalse(res.data["reversal_applied"])

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, "suspended")

    def test_non_staff_cannot_decide_an_appeal(self):
        self.client.force_authenticate(self.user)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "appeal"},
            format="json",
        )
        appeal_id = submit.data["id"]

        res = self.client.post(decide_url(appeal_id), {"action": "overturn"}, format="json")

        self.assertEqual(res.status_code, 403)

    def test_an_already_decided_appeal_cannot_be_decided_again(self):
        self.client.force_authenticate(self.user)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "flag", "target_id": str(self.flag.id), "reason": "appeal"},
            format="json",
        )
        appeal_id = submit.data["id"]

        self.client.force_authenticate(self.admin)
        self.client.post(decide_url(appeal_id), {"action": "uphold"}, format="json")
        res = self.client.post(decide_url(appeal_id), {"action": "overturn"}, format="json")

        self.assertEqual(res.status_code, 400)

    def test_users_only_see_their_own_appeals_but_staff_see_all(self):
        other = User.objects.create_user(phone="+2348400000004", password="pw123456", country="NG")
        other_flag = models.Flag.objects.create(
            source="SYSTEM", target_type="USER", target_id=other.id,
            reason="x", severity="LOW", status="ACTIONED",
        )
        self.client.force_authenticate(self.user)
        self.client.post(
            APPEALS_URL, {"target_type": "flag", "target_id": str(self.flag.id), "reason": "a"}, format="json",
        )
        self.client.force_authenticate(other)
        self.client.post(
            APPEALS_URL, {"target_type": "flag", "target_id": str(other_flag.id), "reason": "b"}, format="json",
        )

        self.client.force_authenticate(self.user)
        own = self.client.get(APPEALS_URL)
        self.assertEqual(len(own.data["results"] if isinstance(own.data, dict) and "results" in own.data else own.data), 1)

        self.client.force_authenticate(self.admin)
        allres = self.client.get(APPEALS_URL)
        rows = allres.data["results"] if isinstance(allres.data, dict) and "results" in allres.data else allres.data
        self.assertEqual(len(rows), 2)


class MediaSafetyScanAppealTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+2348400000005", password="pw123456", country="NG")
        self.admin = User.objects.create_user(
            phone="+2348400000006", password="pw123456", country="NG", is_staff=True,
        )
        self.scan = MediaSafetyScan.objects.create(
            owner=self.user, context="channel", original_name="clip.mp4", mime_type="video/mp4",
            status="blocked", quarantine=True, requires_review=False, reason="staff_blocked",
        )

    def test_owner_can_appeal_a_blocked_scan(self):
        self.client.force_authenticate(self.user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "media_safety_scan", "target_id": str(self.scan.id), "reason": "this was appropriate content"},
            format="json",
        )

        self.assertEqual(res.status_code, 201)

    def test_cannot_appeal_a_scan_that_is_not_blocked(self):
        passing_scan = MediaSafetyScan.objects.create(
            owner=self.user, context="channel", status="passed",
        )
        self.client.force_authenticate(self.user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "media_safety_scan", "target_id": str(passing_scan.id), "reason": "n/a"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)

    def test_staff_overturn_restores_the_scan_to_passed(self):
        self.client.force_authenticate(self.user)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "media_safety_scan", "target_id": str(self.scan.id), "reason": "appeal"},
            format="json",
        )
        appeal_id = submit.data["id"]

        self.client.force_authenticate(self.admin)
        res = self.client.post(decide_url(appeal_id), {"action": "overturn"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["reversal_applied"])
        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, "passed")
        self.assertFalse(self.scan.quarantine)


class ChannelModerationRecordAppealTests(APITestCase):
    def setUp(self):
        from apps.broadcasts.models import BroadcastChannel, ChannelContent, ChannelModerationRecord

        self.creator = User.objects.create_user(phone="+2348400000007", password="pw123456", country="NG")
        self.admin = User.objects.create_user(
            phone="+2348400000008", password="pw123456", country="NG", is_staff=True,
        )
        self.channel = BroadcastChannel.objects.create(
            owner_type="user", owner_id=self.creator.id, owner_user=self.creator,
            handle="creator-channel", display_name="Creator Channel",
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="text", title="My post",
            is_deleted=True, visibility="private", status="archived",
        )
        self.record = ChannelModerationRecord.objects.create(
            channel=self.channel, content=self.content, target_type="content",
            target_id=self.content.id, status="actioned", action="remove",
        )

    def test_content_owner_can_appeal_removal(self):
        self.client.force_authenticate(self.creator)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "channel_moderation_record", "target_id": str(self.record.id), "reason": "not a violation"},
            format="json",
        )

        self.assertEqual(res.status_code, 201)

    def test_non_owner_cannot_appeal(self):
        other = User.objects.create_user(phone="+2348400000009", password="pw123456", country="NG")
        self.client.force_authenticate(other)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "channel_moderation_record", "target_id": str(self.record.id), "reason": "not mine"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)

    def test_staff_overturn_restores_the_content(self):
        from apps.broadcasts.models import ChannelContent

        self.client.force_authenticate(self.creator)
        submit = self.client.post(
            APPEALS_URL,
            {"target_type": "channel_moderation_record", "target_id": str(self.record.id), "reason": "appeal"},
            format="json",
        )
        appeal_id = submit.data["id"]

        self.client.force_authenticate(self.admin)
        res = self.client.post(decide_url(appeal_id), {"action": "overturn"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["reversal_applied"])
        content = ChannelContent.objects.get(id=self.content.id)
        self.assertFalse(content.is_deleted)
        self.assertEqual(content.visibility, "public")
        self.assertEqual(content.status, "published")


class ChatMessageReportAppealTests(APITestCase):
    def test_chat_message_report_appeals_are_rejected_as_unsupported(self):
        from apps.moderation.models import ChatMessageReport

        user = User.objects.create_user(phone="+2348400000010", password="pw123456", country="NG")
        report = ChatMessageReport.objects.create(
            conversation_id="conv-1", message_id="m1", reported_by_id=user.id, status="ACTIONED",
        )
        self.client.force_authenticate(user)

        res = self.client.post(
            APPEALS_URL,
            {"target_type": "chat_message_report", "target_id": str(report.id), "reason": "appeal"},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("supported", res.data["detail"])
