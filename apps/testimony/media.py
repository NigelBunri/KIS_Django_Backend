from rest_framework.exceptions import NotFound, ValidationError

from apps.media import upload_intent
from apps.media.models import MediaUploadIntent


def resolve_testimony_media(*, user, media_id) -> MediaUploadIntent:
    """Looks up a confirmed, not-yet-attached MediaUploadIntent owned by
    `user` for the testimony_media context. Never trusts a storage key or
    object id supplied by the client for anything other than this opaque
    `media_id` — mirrors apps.commerce.media_uploads.resolve_confirmed_media.

    Testimony media is publicly viewable once attached (can_view_testimony_
    media has no auth gate) - this was found completely unscanned during an
    AI-moderation coverage audit, the single worst combination in that audit
    (public + unscanned). The explicit-content check below closes that gap."""
    if not media_id:
        raise ValidationError({"mediaId": "mediaId is required."})
    intent = MediaUploadIntent.objects.filter(id=media_id, owner_id=user.id).first()
    if not intent:
        raise NotFound("Confirmed media not found.")
    if intent.context != "testimony_media":
        raise ValidationError({"mediaId": "This media was not uploaded for this purpose."})
    if intent.status != MediaUploadIntent.STATUS_CONFIRMED:
        raise ValidationError({"mediaId": f"This media is not confirmed (status={intent.status})."})
    if intent.attached_at is not None:
        raise ValidationError({"mediaId": "This media has already been attached and cannot be reused."})

    decision = upload_intent.run_and_record_explicit_content_scan(intent, upload_context="general")
    if decision.status == "blocked":
        intent.mark_failed("explicit_content_blocked", decision.user_message)
        raise ValidationError({"mediaId": decision.user_message})

    return intent
