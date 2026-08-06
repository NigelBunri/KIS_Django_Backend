# apps/accounts/views_internal.py
"""
Trusted-internal-service endpoints for apps/accounts. Callers here are other
KIS backend services (currently only Nest), never end-user devices directly
— authorization is `apps.chat.internal_auth.require_internal_auth` (the same
HMAC-signed internal-token mechanism apps/media, apps/broadcasts, and
apps/notifications already reuse for their own Nest<->Django calls), not a
Django user session.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.internal_auth import require_internal_auth

from .models import ProfilePreferences


class NotificationPreferencesInternalView(APIView):
    """GET /api/v1/profile-preferences/internal/notification-prefs/?user_id=<uuid>

    Lets Nest fetch a user's notification preferences (category mutes, DND
    quiet hours) before sending a chat/call push, without a per-user JWT —
    Nest is acting as a trusted service here, not as that user.

    This closes a real gap: the previously-only route for this,
    GET /api/v1/profile-preferences/me/, requires DeviceBoundJWTAuthentication
    (a real, currently-valid user access token). Nest's caller
    (django-user-prefs.client.ts) has never sent one — it only sent internal
    HMAC headers — so that call has always returned 401 there, meaning the
    chat/call preference check has never actually been enforced in
    production; every push has gone out as if no preference was ever set
    (see DjangoUserPrefsClient's warn-and-return-null catch, which made the
    failure silent-looking even though it was logged). This endpoint is the
    fix: real HMAC-verified internal auth, matching the exact pattern
    ChatVoicePlaybackSignView (apps/media/views_internal.py) already
    established for a different Nest->Django call.

    Response shape intentionally matches what /me/ already returns under
    `notification_preferences`, so the Nest-side reader doesn't need to
    change what it does with the payload — only how it gets there.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        require_internal_auth(request)

        user_id = str(
            request.query_params.get("user_id")
            or request.headers.get("X-Internal-User-Id")
            or ""
        ).strip()
        if not user_id:
            raise ValidationError({"user_id": "This field is required."})

        prefs = (
            ProfilePreferences.objects.filter(user_id=user_id)
            .values_list("notification_preferences", flat=True)
            .first()
        )
        return Response({"notification_preferences": prefs or {}})
