# core/serializers.py
import uuid
from datetime import datetime
from typing import Any, Dict

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from rest_framework import serializers

from . import models

# Helper: default user model (accounts app provides it)
from django.conf import settings
UserModel = apps.get_model(settings.AUTH_USER_MODEL)


# ---------------------------
# GenericRelatedField
# ---------------------------
class GenericRelatedField(serializers.Field):
    """
    Serializes GenericForeignKey as:
      {"type": "app_label.ModelName", "id": "<pk>"}
    Accepts same structure for deserialization. If `allow_null=True` and value is null,
    returns None.

    Example:
      {"type":"accounts.User","id":"1234-..."}
    """
    def to_representation(self, value):
        if value is None:
            return None
        # value is model instance
        ct = ContentType.objects.get_for_model(value.__class__)
        return {"type": f"{ct.app_label}.{ct.model.capitalize()}", "id": str(getattr(value, "pk"))}

    def to_internal_value(self, data):
        # Accept None
        if data is None:
            return None

        if not isinstance(data, dict):
            raise serializers.ValidationError("Generic reference must be an object with 'type' and 'id'.")

        type_str = data.get("type")
        obj_id = data.get("id")

        if not type_str or not obj_id:
            raise serializers.ValidationError("Both 'type' and 'id' are required for generic references.")

        # type_str may be "app_label.ModelName" or "app_label.modelname"
        try:
            app_label, model_name = type_str.split(".", 1)
        except ValueError:
            raise serializers.ValidationError("Invalid 'type' format. Use 'app_label.ModelName'.")

        # normalize model name to lower for ContentType lookup
        ct = ContentType.objects.filter(app_label=app_label, model=model_name.lower()).first()
        if not ct:
            raise serializers.ValidationError(f"Unknown content type '{type_str}'.")

        model_class = ct.model_class()
        if model_class is None:
            raise serializers.ValidationError(f"Model class for '{type_str}' could not be resolved.")

        try:
            obj = model_class.objects.get(pk=obj_id)
        except ObjectDoesNotExist:
            raise serializers.ValidationError(f"Referenced object '{type_str}' with id '{obj_id}' not found.")

        return obj


# ---------------------------
# Short / Utilities serializers
# ---------------------------
class IDSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Permission
        fields = ["id", "codename", "description", "created_at", "updated_at"]


class RoleShortSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(many=True, slug_field="codename", queryset=models.Permission.objects.all())

    class Meta:
        model = models.Role
        fields = ["id", "name", "scope", "is_default", "permissions"]


# ---------------------------
# RoleAssignment serializer
# ---------------------------
class RoleAssignmentSerializer(serializers.ModelSerializer):
    principal = GenericRelatedField(allow_null=False)
    target = GenericRelatedField(allow_null=True)

    role = serializers.PrimaryKeyRelatedField(queryset=models.Role.objects.all())

    class Meta:
        model = models.RoleAssignment
        fields = ["id", "role", "principal", "target", "expires_at", "created_at", "updated_at"]

    def create(self, validated_data):
        principal_obj = validated_data.pop("principal")
        target_obj = validated_data.pop("target", None)
        role = validated_data.pop("role")

        principal_ct = ContentType.objects.get_for_model(principal_obj.__class__)
        principal_id = str(getattr(principal_obj, "pk"))

        if target_obj is None:
            target_ct = None
            target_id = None
        else:
            target_ct = ContentType.objects.get_for_model(target_obj.__class__)
            target_id = str(getattr(target_obj, "pk"))

        ra = models.RoleAssignment.objects.create(
            role=role,
            principal_content_type=principal_ct,
            principal_object_id=principal_id,
            target_content_type=target_ct,
            target_object_id=target_id,
            expires_at=validated_data.get("expires_at"),
        )
        return ra

    def update(self, instance, validated_data):
        # allow changing expires_at and role, but not principal/target
        instance.role = validated_data.get("role", instance.role)
        instance.expires_at = validated_data.get("expires_at", instance.expires_at)
        instance.save()
        return instance


# ---------------------------
# AccessControlEntry serializer
# ---------------------------
class AccessControlEntrySerializer(serializers.ModelSerializer):
    principal = GenericRelatedField(allow_null=True)  # null + principal_object_id="PUBLIC" indicates public
    target = GenericRelatedField(allow_null=True)

    # allow permissions as list of strings
    permissions = serializers.ListField(child=serializers.CharField(), allow_empty=False)

    effect = serializers.ChoiceField(choices=models.AccessControlEntry.EFFECT_CHOICES)

    class Meta:
        model = models.AccessControlEntry
        fields = ["id", "principal", "target", "permissions", "effect", "expires_at", "created_at", "updated_at"]

    def to_internal_ace(self, validated_data):
        """
        Helper to build normalized ACE fields (content types and ids) from input.
        """
        principal_obj = validated_data.pop("principal", None)
        target_obj = validated_data.pop("target", None)
        permissions = validated_data.pop("permissions", [])
        effect = validated_data.pop("effect", models.AccessControlEntry.EFFECT_ALLOW)
        expires_at = validated_data.pop("expires_at", None)

        if principal_obj is None:
            principal_ct = None
            principal_id = "PUBLIC"
        else:
            principal_ct = ContentType.objects.get_for_model(principal_obj.__class__)
            principal_id = str(getattr(principal_obj, "pk"))

        if target_obj is None:
            target_ct = None
            target_id = None
        else:
            target_ct = ContentType.objects.get_for_model(target_obj.__class__)
            target_id = str(getattr(target_obj, "pk"))

        return {
            "principal_ct": principal_ct,
            "principal_id": principal_id,
            "target_ct": target_ct,
            "target_id": target_id,
            "permissions": sorted(set(map(str, permissions))),
            "effect": effect,
            "expires_at": expires_at,
        }

    def create(self, validated_data):
        ace_data = self.to_internal_ace(validated_data)
        ace = models.AccessControlEntry.objects.create(
            principal_content_type=ace_data["principal_ct"],
            principal_object_id=ace_data["principal_id"],
            target_content_type=ace_data["target_ct"],
            target_object_id=ace_data["target_id"],
            permissions=ace_data["permissions"],
            effect=ace_data["effect"],
            expires_at=ace_data["expires_at"],
        )
        return ace

    def update(self, instance, validated_data):
        ace_data = self.to_internal_ace(validated_data)
        instance.principal_content_type = ace_data["principal_ct"]
        instance.principal_object_id = ace_data["principal_id"]
        instance.target_content_type = ace_data["target_ct"]
        instance.target_object_id = ace_data["target_id"]
        instance.permissions = ace_data["permissions"]
        instance.effect = ace_data["effect"]
        instance.expires_at = ace_data["expires_at"]
        instance.save()
        return instance


# ---------------------------
# Community / Group / Channel serializers
# ---------------------------
class GroupSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.GroupSettings
        exclude = ("id", "group", "created_at", "updated_at")


class ChannelSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ChannelSettings
        exclude = ("id", "channel", "created_at", "updated_at")


class MembershipShortSerializer(serializers.ModelSerializer):
    user = GenericRelatedField()
    role = RoleShortSerializer(read_only=True)

    class Meta:
        model = models.Membership
        fields = ["id", "user", "role", "status", "joined_at", "expires_at", "is_moderator"]


class CommunitySerializer(serializers.ModelSerializer):
    owner = GenericRelatedField(allow_null=True)
    metadata = serializers.JSONField(required=False)
    groups = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = models.Community
        fields = ["id", "slug", "name", "description", "owner", "visibility", "metadata", "archived", "groups", "created_at", "updated_at"]

    def create(self, validated_data):
        # owner is model instance from GenericRelatedField
        owner_obj = validated_data.pop("owner", None)
        if owner_obj is not None:
            owner_ct = ContentType.objects.get_for_model(owner_obj.__class__)
            owner_id = str(getattr(owner_obj, "pk"))
            validated_data["owner_content_type"] = owner_ct
            validated_data["owner_object_id"] = owner_id
        return super().create(validated_data)

    def update(self, instance, validated_data):
        owner_obj = validated_data.pop("owner", None) if "owner" in validated_data else None
        if owner_obj is not None:
            instance.owner_content_type = ContentType.objects.get_for_model(owner_obj.__class__)
            instance.owner_object_id = str(getattr(owner_obj, "pk"))
        return super().update(instance, validated_data)


class GroupSerializer(serializers.ModelSerializer):
    community = serializers.PrimaryKeyRelatedField(queryset=models.Community.objects.all(), allow_null=True, required=False)
    settings = GroupSettingsSerializer(read_only=True)
    memberships = MembershipShortSerializer(many=True, read_only=True)
    metadata = serializers.JSONField(required=False)

    class Meta:
        model = models.Group
        fields = [
            "id", "slug", "name", "description", "community", "is_public", "archived",
            "member_count", "metadata", "settings", "memberships", "created_at", "updated_at"
        ]
        read_only_fields = ["member_count", "settings", "memberships"]

    def create(self, validated_data):
        # create group + attach default GroupSettings if not provided
        with transaction.atomic():
            group = super().create(validated_data)
            # create default settings if not present
            if not hasattr(group, "settings"):
                models.GroupSettings.objects.create(group=group)
            return group

    def update(self, instance, validated_data):
        # normal update; settings managed via separate endpoint
        return super().update(instance, validated_data)


class ChannelSerializer(serializers.ModelSerializer):
    communities = serializers.PrimaryKeyRelatedField(queryset=models.Community.objects.all(), many=True, required=False)
    groups = serializers.PrimaryKeyRelatedField(queryset=models.Group.objects.all(), many=True, required=False)
    settings = ChannelSettingsSerializer(read_only=True)
    metadata = serializers.JSONField(required=False)

    class Meta:
        model = models.Channel
        fields = [
            "id", "slug", "name", "description", "is_public", "archived",
            "communities", "groups", "metadata", "settings", "created_at", "updated_at"
        ]
        read_only_fields = ["settings"]

    def create(self, validated_data):
        comms = validated_data.pop("communities", [])
        groups = validated_data.pop("groups", [])
        with transaction.atomic():
            ch = super().create(validated_data)
            if comms:
                ch.communities.set(comms)
            if groups:
                ch.groups.set(groups)
            # ensure settings exist
            if not hasattr(ch, "settings"):
                models.ChannelSettings.objects.create(channel=ch)
            return ch

    def update(self, instance, validated_data):
        comms = validated_data.pop("communities", None)
        groups = validated_data.pop("groups", None)
        with transaction.atomic():
            ch = super().update(instance, validated_data)
            if comms is not None:
                ch.communities.set(comms)
            if groups is not None:
                ch.groups.set(groups)
            return ch


# ---------------------------
# Membership-related serializers
# ---------------------------
class MembershipSerializer(serializers.ModelSerializer):
    user = GenericRelatedField()
    role = serializers.PrimaryKeyRelatedField(queryset=models.Role.objects.all(), allow_null=True, required=False)
    group = serializers.PrimaryKeyRelatedField(queryset=models.Group.objects.all())

    class Meta:
        model = models.Membership
        fields = [
            "id", "group", "user", "role", "status", "joined_at", "expires_at", "is_moderator", "preferences", "created_at", "updated_at"
        ]
        read_only_fields = ["joined_at", "created_at", "updated_at"]

    def validate(self, data):
        # If status is ACTIVE then joined_at must be set (it will be automatically)
        status = data.get("status", None)
        if status == models.Membership.STATUS_ACTIVE and not data.get("joined_at"):
            data["joined_at"] = timezone.now()
        return data

    def create(self, validated_data):
        user_obj = validated_data.pop("user")
        group = validated_data.get("group")
        role = validated_data.get("role", None)

        user_ct = ContentType.objects.get_for_model(user_obj.__class__)
        user_id = str(getattr(user_obj, "pk"))

        with transaction.atomic():
            # ensure unique membership constraint is respected by using get_or_create
            mem, created = models.Membership.objects.update_or_create(
                group=group,
                user_content_type=user_ct,
                user_object_id=user_id,
                defaults={
                    "role": role,
                    "status": validated_data.get("status", models.Membership.STATUS_ACTIVE),
                    "joined_at": validated_data.get("joined_at", timezone.now()),
                    "expires_at": validated_data.get("expires_at", None),
                    "is_moderator": validated_data.get("is_moderator", False),
                    "preferences": validated_data.get("preferences", {}),
                }
            )
            # update group's member_count
            group.recalc_member_count()
            return mem

    def update(self, instance, validated_data):
        # update membership fields; recalc group member_count if status changes
        prev_active = instance.is_active()
        instance.role = validated_data.get("role", instance.role)
        instance.status = validated_data.get("status", instance.status)
        instance.expires_at = validated_data.get("expires_at", instance.expires_at)
        instance.is_moderator = validated_data.get("is_moderator", instance.is_moderator)
        instance.preferences = validated_data.get("preferences", instance.preferences)
        instance.save()
        post_active = instance.is_active()
        if prev_active != post_active:
            instance.group.recalc_member_count()
        return instance


class MembershipInviteSerializer(serializers.ModelSerializer):
    group = serializers.PrimaryKeyRelatedField(queryset=models.Group.objects.all(), allow_null=True, required=False)
    community = serializers.PrimaryKeyRelatedField(queryset=models.Community.objects.all(), allow_null=True, required=False)
    created_by = GenericRelatedField(allow_null=True)

    class Meta:
        model = models.MembershipInvite
        fields = [
            "id", "token", "group", "community", "created_by", "expires_at", "max_uses", "uses", "created_at", "updated_at"
        ]
        read_only_fields = ["uses", "created_at", "updated_at", "token"]

    def validate(self, attrs):
        # must have either group or community (or neither for global invite) but not both simultaneously in some setups.
        if not attrs.get("group") and not attrs.get("community"):
            # allow global invites — you may prefer to require at least one
            pass
        # validate max_uses positive if provided
        max_uses = attrs.get("max_uses", None)
        if max_uses is not None and max_uses <= 0:
            raise serializers.ValidationError({"max_uses": "max_uses must be positive or null for unlimited."})
        return attrs

    def create(self, validated_data):
        # create token if not provided
        token = validated_data.get("token") or uuid.uuid4().hex
        created_by = validated_data.pop("created_by", None)
        if created_by is not None:
            cbt = ContentType.objects.get_for_model(created_by.__class__)
            created_by_ct = cbt
            created_by_id = str(getattr(created_by, "pk"))
        else:
            created_by_ct = None
            created_by_id = None

        invite = models.MembershipInvite.objects.create(
            token=token,
            group=validated_data.get("group", None),
            community=validated_data.get("community", None),
            created_by_content_type=created_by_ct,
            created_by_object_id=created_by_id,
            expires_at=validated_data.get("expires_at", None),
            max_uses=validated_data.get("max_uses", None),
        )
        return invite


class MembershipRequestSerializer(serializers.ModelSerializer):
    group = serializers.PrimaryKeyRelatedField(queryset=models.Group.objects.all())
    user = GenericRelatedField()
    reviewed_by = GenericRelatedField(allow_null=True, required=False)

    class Meta:
        model = models.MembershipRequest
        fields = [
            "id", "group", "user", "message", "status", "reviewed_by", "created_at"
        ]
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        # ensure user not already member
        user_obj = attrs.get("user")
        group = attrs.get("group")
        user_ct = ContentType.objects.get_for_model(user_obj.__class__)
        exists = models.Membership.objects.filter(group=group, user_content_type=user_ct, user_object_id=str(getattr(user_obj, "pk"))).exists()
        if exists:
            raise serializers.ValidationError("User is already a member of the group.")
        return attrs

    def create(self, validated_data):
        return super().create(validated_data)


# ---------------------------
# ModerationAction serializer
# ---------------------------
class ModerationActionSerializer(serializers.ModelSerializer):
    target = GenericRelatedField()
    subject = GenericRelatedField()
    performed_by = GenericRelatedField(allow_null=True)

    class Meta:
        model = models.ModerationAction
        fields = [
            "id", "target", "subject", "action", "reason", "performed_by", "expires_at", "created_at", "updated_at"
        ]


# ---------------------------
# Settings serializers (update only)
# ---------------------------
class GroupSettingsUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.GroupSettings
        fields = "__all__"
        read_only_fields = ("group", "id", "created_at", "updated_at")


class ChannelSettingsUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ChannelSettings
        fields = "__all__"
        read_only_fields = ("channel", "id", "created_at", "updated_at")


# ---------------------------
# Top-level convenient serializers for admin / read-heavy endpoints
# ---------------------------
class GroupDetailSerializer(GroupSerializer):
    # extend group serializer with nested data for detail endpoints
    community = CommunitySerializer(read_only=True)
    settings = GroupSettingsSerializer(read_only=True)
    memberships = MembershipShortSerializer(many=True, read_only=True)


class ChannelDetailSerializer(ChannelSerializer):
    communities = CommunitySerializer(many=True, read_only=True)
    groups = GroupSerializer(many=True, read_only=True)
    settings = ChannelSettingsSerializer(read_only=True)


# ---------------------------
# Healthcare serializers
# ---------------------------

class HealthcareOrganizationSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)
    verified_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.HealthcareOrganization
        fields = [
            "id",
            "tenant_id",
            "name",
            "slug",
            "org_type",
            "status",
            "region",
            "metadata",
            "owner",
            "is_deleted",
            "onboarding_status",
            "onboarding_metadata",
            "verified_by",
            "last_status_updated_at",
            "document_expiry",
            "compliance_officer",
            "risk_summary",
            "security_notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")

    def _request_user(self):
        request = self.context.get("request")
        return request.user if request and getattr(request, "user", None) else None

    def validate_onboarding_status(self, value):
        allowed = {choice[0] for choice in models.HealthcareOrganization.ONBOARDING_STATUS_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError("Invalid onboarding status.")
        return value

    def update(self, instance, validated_data):
        request_user = self._request_user()
        old_status = instance.onboarding_status
        instance = super().update(instance, validated_data)
        new_status = validated_data.get("onboarding_status")
        if new_status and new_status != old_status:
            instance.last_status_updated_at = timezone.now()
            instance.save(update_fields=["last_status_updated_at"])
            self._log_status_change(instance, request_user, old_status, new_status)
        return instance

    def _log_status_change(self, instance, user, from_status, to_status):
        if not hasattr(models, "ComplianceAuditLog"):
            return
        actor = user if getattr(user, "is_authenticated", False) else None
        ct = ContentType.objects.get_for_model(instance.__class__)
        models.ComplianceAuditLog.objects.create(
            actor=actor,
            action="onboarding.status_change",
            target_type=ct,  # type: ignore[arg-type]
            target_id=str(instance.pk),
            severity="info",
            metadata={"from": from_status, "to": to_status},
        )


class StaffProfileSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.StaffProfile
        fields = [
            "id",
            "profile",
            "user",
            "role",
            "scope",
            "permissions",
            "licenses",
            "shifts",
            "is_on_call",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Location
        fields = [
            "id",
            "organization",
            "profile",
            "label",
            "address",
            "is_primary",
            "timezone",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class WardSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Ward
        fields = [
            "id",
            "location",
            "name",
            "capacity",
            "is_isolation",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Service
        fields = [
            "id",
            "profile",
            "department",
            "name",
            "category",
            "description",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class EquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Equipment
        fields = [
            "id",
            "profile",
            "ward",
            "name",
            "equipment_type",
            "status",
            "last_service_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class StaffAuditSerializer(serializers.ModelSerializer):
    staff = serializers.PrimaryKeyRelatedField(queryset=models.StaffProfile.objects.all())
    actor = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.StaffAuditLog
        fields = ["id", "staff", "actor", "action", "metadata", "note", "created_at"]
        read_only_fields = ("id", "created_at")


class ComplianceAuditLogSerializer(serializers.ModelSerializer):
    actor = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ComplianceAuditLog
        fields = [
            "id",
            "actor",
            "action",
            "target_type",
            "target_id",
            "severity",
            "metadata",
            "created_at",
        ]
        read_only_fields = ("id", "created_at")


class CredentialVerificationSerializer(serializers.ModelSerializer):
    staff_profile = serializers.PrimaryKeyRelatedField(queryset=models.StaffProfile.objects.all())
    verified_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.CredentialVerification
        fields = [
            "id",
            "staff_profile",
            "credential_type",
            "license_number",
            "issuing_body",
            "status",
            "issued_at",
            "expires_at",
            "verified_by",
            "verified_at",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class RegulatoryReportSerializer(serializers.ModelSerializer):
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all())
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all())

    class Meta:
        model = models.RegulatoryReport
        fields = [
            "id",
            "report_type",
            "profile",
            "organization",
            "period_start",
            "period_end",
            "status",
            "data_payload",
            "submitted_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ComplianceDocumentSerializer(serializers.ModelSerializer):
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all())
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all())
    signed_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ComplianceDocument
        fields = [
            "id",
            "profile",
            "organization",
            "document_name",
            "file_path",
            "status",
            "is_signed",
            "signed_by",
            "signed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class DataAccessConsentSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())

    class Meta:
        model = models.DataAccessConsent
        fields = [
            "id",
            "patient",
            "granted_to",
            "scope",
            "status",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Department
        fields = ["id", "profile", "name", "is_ward", "services", "created_at", "updated_at"]
        read_only_fields = ("id", "created_at", "updated_at")


class MedicalProfileSerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all())
    created_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)
    onboarding_status = serializers.ChoiceField(choices=models.HealthcareOrganization.ONBOARDING_STATUS_CHOICES, required=False)
    compliance_documents_metadata = serializers.JSONField(required=False)
    audit_entries = serializers.JSONField(required=False)
    review_notes = serializers.CharField(allow_blank=True, required=False)

    class Meta:
        model = models.MedicalProfile
        fields = [
            "id",
            "organization",
            "profile_type",
            "name",
            "slug",
            "status",
            "onboarding_status",
            "compliance_documents_metadata",
            "audit_entries",
            "review_notes",
            "metadata",
            "location",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class MedicalProfileDetailSerializer(MedicalProfileSerializer):
    departments = DepartmentSerializer(many=True, read_only=True)
    staff_profiles = StaffProfileSerializer(many=True, read_only=True)

    class Meta(MedicalProfileSerializer.Meta):
        fields = MedicalProfileSerializer.Meta.fields + ["departments", "staff_profiles"]


class HealthcareOrganizationDetailSerializer(HealthcareOrganizationSerializer):
    profiles = MedicalProfileSerializer(many=True, read_only=True)

    class Meta(HealthcareOrganizationSerializer.Meta):
        fields = HealthcareOrganizationSerializer.Meta.fields + ["profiles"]


class PatientMasterRecordSerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.PatientMasterRecord
        fields = [
            "id",
            "tenant_id",
            "mrn",
            "first_name",
            "last_name",
            "dob",
            "gender",
            "primary_contact",
            "emergency_contact",
            "family",
            "metadata",
            "status",
            "organization",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class FamilyProfileSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())

    class Meta:
        model = models.PatientFamilyProfile
        fields = ["id", "patient", "relationship", "members", "notes", "created_at", "updated_at"]
        read_only_fields = ("id", "created_at", "updated_at")


class ConsentRecordSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    granted_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ConsentRecord
        fields = [
            "id",
            "patient",
            "purpose",
            "consent_text",
            "granted_by",
            "granted_at",
            "expires_at",
            "metadata",
        ]
        read_only_fields = ("id",)


class EncounterSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all(), allow_null=True, required=False)
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)
    clinician = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.EncounterNote
        fields = [
            "id",
            "patient",
            "organization",
            "profile",
            "clinician",
            "encounter_type",
            "summary",
            "notes",
            "ai_insights",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class AppointmentSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.Appointment
        fields = [
            "id",
            "patient",
            "profile",
            "scheduled_at",
            "status",
            "queue_position",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class PatientMasterRecordDetailSerializer(PatientMasterRecordSerializer):
    consents = ConsentRecordSerializer(many=True, read_only=True)
    family_profiles = FamilyProfileSerializer(many=True, read_only=True)
    encounters = EncounterSerializer(many=True, read_only=True)
    appointments = AppointmentSerializer(many=True, read_only=True)

    class Meta(PatientMasterRecordSerializer.Meta):
        fields = PatientMasterRecordSerializer.Meta.fields + [
            "consents",
            "family_profiles",
            "encounters",
            "appointments",
        ]


class MedicationOrderSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)
    clinician = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.MedicationOrder
        fields = [
            "id",
            "patient",
            "profile",
            "clinician",
            "encounter",
            "drug_name",
            "dosage",
            "route",
            "frequency",
            "duration",
            "notes",
            "status",
            "fhir_resource",
            "ai_insights",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["fhir"] = instance.fhir_resource or {
            "resourceType": "MedicationRequest",
            "status": instance.status,
            "medicationCodeableConcept": {"text": instance.drug_name},
            "subject": str(instance.patient_id),
        }
        return data


class InventoryItemSerializer(serializers.ModelSerializer):
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all())
    organization = serializers.PrimaryKeyRelatedField(queryset=models.HealthcareOrganization.objects.all())

    class Meta:
        model = models.InventoryItem
        fields = [
            "id",
            "profile",
            "organization",
            "name",
            "category",
            "sku",
            "unit",
            "quantity_on_hand",
            "reorder_level",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class DiagnosticOrderSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)
    requested_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.DiagnosticOrder
        fields = [
            "id",
            "patient",
            "profile",
            "requested_by",
            "test_name",
            "status",
            "specimen_collected_at",
            "results",
            "completed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ImagingStudySerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ImagingStudy
        fields = [
            "id",
            "patient",
            "profile",
            "modality",
            "body_region",
            "scheduled_at",
            "status",
            "results_summary",
            "result_files",
            "completed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class MedicationAdherenceReminderSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    medication_order = serializers.PrimaryKeyRelatedField(queryset=models.MedicationOrder.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.MedicationAdherenceReminder
        fields = [
            "id",
            "patient",
            "medication_order",
            "scheduled_at",
            "status",
            "channel",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class SupplyForecastSerializer(serializers.ModelSerializer):
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all())

    class Meta:
        model = models.SupplyForecast
        fields = [
            "id",
            "profile",
            "category",
            "period_start",
            "period_end",
            "predicted_usage",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ClinicalTaskSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)
    created_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ClinicalTask
        fields = [
            "id",
            "patient",
            "title",
            "description",
            "assigned_to",
            "created_by",
            "status",
            "priority",
            "due_at",
            "completed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "completed_at")


class EmergencyEscalationSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    reported_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.EmergencyEscalation
        fields = [
            "id",
            "patient",
            "reported_by",
            "severity",
            "status",
            "summary",
            "metadata",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "resolved_at")


class TriageRecordSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    created_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.TriageRecord
        fields = [
            "id",
            "patient",
            "created_by",
            "symptoms",
            "acuity_level",
            "recommended_unit",
            "ai_response",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ReferralRouteSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    from_organization = serializers.PrimaryKeyRelatedField(
        queryset=models.HealthcareOrganization.objects.all(),
        allow_null=True,
        required=False,
    )
    to_organization = serializers.PrimaryKeyRelatedField(
        queryset=models.HealthcareOrganization.objects.all(),
        allow_null=True,
        required=False,
    )
    created_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ReferralRoute
        fields = [
            "id",
            "patient",
            "from_organization",
            "to_organization",
            "created_by",
            "reason",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class ClinicalEventLogSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())
    triggered_by = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.ClinicalEventLog
        fields = [
            "id",
            "patient",
            "event_type",
            "description",
            "triggered_by",
            "task",
            "escalation",
            "triage",
            "referral",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class AllergyRecordSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())

    class Meta:
        model = models.AllergyRecord
        fields = [
            "id",
            "patient",
            "agent",
            "category",
            "severity",
            "reaction",
            "status",
            "recorded_at",
            "metadata",
        ]
        read_only_fields = ("id",)


class VitalSignSerializer(serializers.ModelSerializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all())

    class Meta:
        model = models.VitalSign
        fields = [
            "id",
            "patient",
            "profile",
            "clinician",
            "vital_type",
            "value",
            "units",
            "recorded_at",
            "notes",
            "metadata",
        ]
        read_only_fields = ("id", "recorded_at")

class TelemedicineSessionSerializer(serializers.ModelSerializer):
    profile = serializers.PrimaryKeyRelatedField(queryset=models.MedicalProfile.objects.all(), allow_null=True, required=False)
    patient = serializers.PrimaryKeyRelatedField(queryset=models.PatientMasterRecord.objects.all(), allow_null=True, required=False)
    clinician = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)
    appointment = serializers.PrimaryKeyRelatedField(queryset=models.Appointment.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.TelemedicineSession
        fields = [
            "id",
            "profile",
            "patient",
            "clinician",
            "appointment",
            "status",
            "started_at",
            "ended_at",
            "recording_url",
            "notes",
            "reminder_sent",
            "reminder_sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "reminder_sent_at")


class TelemedicineDeviceSerializer(serializers.ModelSerializer):
    session = serializers.PrimaryKeyRelatedField(queryset=models.TelemedicineSession.objects.all())

    class Meta:
        model = models.TelemedicineDevice
        fields = ["id", "session", "device_id", "device_type", "metadata", "last_seen", "created_at", "updated_at"]
        read_only_fields = ("id", "created_at", "updated_at")


class VoiceDictationSerializer(serializers.ModelSerializer):
    session = serializers.PrimaryKeyRelatedField(queryset=models.TelemedicineSession.objects.all(), allow_null=True, required=False)
    clinician = serializers.PrimaryKeyRelatedField(queryset=UserModel.objects.all(), allow_null=True, required=False)

    class Meta:
        model = models.VoiceDictation
        fields = [
            "id",
            "session",
            "clinician",
            "audio_metadata",
            "transcript",
            "groq_job",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


# ---------------------------
# Final notes for use
# ---------------------------
"""
Notes & tips:
- GenericRelatedField expects the referenced object to exist. In most endpoints you'll pass:
    {"type":"accounts.User","id":"<user_pk>"}
  or for a role:
    {"type":"core.Role","id":"<role_uuid>"}

- For create endpoints you may prefer to accept simpler shapes for user references (e.g., user_id)
  — if so, make small wrapper serializers or override .to_internal_value to accept ints/uuids for
  specific content types such as accounts.User.

- Use separate endpoints for RoleAssignment and ACE management, and use the serializers above
  to validate/serialize data. Be sure to secure these endpoints heavily (only admins allowed).

- You can add view-level permission classes that call the domain object's `.can_user(user, permission)` methods
  before allowing create/update/delete operations.

"""
