from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.media.models import MediaAsset
from apps.partners.models import (
    Partner,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerRole,
    PartnerRoleAssignment,
    PartnerSubscription,
)

from .models import Task, TaskActivityLog, TaskStatus


class TasksTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        ensure_default_account_tiers()

        self.owner = User.objects.create_user(phone="+237671100001", country="CM", password="pass1234")
        self.member = User.objects.create_user(phone="+237671100002", country="CM", password="pass1234")
        self.other_member = User.objects.create_user(phone="+237671100003", country="CM", password="pass1234")
        self.outsider = User.objects.create_user(phone="+237671100004", country="CM", password="pass1234")

        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Tasks Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(
            owner=self.owner, name="Tasks Partner", slug="tasks-partner", main_conversation=conversation,
        )

        partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        self.subscription = PartnerSubscription.objects.create(partner=self.partner, tier=partner_tier, status="active")
        # Personal tier deliberately left as Free — this whole feature is
        # gated by the ORG's plan, not the requesting staff member's own.
        Subscription.objects.filter(user=self.owner).delete()

        for user in (self.member, self.other_member):
            PartnerMembership.objects.create(partner=self.partner, user=user, role="member", status=PartnerMembershipStatus.MEMBER)

        channel_conversation = Conversation.objects.create(type=ConversationType.CHANNEL, created_by=self.owner)
        self.channel = Channel.objects.create(
            partner=self.partner, name="general", slug="general", owner=self.owner, conversation=channel_conversation,
        )

    def _create_task(self, **overrides):
        defaults = {
            "partner": self.partner, "channel": self.channel, "title": "Write the report",
            "created_by": self.owner, "assigned_to": self.member,
        }
        defaults.update(overrides)
        return Task.objects.create(**defaults)

    def _list_create_url(self):
        return f"/api/v1/partners/{self.partner.id}/channels/{self.channel.id}/tasks/"

    def _task_url(self, task, suffix=""):
        return f"/api/v1/tasks/{task.id}/{suffix}"


class TaskTierGateApiTests(TasksTestBase):
    def test_free_tier_organization_cannot_use_tasks(self):
        self.subscription.tier = AccountTier.objects.filter(name__iexact="Free").first()
        self.subscription.save(update_fields=["tier"])

        self.client.force_authenticate(self.owner)
        response = self.client.get(self._list_create_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_partner_tier_organization_can_use_tasks(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self._list_create_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TaskCreateAndPermissionsApiTests(TasksTestBase):
    def test_owner_can_create_and_assign_a_task(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._list_create_url(),
            {"title": "Design the flyer", "assigned_to_id": str(self.member.id), "priority": "high"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["status"], TaskStatus.NOT_STARTED)
        self.assertEqual(response.data["assigned_to"]["id"], str(self.member.id))
        task = Task.objects.get(id=response.data["id"])
        self.assertEqual(task.activity.filter(event_type=TaskActivityLog.EventType.ASSIGNED).count(), 1)

    def test_plain_member_cannot_create_a_task(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(self._list_create_url(), {"title": "Sneaky task"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_with_custom_role_can_create_tasks(self):
        role = PartnerRole.objects.create(partner=self.partner, name="Coordinator", permissions=["partner.tasks.manage"])
        PartnerRoleAssignment.objects.create(partner=self.partner, role=role, user=self.member, scope_type="global")
        self.client.force_authenticate(self.member)

        response = self.client.post(self._list_create_url(), {"title": "Coordinated task"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_outsider_cannot_view_channel_tasks(self):
        self.client.force_authenticate(self.outsider)

        response = self.client.get(self._list_create_url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_channel_member_can_list_tasks(self):
        self._create_task()
        self.client.force_authenticate(self.other_member)

        response = self.client.get(self._list_create_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["tasks"]), 1)


class TaskAssignApiTests(TasksTestBase):
    def test_owner_can_reassign_a_task(self):
        task = self._create_task(assigned_to=self.member)
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._task_url(task, "assign/"), {"assigned_to_id": str(self.other_member.id)}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to_id, self.other_member.id)
        self.assertEqual(
            task.activity.filter(event_type=TaskActivityLog.EventType.REASSIGNED).count(), 1,
        )

    def test_member_cannot_reassign_a_task(self):
        task = self._create_task(assigned_to=self.member)
        self.client.force_authenticate(self.member)

        response = self.client.post(
            self._task_url(task, "assign/"), {"assigned_to_id": str(self.other_member.id)}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskMemberWorkflowApiTests(TasksTestBase):
    def test_assignee_can_start_and_submit_task(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.NOT_STARTED)
        self.client.force_authenticate(self.member)

        start_response = self.client.post(self._task_url(task, "status/"), {"status": "in_progress"}, format="json")
        self.assertEqual(start_response.status_code, status.HTTP_200_OK, start_response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

        submit_response = self.client.post(
            self._task_url(task, "submit/"), {"note": "Done, see attached"}, format="json",
        )
        self.assertEqual(submit_response.status_code, status.HTTP_200_OK, submit_response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.SUBMITTED)
        self.assertIsNotNone(task.submitted_at)

    def test_non_assignee_cannot_submit_task(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.IN_PROGRESS)
        self.client.force_authenticate(self.other_member)

        response = self.client.post(self._task_url(task, "submit/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_submit_with_report_attachment(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.IN_PROGRESS)
        asset = MediaAsset.objects.create(
            owner=self.member, type="document", bucket_key="private/tasks/report/x.pdf",
            mime_type="application/pdf", bytes=1024, original_filename="report.pdf", status="ready",
        )
        self.client.force_authenticate(self.member)

        response = self.client.post(
            self._task_url(task, "submit/"), {"asset_ids": [str(asset.id)]}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data["attachments"]), 1)
        self.assertEqual(response.data["attachments"][0]["file_name"], "report.pdf")

    def test_cannot_submit_someone_elses_media_asset(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.IN_PROGRESS)
        asset = MediaAsset.objects.create(
            owner=self.other_member, type="document", bucket_key="private/tasks/report/y.pdf",
            mime_type="application/pdf", bytes=1024, original_filename="not-yours.pdf", status="ready",
        )
        self.client.force_authenticate(self.member)

        response = self.client.post(
            self._task_url(task, "submit/"), {"asset_ids": [str(asset.id)]}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TaskReviewWorkflowApiTests(TasksTestBase):
    def test_admin_can_move_through_review_states_to_completed(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.SUBMITTED)
        self.client.force_authenticate(self.owner)

        for target in ("under_review", "reviewed_pending", "completed"):
            response = self.client.post(self._task_url(task, "status/"), {"status": target}, format="json")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            task.refresh_from_db()
            self.assertEqual(task.status, target)

        self.assertIsNotNone(task.completed_at)

    def test_admin_can_send_task_back_for_redo(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.UNDER_REVIEW)
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            self._task_url(task, "status/"), {"status": "redo", "note": "Please redo section 2"}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REDO)
        self.assertEqual(task.review_note, "Please redo section 2")

    def test_assignee_can_restart_work_after_redo(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.REDO)
        self.client.force_authenticate(self.member)

        response = self.client.post(self._task_url(task, "status/"), {"status": "in_progress"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_member_cannot_mark_task_completed(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.SUBMITTED)
        self.client.force_authenticate(self.member)

        response = self.client.post(self._task_url(task, "status/"), {"status": "completed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskUndoApiTests(TasksTestBase):
    def test_undo_reverts_to_previous_status(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.SUBMITTED)
        self.client.force_authenticate(self.owner)
        self.client.post(self._task_url(task, "status/"), {"status": "under_review"}, format="json")

        response = self.client.post(self._task_url(task, "undo/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.SUBMITTED)

    def test_undo_from_completed_clears_completed_at(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.REVIEWED_PENDING)
        self.client.force_authenticate(self.owner)
        self.client.post(self._task_url(task, "status/"), {"status": "completed"}, format="json")

        response = self.client.post(self._task_url(task, "undo/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVIEWED_PENDING)
        self.assertIsNone(task.completed_at)

    def test_undo_unavailable_from_not_started(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.NOT_STARTED)
        self.client.force_authenticate(self.owner)

        response = self.client.post(self._task_url(task, "undo/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_cannot_undo(self):
        task = self._create_task(assigned_to=self.member, status=TaskStatus.SUBMITTED)
        self.client.force_authenticate(self.owner)
        self.client.post(self._task_url(task, "status/"), {"status": "under_review"}, format="json")
        self.client.force_authenticate(self.member)

        response = self.client.post(self._task_url(task, "undo/"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskDeleteApiTests(TasksTestBase):
    def test_owner_can_delete_a_task(self):
        task = self._create_task()
        self.client.force_authenticate(self.owner)

        response = self.client.delete(self._task_url(task))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        task.refresh_from_db()
        self.assertTrue(task.is_deleted)

    def test_deleted_task_is_not_returned_in_list(self):
        task = self._create_task()
        task.is_deleted = True
        task.save(update_fields=["is_deleted"])
        self.client.force_authenticate(self.owner)

        response = self.client.get(self._list_create_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["tasks"]), 0)

    def test_member_cannot_delete_a_task(self):
        task = self._create_task()
        self.client.force_authenticate(self.member)

        response = self.client.delete(self._task_url(task))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskCommentApiTests(TasksTestBase):
    def test_channel_member_can_comment(self):
        task = self._create_task(assigned_to=self.member)
        self.client.force_authenticate(self.other_member)

        response = self.client.post(self._task_url(task, "comments/"), {"body": "Looks good so far"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(task.comments.count(), 1)

    def test_outsider_cannot_comment(self):
        task = self._create_task(assigned_to=self.member)
        self.client.force_authenticate(self.outsider)

        response = self.client.post(self._task_url(task, "comments/"), {"body": "Hi"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TaskMineAndSummaryApiTests(TasksTestBase):
    def test_my_tasks_only_returns_assignees_own_tasks(self):
        self._create_task(assigned_to=self.member, title="Mine")
        self._create_task(assigned_to=self.other_member, title="Not mine")
        self.client.force_authenticate(self.member)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/tasks/mine/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["tasks"]), 1)
        self.assertEqual(response.data["tasks"][0]["title"], "Mine")

    def test_summary_counts_by_status(self):
        self._create_task(status=TaskStatus.NOT_STARTED)
        self._create_task(status=TaskStatus.COMPLETED)
        self._create_task(status=TaskStatus.COMPLETED)
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/partners/{self.partner.id}/tasks/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["counts"]["completed"], 2)
        self.assertEqual(response.data["counts"]["not_started"], 1)
        self.assertEqual(response.data["total"], 3)
