import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import AuthenticationFailed
from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication, validate_device_bound_token
from apps.accounts.tiers import get_aggregated_tier_features, get_user_tier, is_paid_tier_name

from .internal_auth import require_internal_auth


class IntrospectView(APIView):
    """
    Authoritative token-validation endpoint for Nest.js (chat, calls, push
    notifications) — the single place external services confirm a bearer
    token is still valid. Must enforce EXACTLY the same device-bound
    revocation policy as normal Django REST auth: previously this only
    checked that the token carried a device_id claim, never whether a live,
    non-revoked Device row still backed it, so revoking a device (logout,
    single-device revoke, bulk revoke) had no effect on Nest.js access until
    the token's own expiry. Now routed through the same
    validate_device_bound_token() DeviceBoundJWTAuthentication itself uses.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        require_internal_auth(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            raise AuthenticationFailed("Missing token")

        scheme = os.environ.get("DJANGO_AUTH_SCHEME", "Bearer").strip()
        prefix = f"{scheme} "
        if not auth_header.startswith(prefix):
            raise AuthenticationFailed("Invalid auth scheme")

        token = auth_header[len(prefix):].strip()
        if not token:
            raise AuthenticationFailed("Missing token")

        jwt_auth = DeviceBoundJWTAuthentication()
        validated = jwt_auth.get_validated_token(token)
        user = jwt_auth.get_user(validated)
        if not user or not user.is_active:
            raise AuthenticationFailed("Invalid token")

        # X-Device-Id is optional here (Nest is relaying a client's token,
        # not originating the request) but cross-checked when present.
        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        validate_device_bound_token(
            user, validated, header_device_id=header_device_id, require_header=False,
        )

        username = getattr(user, "username", "") or ""
        email = getattr(user, "email", "") or ""
        display_name = (getattr(user, "display_name", "") or "").strip()
        if hasattr(user, "get_full_name"):
            display_name = display_name or user.get_full_name() or ""
        if not display_name:
            display_name = username or (email.split("@")[0] if email else "")

        # Resolves via the same canonical path as the rest of the app
        # (active Subscription first, falling back to the denormalized
        # User.tier string with alias normalization) — previously this did
        # its own ad-hoc extraction and compared against the stale string
        # "basic" (the free tier was renamed to "Free" well before this
        # endpoint existed), so every free-tier user was reported as
        # isPremium: true to Nest.js. entitlements was also always a
        # hardcoded {}, so Nest.js never had real per-tier feature data to
        # act on even though the plumbing for it existed.
        tier_obj = get_user_tier(user)
        tier_name = tier_obj.name if tier_obj else (getattr(user, "tier", "") or "")
        is_premium = is_paid_tier_name(tier_name)
        entitlements = get_aggregated_tier_features(tier_obj) if tier_obj else {}

        return Response({
            "id": str(user.id),
            "username": username,
            "email": email,
            "display_name": display_name,
            "tier": tier_name,
            "isPremium": is_premium,
            "device_id": validated.get("device_id"),
            "entitlements": entitlements,
            "scopes": [],
        })
