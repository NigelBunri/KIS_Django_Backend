# Owner app -> public website preview bridge. Modeled 1:1 on
# apps.media.signing's TimestampSigner pattern — a short-lived, single-
# purpose signed token, not a session or cookie. Lets an owner see their
# own draft/unpublished website content on the real public URL without
# any new login/session infrastructure (Phase 1 explicitly excludes a
# full web session system — see the website-builder plan's Context).
import os

from django.core import signing

WEBSITE_PREVIEW_TOKEN_TTL_SECONDS = int(os.environ.get("WEBSITE_PREVIEW_TOKEN_TTL_SECONDS", "900"))
_SALT = "kis-website-preview"


def sign_website_preview_token(website_id, user_id) -> str:
    signer = signing.TimestampSigner(salt=_SALT)
    return signer.sign(f"{website_id}:{user_id}")


def verify_website_preview_token(token: str | None, website_id) -> bool:
    """True if `token` is a still-valid preview token for this specific
    website_id. Doesn't need to return the user id — callers already know
    which website they're serving; this only needs to answer "may this
    request see draft content for THIS site"."""
    if not token:
        return False
    signer = signing.TimestampSigner(salt=_SALT)
    try:
        value = signer.unsign(token, max_age=WEBSITE_PREVIEW_TOKEN_TTL_SECONDS)
    except (signing.BadSignature, signing.SignatureExpired):
        return False
    signed_website_id, _, _signed_user_id = value.partition(":")
    return signed_website_id == str(website_id)
