import json

from rest_framework import serializers

from apps.accounts.models import UserContact
from apps.media.models import MediaSafetyScan
from apps.media.safety import (
    USER_SAFE_REVIEW_MESSAGE,
    hash_upload,
    scan_upload_for_explicit_content,
    validate_upload_file_safety,
)
from apps.statuses.models import (
    StatusAudienceTarget,
    StatusItem,
    StatusReplyPermission,
    StatusType,
    StatusVisibility,
)


class StatusItemSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    viewed = serializers.SerializerMethodField()
    reply_allowed = serializers.SerializerMethodField()
    audience_user_ids = serializers.SerializerMethodField()
    view_count = serializers.SerializerMethodField()
    viewed_by_preview = serializers.SerializerMethodField()

    class Meta:
        model = StatusItem
        fields = [
            "id",
            "type",
            "text",
            "style",
            "file_url",
            "duration_ms",
            "visibility",
            "reply_permission",
            "audience_user_ids",
            "view_count",
            "viewed_by_preview",
            "created_at",
            "expires_at",
            "viewed",
            "reply_allowed",
        ]

    def get_file_url(self, obj: StatusItem) -> str | None:
        if not obj.file:
            return None
        request = self.context.get("request")
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def get_viewed(self, obj: StatusItem) -> bool:
        viewed_ids = self.context.get("viewed_ids")
        if not viewed_ids:
            return False
        return str(obj.id) in viewed_ids

    def get_reply_allowed(self, obj: StatusItem) -> bool:
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
            return False
        if obj.user_id == request.user.id:
            return False
        if obj.reply_permission == StatusReplyPermission.NOBODY:
            return False
        mutual_contact_ids = self.context.get("mutual_contact_ids") or set()
        return str(obj.user_id) in {str(value) for value in mutual_contact_ids}

    def get_audience_user_ids(self, obj: StatusItem) -> list[str]:
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or obj.user_id != request.user.id:
            return []
        targets = getattr(obj, "_prefetched_objects_cache", {}).get("audience_targets")
        if targets is not None:
            return [str(target.target_user_id) for target in targets]
        return [str(value) for value in obj.audience_targets.values_list("target_user_id", flat=True)]

    def get_view_count(self, obj: StatusItem) -> int:
        annotated = getattr(obj, "view_count", None)
        if annotated is not None:
            return int(annotated)
        return int(obj.views.count())

    def get_viewed_by_preview(self, obj: StatusItem) -> list[dict]:
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or obj.user_id != request.user.id:
            return []
        preview = self.context.get("viewed_by_preview") or {}
        rows = preview.get(str(obj.id)) or []
        return rows


class StatusCreateSerializer(serializers.ModelSerializer):
    target_user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )
    allowed_user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )
    excluded_user_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = StatusItem
        fields = [
            "id",
            "type",
            "text",
            "file",
            "style",
            "duration_ms",
            "visibility",
            "reply_permission",
            "target_user_ids",
            "allowed_user_ids",
            "excluded_user_ids",
            "created_at",
            "expires_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "expires_at",
        ]

    def validate(self, attrs):
        status_type = attrs.get("type")
        text = (attrs.get("text") or "").strip()
        file = attrs.get("file")
        style = attrs.get("style")
        visibility = attrs.get("visibility") or StatusVisibility.CONTACTS
        target_user_ids = attrs.get("target_user_ids") or []

        if isinstance(style, str):
            try:
                attrs["style"] = json.loads(style)
            except Exception:
                raise serializers.ValidationError({"style": "Invalid style payload."})

        def parse_id_list(raw_value):
            if raw_value is None:
                return []
            if isinstance(raw_value, (list, tuple)):
                return list(raw_value)
            if isinstance(raw_value, str):
                try:
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
                return [token.strip() for token in raw_value.split(",") if token.strip()]
            return []

        raw_target_aliases = [
            self.initial_data.get("target_user_ids"),
            self.initial_data.get("audience_user_ids"),
        ]
        if visibility == StatusVisibility.ONLY_SHARE_WITH:
            raw_target_aliases.extend([
                self.initial_data.get("allowed_user_ids"),
                self.initial_data.get("only_user_ids"),
                self.initial_data.get("only_share_with_user_ids"),
            ])
        elif visibility == StatusVisibility.CONTACTS_EXCEPT:
            raw_target_aliases.extend([
                self.initial_data.get("excluded_user_ids"),
                self.initial_data.get("except_user_ids"),
                self.initial_data.get("contacts_except_user_ids"),
            ])

        merged_targets = []
        for raw_targets in raw_target_aliases:
            merged_targets.extend(parse_id_list(raw_targets))
        if merged_targets:
            attrs["target_user_ids"] = merged_targets
            target_user_ids = merged_targets

        if status_type == StatusType.TEXT and not text:
            raise serializers.ValidationError({"text": "Text status requires text."})
        if status_type != StatusType.TEXT and not file:
            raise serializers.ValidationError({"file": "Media status requires a file."})
        if file:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            validate_upload_file_safety(file, context="status")
            checksum = hash_upload(file)
            decision = scan_upload_for_explicit_content(
                filename=getattr(file, "name", "status-upload"),
                mime_type=getattr(file, "content_type", "") or "",
                context="status",
            )
            MediaSafetyScan.objects.create(
                owner=user if user and user.is_authenticated else None,
                context="status",
                original_name=getattr(file, "name", "") or "",
                mime_type=getattr(file, "content_type", "") or "",
                bytes=getattr(file, "size", 0) or 0,
                checksum=checksum,
                provider=decision.provider,
                status=decision.status,
                quarantine=decision.quarantine,
                requires_review=decision.requires_review,
                policy_version=decision.policy_version,
                reason=decision.reason,
                result={
                    **decision.as_metadata(),
                    "surface": "messaging_status",
                },
            )
            if decision.quarantine or decision.requires_review or decision.status in {"blocked", "failed", "pending_review"}:
                raise serializers.ValidationError({"file": decision.user_message or USER_SAFE_REVIEW_MESSAGE})
        if visibility in (StatusVisibility.CONTACTS_EXCEPT, StatusVisibility.ONLY_SHARE_WITH) and not target_user_ids:
            raise serializers.ValidationError(
                {"target_user_ids": "Choose at least one contact for this audience setting."}
            )
        if visibility == StatusVisibility.CONTACTS and target_user_ids:
            attrs["target_user_ids"] = []

        request = self.context.get("request")
        user = getattr(request, "user", None)
        normalized_target_ids = []
        for value in attrs.get("target_user_ids") or []:
            normalized_target_ids.append(str(value))
        attrs["target_user_ids"] = list(dict.fromkeys(normalized_target_ids))

        if user and user.is_authenticated and attrs["target_user_ids"]:
            allowed_ids = set(
                str(value)
                for value in UserContact.objects.filter(
                    user=user,
                    contact_user__isnull=False,
                ).values_list("contact_user_id", flat=True)
            )
            invalid = [value for value in attrs["target_user_ids"] if value not in allowed_ids]
            if invalid:
                raise serializers.ValidationError(
                    {"target_user_ids": "Audience targets must be registered contacts."}
                )
        return attrs

    def create(self, validated_data):
        target_user_ids = validated_data.pop("target_user_ids", [])
        validated_data.pop("allowed_user_ids", None)
        validated_data.pop("excluded_user_ids", None)
        item = super().create(validated_data)
        if target_user_ids:
            StatusAudienceTarget.objects.bulk_create(
                [
                    StatusAudienceTarget(status=item, target_user_id=target_user_id)
                    for target_user_id in target_user_ids
                ],
                ignore_conflicts=True,
            )
        return item
