from rest_framework import serializers

from .constants import PUBLIC_BADGE_LABELS, VerificationBadgeCode
from .models import (
    VerificationAuditEvent,
    VerificationBadge,
    VerificationCase,
    VerificationCheck,
    VerificationSubject,
)


RAW_EVIDENCE_KEYS = {
    "raw",
    "raw_document",
    "document_raw",
    "document_base64",
    "base64",
    "image_base64",
    "image_data",
    "document_data",
    "passport_image",
    "id_image",
    "selfie_image",
}


def validate_private_evidence_metadata(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("Evidence metadata must be an object of private media references.")
    _reject_raw_evidence(value)
    return value


def _reject_raw_evidence(value, path="evidence_metadata"):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in RAW_EVIDENCE_KEYS or "base64" in normalized_key:
                raise serializers.ValidationError({path: "Raw documents or base64 evidence are not allowed. Upload privately and send references only."})
            _reject_raw_evidence(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_evidence(child, f"{path}[{index}]")
    elif isinstance(value, str):
        stripped = value.strip().lower()
        if stripped.startswith(("data:image/", "data:application/", "data:video/", "data:audio/")) or len(value) > 5000:
            raise serializers.ValidationError({path: "Raw file data is not allowed. Use private media references only."})


class VerificationSubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationSubject
        fields = (
            "id",
            "subject_type",
            "subject_id",
            "owner",
            "display_name",
            "country",
            "metadata",
            "current_level",
            "current_status",
            "last_verified_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class VerificationCaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationCase
        fields = (
            "id",
            "subject",
            "requested_by",
            "reviewed_by",
            "level",
            "status",
            "provider",
            "provider_applicant_id",
            "provider_case_id",
            "provider_status",
            "risk_score",
            "evidence_metadata",
            "provider_payload",
            "reviewer_notes",
            "public_summary",
            "submitted_at",
            "reviewed_at",
            "expires_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class VerificationCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationCheck
        fields = (
            "id",
            "case",
            "check_type",
            "status",
            "provider",
            "provider_check_id",
            "confidence",
            "result_code",
            "result_summary",
            "evidence_metadata",
            "raw_result_metadata",
            "checked_at",
            "expires_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class VerificationBadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationBadge
        fields = (
            "id",
            "subject",
            "case",
            "code",
            "label",
            "level",
            "status",
            "public",
            "issued_by",
            "issued_at",
            "expires_at",
            "revoked_at",
            "revoke_reason",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class VerificationAuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationAuditEvent
        fields = (
            "id",
            "subject",
            "case",
            "actor",
            "action",
            "provider",
            "ip_address",
            "user_agent",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class StaffVerificationSubjectSummarySerializer(serializers.ModelSerializer):
    owner_label = serializers.SerializerMethodField()

    class Meta:
        model = VerificationSubject
        fields = (
            "id",
            "subject_type",
            "subject_id",
            "display_name",
            "country",
            "current_level",
            "current_status",
            "last_verified_at",
            "owner_label",
        )

    def get_owner_label(self, obj):
        owner = getattr(obj, "owner", None)
        if not owner:
            return ""
        return str(getattr(owner, "email", "") or getattr(owner, "phone", "") or getattr(owner, "username", "") or owner.pk)


def _metadata_shape(value):
    if not isinstance(value, dict):
        return {"type": type(value).__name__, "keys": [], "item_count": 0}
    item_count = 0
    for child in value.values():
        if isinstance(child, list):
            item_count += len(child)
        elif isinstance(child, dict):
            item_count += len(child)
        elif child not in (None, "", [], {}):
            item_count += 1
    return {
        "type": "object",
        "keys": sorted(str(key) for key in value.keys()),
        "item_count": item_count,
        "private_references_only": bool(value.get("private_references_only")),
    }


class StaffVerificationCaseSerializer(serializers.ModelSerializer):
    subject = StaffVerificationSubjectSummarySerializer(read_only=True)
    requested_by_label = serializers.SerializerMethodField()
    reviewed_by_label = serializers.SerializerMethodField()
    evidence_summary = serializers.SerializerMethodField()
    provider_payload_summary = serializers.SerializerMethodField()
    badges = serializers.SerializerMethodField()

    class Meta:
        model = VerificationCase
        fields = (
            "id",
            "subject",
            "requested_by_label",
            "reviewed_by_label",
            "level",
            "status",
            "provider",
            "provider_applicant_id",
            "provider_case_id",
            "provider_status",
            "risk_score",
            "evidence_summary",
            "provider_payload_summary",
            "reviewer_notes",
            "public_summary",
            "submitted_at",
            "reviewed_at",
            "expires_at",
            "created_at",
            "updated_at",
            "badges",
        )

    def get_requested_by_label(self, obj):
        user = getattr(obj, "requested_by", None)
        return str(getattr(user, "email", "") or getattr(user, "phone", "") or getattr(user, "username", "") or "") if user else ""

    def get_reviewed_by_label(self, obj):
        user = getattr(obj, "reviewed_by", None)
        return str(getattr(user, "email", "") or getattr(user, "phone", "") or getattr(user, "username", "") or "") if user else ""

    def get_evidence_summary(self, obj):
        return _metadata_shape(obj.evidence_metadata)

    def get_provider_payload_summary(self, obj):
        return _metadata_shape(obj.provider_payload)

    def get_badges(self, obj):
        return StaffVerificationBadgeSerializer(obj.badges.order_by("code"), many=True).data


class StaffVerificationBadgeSerializer(serializers.ModelSerializer):
    subject = StaffVerificationSubjectSummarySerializer(read_only=True)
    issued_by_label = serializers.SerializerMethodField()

    class Meta:
        model = VerificationBadge
        fields = (
            "id",
            "subject",
            "case",
            "code",
            "label",
            "level",
            "status",
            "public",
            "issued_by_label",
            "issued_at",
            "expires_at",
            "revoked_at",
            "revoke_reason",
            "metadata",
            "created_at",
            "updated_at",
        )

    def get_issued_by_label(self, obj):
        user = getattr(obj, "issued_by", None)
        return str(getattr(user, "email", "") or getattr(user, "phone", "") or getattr(user, "username", "") or "") if user else ""


class StaffVerificationAuditEventSerializer(serializers.ModelSerializer):
    subject = StaffVerificationSubjectSummarySerializer(read_only=True)
    actor_label = serializers.SerializerMethodField()

    class Meta:
        model = VerificationAuditEvent
        fields = (
            "id",
            "subject",
            "case",
            "actor_label",
            "action",
            "provider",
            "ip_address",
            "user_agent",
            "metadata",
            "created_at",
        )

    def get_actor_label(self, obj):
        actor = getattr(obj, "actor", None)
        return str(getattr(actor, "email", "") or getattr(actor, "phone", "") or getattr(actor, "username", "") or "") if actor else ""


class StaffBadgeIssueSerializer(serializers.Serializer):
    subject_type = serializers.ChoiceField(choices=VerificationSubject._meta.get_field("subject_type").choices)
    subject_id = serializers.UUIDField(required=False)
    case_id = serializers.UUIDField(required=False)
    code = serializers.ChoiceField(choices=tuple((code, label) for code, label in PUBLIC_BADGE_LABELS.items()))
    label = serializers.CharField(required=False, allow_blank=True, max_length=128)
    level = serializers.CharField(required=False, allow_blank=True, max_length=64)
    public = serializers.BooleanField(required=False, default=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate(self, attrs):
        if not attrs.get("case_id") and not attrs.get("subject_id"):
            raise serializers.ValidationError("Provide either case_id or subject_id.")
        return attrs


class StaffBadgeRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class StaffCaseStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=(
            ("in_review", "In review"),
            ("needs_more_info", "Needs more info"),
            ("cancelled", "Cancelled"),
            ("expired", "Expired"),
        )
    )
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class PublicVerificationBadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationBadge
        fields = ("code", "label", "level", "issued_at", "expires_at")


class UserVerificationStartSerializer(serializers.Serializer):
    level = serializers.ChoiceField(
        choices=(("identity_verified", "Identity verified"),),
        default="identity_verified",
        required=False,
    )
    provider = serializers.ChoiceField(
        choices=(("dojah", "Dojah"), ("sumsub", "Sumsub")),
        required=False,
        allow_blank=True,
    )
    evidence_metadata = serializers.JSONField(required=False)

    def validate_evidence_metadata(self, value):
        return validate_private_evidence_metadata(value)


class UserVerificationEvidenceSerializer(serializers.Serializer):
    evidence_metadata = serializers.JSONField(required=True)

    def validate_evidence_metadata(self, value):
        return validate_private_evidence_metadata(value)


class UserVerificationReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject"), ("needs_more_info", "Needs more info")))
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    badge_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=((VerificationBadgeCode.VERIFIED_USER, "Verified user"), (VerificationBadgeCode.ID_VERIFIED, "ID verified"))),
        required=False,
        allow_empty=True,
    )


class PartnerVerificationStartSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(
        choices=(("dojah", "Dojah"), ("sumsub", "Sumsub")),
        required=False,
        allow_blank=True,
    )
    evidence_metadata = serializers.JSONField(required=False)

    def validate_evidence_metadata(self, value):
        return validate_private_evidence_metadata(value)


class PartnerVerificationReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject"), ("needs_more_info", "Needs more info")))
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    badge_codes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=(
                (VerificationBadgeCode.VERIFIED_PARTNER, "Verified partner"),
                (VerificationBadgeCode.VERIFIED_ORGANIZATION, "Verified organization"),
                (VerificationBadgeCode.OFFICIAL_PARTNER, "Official partner"),
            )
        ),
        required=False,
        allow_empty=True,
    )


class HealthInstitutionVerificationStartSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(
        choices=(("dojah", "Dojah"), ("sumsub", "Sumsub")),
        required=False,
        allow_blank=True,
    )
    evidence_metadata = serializers.JSONField(required=False)

    def validate_evidence_metadata(self, value):
        return validate_private_evidence_metadata(value)


class HealthInstitutionVerificationReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject"), ("needs_more_info", "Needs more info")))
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    badge_codes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=(
                (VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION, "Verified health institution"),
                (VerificationBadgeCode.LICENSED_PROVIDER, "Licensed provider"),
            )
        ),
        required=False,
        allow_empty=True,
    )


class EducationInstitutionVerificationStartSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(
        choices=(("dojah", "Dojah"), ("sumsub", "Sumsub")),
        required=False,
        allow_blank=True,
    )
    evidence_metadata = serializers.JSONField(required=False)

    def validate_evidence_metadata(self, value):
        return validate_private_evidence_metadata(value)


class EducationInstitutionVerificationReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject"), ("needs_more_info", "Needs more info")))
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    badge_codes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=(
                (VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION, "Verified education institution"),
                (VerificationBadgeCode.ACCREDITED_EDUCATION, "Accredited education"),
            )
        ),
        required=False,
        allow_empty=True,
    )
