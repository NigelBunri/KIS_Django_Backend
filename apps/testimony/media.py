from rest_framework.exceptions import NotFound, ValidationError

from apps.media.models import MediaUploadIntent


def resolve_testimony_media(*, user, media_id) -> MediaUploadIntent:
    """Looks up a confirmed, not-yet-attached MediaUploadIntent owned by
    `user` for the testimony_media context. Never trusts a storage key or
    object id supplied by the client for anything other than this opaque
    `media_id` — mirrors apps.commerce.media_uploads.resolve_confirmed_media."""
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
    return intent
