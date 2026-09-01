# moderation/models.py
from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid


def uuid4():
    return uuid.uuid4()


class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class Flag(BaseEntity):
    SOURCE_CHOICES = [("USER", "User"), ("SYSTEM", "System"), ("AI", "AI")]
    TARGET_TYPES = [("POST", "Post"), ("COMMENT", "Comment"), ("GROUP", "Group"), ("CHANNEL", "Channel"), ("USER", "User"), ("STATUS", "Status")]
    SEVERITY = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")]
    STATUS = [("PENDING", "Pending"), ("REVIEWED", "Reviewed"), ("ACTIONED", "Actioned"), ("DISMISSED", "Dismissed")]
    ESCALATION = [("AUTO", "Auto"), ("MODERATOR", "Moderator"), ("ADMIN", "Admin")]

    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    target_type = models.CharField(max_length=32, choices=TARGET_TYPES)
    target_id = models.UUIDField()
    reporter_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField()
    severity = models.CharField(max_length=16, choices=SEVERITY)
    status = models.CharField(max_length=16, choices=STATUS, default="PENDING")
    ai_score = models.FloatField(null=True, blank=True)
    escalation_level = models.CharField(max_length=16, choices=ESCALATION, default="AUTO")
    tags = models.JSONField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class ChatMessageReport(BaseEntity):
    """
    A user report against a NestJS chat message. Chat messages live
    entirely in Nest's Mongo (message._id is a Mongo ObjectId, not a UUID),
    so this can't reuse Flag.target_id (a strict UUIDField) the way every
    other report type does - conversation_id/message_id are plain strings
    instead. Created via apps.chat.views_introspect.ChatMessageReportView,
    called by Nest's ModerationController.report() right after it writes
    its own local MessageReport - previously that Mongo write was the only
    place a chat report was recorded, so it never reached this app's staff
    queue (StaffModerationOperationsQueueView) at all; a GO/staff moderator
    reviewing reports had no way to see a chat message had been reported.
    """
    STATUS = [("PENDING", "Pending"), ("REVIEWED", "Reviewed"), ("ACTIONED", "Actioned"), ("DISMISSED", "Dismissed")]

    conversation_id = models.CharField(max_length=64, db_index=True)
    message_id = models.CharField(max_length=64, db_index=True)
    reported_by_id = models.UUIDField()
    reason = models.CharField(max_length=64, blank=True, default="")
    note = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS, default="PENDING")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("conversation_id", "message_id", "reported_by_id")]


class ModerationAppeal(BaseEntity):
    """
    A user's contest of a moderation decision made against them. No appeal
    mechanism existed anywhere in the system before this - a warned/
    suspended user, a creator whose content was taken down, or an uploader
    whose media was blocked had no way to ask for a human to reconsider.

    target_type/target_id point at the ORIGINAL decision record (a Flag,
    MediaSafetyScan, or ChannelModerationRecord row - all real Django
    UUIDs, unlike Flag.target_id's cross-service ambiguity), not at the
    underlying content. See apps.moderation.services.decide_appeal for who
    is allowed to appeal each target_type and what "overturned" actually
    does - deliberately NOT every target_type supports appeal yet (chat
    message reports don't record who sent the reported message, so there's
    no way to verify an appellant is the affected party - rejected with a
    clear error rather than silently allowing anyone to appeal).
    """
    TARGET_TYPES = [
        ("flag", "Flag"),
        ("media_safety_scan", "Media safety scan"),
        ("channel_moderation_record", "Channel moderation record"),
        ("chat_message_report", "Chat message report"),
    ]
    STATUS = [
        ("PENDING", "Pending"),
        ("UNDER_REVIEW", "Under review"),
        ("UPHELD", "Upheld"),
        ("OVERTURNED", "Overturned"),
    ]

    target_type = models.CharField(max_length=32, choices=TARGET_TYPES)
    target_id = models.UUIDField()
    appellant_id = models.UUIDField()
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS, default="PENDING")
    decided_by_id = models.UUIDField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    # Honest record of whether overturning this appeal actually undid the
    # original consequence (restored content, lifted a suspension) versus
    # only flipping this row's own status - see decide_appeal. Set at
    # decision time; never claim a reversal happened if it didn't.
    reversal_applied = models.BooleanField(default=False)

    class Meta:
        # A duplicate PENDING appeal for the same decision by the same
        # person is blocked; a fresh appeal is allowed again once the
        # first one is actually resolved (status has moved off PENDING).
        unique_together = [("target_type", "target_id", "appellant_id", "status")]


class ModerationAction(BaseEntity):
    ACTIONS = [("WARN", "Warn"), ("SUSPEND", "Suspend"), ("DELETE", "Delete"),
               ("BAN", "Ban"), ("TEMP_RESTRICT", "Temporary Restrict"), ("ESCALATE", "Escalate"),
               ("REINSTATE", "Reinstate")]

    flag = models.ForeignKey(Flag, related_name="actions", on_delete=models.CASCADE)
    action = models.CharField(max_length=32, choices=ACTIONS)
    notes = models.TextField(blank=True)
    performed_by_id = models.UUIDField()
    scheduled_action_at = models.DateTimeField(null=True, blank=True)
    auto_generated = models.BooleanField(default=False)


class AuditLog(BaseEntity):
    actor_id = models.UUIDField()
    action = models.CharField(max_length=128)
    target_type = models.CharField(max_length=32)
    target_id = models.UUIDField()
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    device_info = models.TextField(null=True, blank=True)


class UserReputation(BaseEntity):
    user_id = models.UUIDField(unique=True)
    score = models.FloatField(default=100.0)
    flags_received = models.IntegerField(default=0)
    actions_taken = models.IntegerField(default=0)
    last_updated = models.DateTimeField(default=timezone.now)


class UserBlock(BaseEntity):
    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocks_made",
        on_delete=models.CASCADE,
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocked_by",
        on_delete=models.CASCADE,
    )
    reason = models.TextField(blank=True)

    class Meta:
        unique_together = [("blocker", "blocked")]
        indexes = [
            models.Index(fields=["blocker", "blocked"]),
            models.Index(fields=["blocker", "created_at"]),
        ]


class ModerationRule(BaseEntity):
    target_type = models.CharField(max_length=32)
    condition_json = models.JSONField(default=dict, blank=True)
    action_json = models.JSONField(default=dict, blank=True)
    escalation_json = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)


class SafetyAlert(BaseEntity):
    ALERT_TYPES = [("HIGH_SEVERITY", "High Severity"), ("LEGAL", "Legal"), ("COMMUNITY_RISK", "Community Risk")]
    flag = models.ForeignKey(Flag, null=True, blank=True, on_delete=models.SET_NULL)
    alert_type = models.CharField(max_length=32, choices=ALERT_TYPES)
    message = models.TextField()
    sent_to_ids = models.JSONField(default=list, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
