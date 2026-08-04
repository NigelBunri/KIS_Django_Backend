# apps/media/signing.py
#
# Shared signed-token helpers for MediaAssetViewSet's proxy-download path
# (MediaAssetViewSet.sign/.download in views.py) and the trusted-internal
# chat-voice sign endpoint (services/chat_voice_playback.py). Pulled out of
# views.py into their own module so services/ can import them without
# creating a views.py <-> services/ circular import (views.py already
# imports from services/ at the bottom of the file for the same reason).
#
# MEDIA_SIGNED_URL_TTL_SECONDS was temporarily raised to 10 days as a stopgap
# while voice notes had no refresh path for a non-owner recipient (see the
# KIS Universal Media Platform voice-note investigation). That gap is now
# closed: apps.media.services.chat_voice_playback + Nest's
# GET /chat/messages/:messageId/voice/playback-url give a receiver a real,
# authorized way to get a fresh URL at playback time, so this reverts to a
# genuinely short default — a long-lived link is no longer needed and is a
# larger blast radius if one ever leaks into a log or gets forwarded.
import os

from django.core import signing

MEDIA_SIGNED_URL_TTL_SECONDS = int(os.environ.get("MEDIA_SIGNED_URL_TTL_SECONDS", "900"))


def sign_media_asset_token(asset) -> str:
    signer = signing.TimestampSigner(salt="kis-media-download")
    return signer.sign(str(asset.id))


def token_allows_asset(token: str | None, asset) -> bool:
    if not token:
        return False
    signer = signing.TimestampSigner(salt="kis-media-download")
    try:
        value = signer.unsign(token, max_age=MEDIA_SIGNED_URL_TTL_SECONDS)
    except signing.BadSignature:
        return False
    except signing.SignatureExpired:
        return False
    return str(value) == str(asset.id)
