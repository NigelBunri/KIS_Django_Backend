# apps/broadcasts/education_media.py
"""
Education-specific wiring on top of the shared direct-to-S3 presigned
upload handshake (apps/media/upload_intent.py). Mirrors the confirm-then-
attach pattern in apps/commerce/media_uploads.py and the content-safety
gate in apps/statuses/status_media.py:

  1. initiate_education_upload() — validates `context` and authorizes the
     institution (owner/manager/administrator membership, same rule as
     every other institution-management endpoint) BEFORE a presigned URL
     is ever issued.
  2. POST /api/v1/media/uploads/<uuid:upload_id>/confirm/ (existing,
     unmodified route) verifies the S3 object and returns a stable
     `mediaId`.
  3. resolve_and_scan_education_media() — re-validates institution
     ownership (defense in depth), then runs the same
     scan_upload_for_explicit_content() gate status/complaint uploads run,
     recording a MediaSafetyScan audit row. Callers decide what "blocked"
     means for their field (education's existing convention, preserved
     here rather than replaced, is to blank the field + set a
     `blocked_user_message`, not a hard rejection — see
     apps.broadcasts.views._education_material_media_payload).
  4. bind_education_media() — thin wrapper around
     apps.media.services.lifecycle.sync_attachment(), called once the
     real target row exists (institution/program/course/.../material),
     exactly like commerce's attach_*() functions do.

Queries EducationInstitution/EducationInstitutionMembership directly
(rather than importing the equivalent helpers from apps.broadcasts.views)
to avoid a circular import — apps.broadcasts.views imports this module.
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.media import upload_intent
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.media.safety import MediaSafetyDecision, scan_upload_for_explicit_content
from apps.media.services import lifecycle

from .models import (
    EducationInstitution,
    EducationInstitutionMembershipRole,
    EducationInstitutionMembershipStatus,
)

LOGO_CONTEXT = "education_institution_logo"
COVER_IMAGE_CONTEXT = "education_module_cover_image"
MATERIAL_CONTEXT = "education_material"

_VALID_CONTEXTS = {LOGO_CONTEXT, COVER_IMAGE_CONTEXT, MATERIAL_CONTEXT}

_MANAGE_ROLES = {
    EducationInstitutionMembershipRole.OWNER,
    EducationInstitutionMembershipRole.MANAGER,
    EducationInstitutionMembershipRole.ADMINISTRATOR,
}


def _user_can_manage_institution(user, institution: EducationInstitution) -> bool:
    if institution.owner_id == user.id:
        return True
    membership = institution.memberships.filter(user=user).first()
    if not membership or membership.status != EducationInstitutionMembershipStatus.ACTIVE:
        return False
    return membership.role in _MANAGE_ROLES


def _get_managed_institution(user, institution_id: str) -> EducationInstitution:
    institution = EducationInstitution.objects.filter(id=institution_id).first()
    if not institution:
        raise NotFound("Institution not found.")
    if not _user_can_manage_institution(user, institution):
        raise PermissionDenied("You do not have permission to manage this institution.")
    return institution


# --------------------------------------------------------------------------
# Initiate
# --------------------------------------------------------------------------

def initiate_education_upload(
    *,
    user,
    context: str,
    filename: str,
    content_type: str,
    size_bytes,
    institution_id: str | None = None,
) -> dict:
    if context not in _VALID_CONTEXTS:
        raise ValidationError({"context": "Unknown or unsupported education upload context."})

    institution = None
    if institution_id:
        institution = _get_managed_institution(user, institution_id)
    elif context != LOGO_CONTEXT:
        # Every other context always has a pre-existing institution to
        # authorize against. Logo uploads are the one exception — mirrors
        # apps.commerce.media_uploads._resolve_target_for_initiate's
        # "brand new shop" case: a user uploading a logo while creating
        # their first institution has no institution id yet, so there is
        # nothing to authorize against at initiate time. The institution's
        # own creation flow (EducationInstitutionListView.post) is what
        # actually gates this feature (tier limits etc).
        raise ValidationError({"institutionId": "institutionId is required."})

    upload_intent.validate_initiate_payload(
        context=context, filename=filename, content_type=content_type, size_bytes=size_bytes,
    )
    intent = upload_intent.create_upload_intent(
        user=user,
        context=context,
        filename=filename,
        content_type=str(content_type).strip().lower(),
        size_bytes=int(size_bytes),
        target_id=str(institution.id) if institution else "",
    )
    return upload_intent.build_initiate_response(intent)


# --------------------------------------------------------------------------
# Resolve confirmed media + content-safety gate (attach time)
# --------------------------------------------------------------------------

def resolve_and_scan_education_media(
    *,
    user,
    institution: EducationInstitution | None,
    media_id,
    expected_context: str,
) -> tuple[MediaUploadIntent, MediaSafetyDecision]:
    """Looks up a confirmed, not-yet-attached MediaUploadIntent owned by
    `user` for the expected context, verifies it was initiated for THIS
    institution (attach-time can be minutes after initiate-time, and a user
    may manage more than one institution), then runs the same explicit-
    content gate status/complaint uploads run. Never trusts a URL, storage
    key, or object id supplied directly by the client — only this opaque
    `media_id`, resolved server-side."""
    intent = upload_intent.resolve_confirmed_intent(
        user=user, media_id=media_id, expected_context=expected_context,
    )
    if institution is not None and intent.target_id and intent.target_id != str(institution.id):
        raise ValidationError({"mediaId": "This media was not uploaded for this institution."})

    decision = scan_upload_for_explicit_content(
        filename=intent.original_filename or "education-upload",
        mime_type=intent.content_type or "",
        context="education",
    )
    MediaSafetyScan.objects.create(
        owner=user if user and user.is_authenticated else None,
        context="education",
        original_name=intent.original_filename or "",
        mime_type=intent.content_type or "",
        bytes=intent.size_bytes or 0,
        checksum="",
        provider=decision.provider,
        status=decision.status,
        quarantine=decision.quarantine,
        requires_review=decision.requires_review,
        policy_version=decision.policy_version,
        reason=decision.reason,
        result={**decision.as_metadata(), "surface": expected_context},
        upload_id=str(intent.id),
    )
    return intent, decision


def is_blocked(decision: MediaSafetyDecision) -> bool:
    return bool(
        decision.quarantine
        or decision.requires_review
        or decision.status in {"blocked", "failed", "pending_review"}
    )


# --------------------------------------------------------------------------
# Bind (attach time, after the real target row exists)
# --------------------------------------------------------------------------

def bind_education_media(*, intent: MediaUploadIntent, target_type: str, target_id: str) -> None:
    lifecycle.sync_attachment(intent=intent, target_type=target_type, target_id=target_id)
