# apps/bots/serializers.py
from rest_framework import serializers

from .models import (
    Bot,
    BotActivityLog,
    BotAIConnection,
    BotAIUsageLog,
    BotApprovalRequest,
    BotCredential,
    BotKnowledgeSource,
    BotMemoryConfig,
    BotMemoryEntry,
    BotResourceGrant,
    BotWebhookDelivery,
    BotWebhookSubscription,
    BotWorkflow,
    BotWorkflowRun,
    BotWorkflowVersion,
)


class BotAIConnectionSerializer(serializers.ModelSerializer):
    # Write-only: owner can set a new key, but it is never read back once
    # stored (section 10 — "AI credentials must remain protected
    # server-side"). has_api_key tells the owner whether one is on file
    # without ever exposing it.
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.SerializerMethodField()

    class Meta:
        model = BotAIConnection
        fields = [
            "id", "provider", "api_key", "has_api_key", "custom_endpoint_url",
            "model", "system_instructions", "daily_request_limit", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_has_api_key(self, obj) -> bool:
        return bool(obj.api_key_encrypted)


class BotCredentialSerializer(serializers.ModelSerializer):
    client_secret = serializers.SerializerMethodField()

    class Meta:
        model = BotCredential
        fields = [
            "id", "client_id", "client_secret", "scopes", "is_active",
            "created_at", "rotated_at", "revoked_at", "last_used_at",
        ]
        read_only_fields = fields

    def get_client_secret(self, obj):
        # Only present immediately after issue/rotate (views.py passes it
        # via serializer context) - never derivable from the stored hash.
        return self.context.get("plaintext_secret")


class BotResourceGrantSerializer(serializers.ModelSerializer):
    resource_display_name = serializers.SerializerMethodField()
    granted_by_display_name = serializers.SerializerMethodField()
    is_currently_valid = serializers.SerializerMethodField()

    class Meta:
        model = BotResourceGrant
        fields = [
            "id", "resource_type", "resource_id", "resource_display_name", "conversation_id", "scopes",
            "auto_approved_actions", "is_active", "is_currently_valid", "granted_by", "granted_by_display_name",
            "created_at", "updated_at", "revoked_at",
        ]
        read_only_fields = ["id", "conversation_id", "granted_by", "created_at", "updated_at", "revoked_at", "is_active"]

    def get_resource_display_name(self, obj) -> str:
        from .services import resolve_resource_display_name

        return resolve_resource_display_name(obj.resource_type, obj.resource_id)

    def get_granted_by_display_name(self, obj):
        if not obj.granted_by_id:
            return None
        return obj.granted_by.display_name or obj.granted_by.username or obj.granted_by.phone

    def get_is_currently_valid(self, obj) -> bool:
        from .services import _grant_is_currently_valid

        return _grant_is_currently_valid(obj)


class BotWebhookSubscriptionSerializer(serializers.ModelSerializer):
    secret = serializers.SerializerMethodField()

    class Meta:
        model = BotWebhookSubscription
        fields = [
            "id", "url", "events", "secret", "is_active", "retry_limit",
            "retry_backoff_seconds", "last_sent_at", "last_error", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_sent_at", "last_error", "created_at", "updated_at"]

    def get_secret(self, obj):
        # Shown once at creation/rotation via serializer context, same
        # posture as BotCredentialSerializer's client_secret above.
        reveal = self.context.get("reveal_secret")
        return obj.secret if reveal else None


class BotWebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = BotWebhookDelivery
        fields = [
            "id", "event", "event_id", "status", "attempt_count", "response_code",
            "error_message", "next_retry_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class BotActivityLogSerializer(serializers.ModelSerializer):
    actor_display_name = serializers.SerializerMethodField()

    class Meta:
        model = BotActivityLog
        fields = [
            "id", "action", "resource_type", "resource_id", "actor",
            "actor_display_name", "metadata", "created_at",
        ]
        read_only_fields = fields

    def get_actor_display_name(self, obj):
        return getattr(obj.actor, "display_name", None) if obj.actor_id else None


class BotAIUsageLogSerializer(serializers.ModelSerializer):
    workflow_name = serializers.SerializerMethodField()

    class Meta:
        model = BotAIUsageLog
        fields = [
            "id", "workflow", "workflow_name", "workflow_run", "provider", "model", "status",
            "selected_indices", "error_message", "prompt_tokens", "completion_tokens",
            "latency_ms", "created_at",
        ]
        read_only_fields = fields

    def get_workflow_name(self, obj):
        return obj.workflow.name if obj.workflow_id else None


class BotApprovalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotApprovalRequest
        fields = [
            "id", "bot", "resource_grant", "action", "details", "status",
            "decided_by", "decided_at", "expires_at", "created_at",
        ]
        read_only_fields = fields


class BotSerializer(serializers.ModelSerializer):
    has_credential = serializers.SerializerMethodField()
    has_webhook = serializers.SerializerMethodField()
    has_ai_connection = serializers.SerializerMethodField()
    resource_count = serializers.SerializerMethodField()

    class Meta:
        model = Bot
        fields = [
            "id", "owner", "partner", "name", "handle", "avatar_url", "description",
            "kind", "status", "config", "created_at", "updated_at",
            "has_credential", "has_webhook", "has_ai_connection", "resource_count",
        ]
        read_only_fields = ["id", "owner", "handle", "status", "created_at", "updated_at"]

    def get_has_credential(self, obj) -> bool:
        return hasattr(obj, "credential") and obj.credential.is_active

    def get_has_webhook(self, obj) -> bool:
        return hasattr(obj, "webhook_subscription") and obj.webhook_subscription.is_active

    def get_has_ai_connection(self, obj) -> bool:
        return hasattr(obj, "ai_connection") and obj.ai_connection.is_active

    def get_resource_count(self, obj) -> int:
        return obj.resource_grants.filter(is_active=True).count()


class BotCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    avatar_url = serializers.URLField(required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(choices=[c[0] for c in Bot._meta.get_field("kind").choices])
    partner_id = serializers.UUIDField(required=False, allow_null=True)


class BotWorkflowSerializer(serializers.ModelSerializer):
    resource_grant_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = BotWorkflow
        fields = [
            "id", "bot", "resource_grant_id", "name", "description", "status", "version",
            "trigger_type", "trigger_config", "definition",
            "created_at", "updated_at", "published_at", "last_run_at",
        ]
        read_only_fields = ["id", "bot", "status", "version", "created_at", "updated_at", "published_at", "last_run_at"]


class BotWorkflowCreateSerializer(serializers.Serializer):
    resource_grant_id = serializers.UUIDField()
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    trigger_type = serializers.ChoiceField(
        choices=[c[0] for c in BotWorkflow._meta.get_field("trigger_type").choices],
    )
    trigger_config = serializers.JSONField(required=False, default=dict)
    definition = serializers.JSONField(required=False, default=dict)


class BotWorkflowUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    trigger_type = serializers.ChoiceField(
        choices=[c[0] for c in BotWorkflow._meta.get_field("trigger_type").choices], required=False,
    )
    trigger_config = serializers.JSONField(required=False)
    definition = serializers.JSONField(required=False)


class BotWorkflowVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotWorkflowVersion
        fields = ["id", "version", "trigger_type", "trigger_config", "definition", "created_at", "created_by"]
        read_only_fields = fields


class BotWorkflowRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotWorkflowRun
        fields = [
            "id", "workflow", "workflow_version", "trigger_event", "status",
            "event_context", "condition_result", "steps_log", "error_message",
            "started_at", "finished_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------
# Phase 4 — Knowledge and Memory
# ---------------------------------------------------------------------

class BotKnowledgeSourceSerializer(serializers.ModelSerializer):
    chunk_count = serializers.SerializerMethodField()

    class Meta:
        model = BotKnowledgeSource
        fields = [
            "id", "source_type", "title", "status", "error_message",
            "file_size_bytes", "content_type", "source_url",
            "chunk_count", "created_at", "updated_at", "deactivated_at",
        ]
        read_only_fields = fields

    def get_chunk_count(self, obj) -> int:
        return obj.chunks.count()


class BotKnowledgeSourceCreateSerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(choices=["file", "url", "text"])
    title = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    url = serializers.URLField(required=False, allow_blank=True, default="")
    text = serializers.CharField(required=False, allow_blank=True, default="")


class BotMemoryConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotMemoryConfig
        fields = ["enabled", "allow_message_content", "retention_days", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class BotMemoryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = BotMemoryEntry
        fields = ["id", "subject_user_id", "content", "source", "created_at", "expires_at"]
        read_only_fields = fields
