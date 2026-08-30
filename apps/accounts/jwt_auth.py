from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.conf import settings
from django.utils import timezone

from apps.chat.internal_auth import require_internal_auth

from .models import Device, E2EDeviceKey, E2EPreKey


def _is_go_device_binding_exempt(user) -> bool:
    """Temporary, explicitly-requested exception — see
    settings.GO_DEVICE_BINDING_EXEMPT's own comment for how to reverse
    this. Local import avoids a module-load-time dependency between
    apps.accounts and apps.partners."""
    if not getattr(settings, "GO_DEVICE_BINDING_EXEMPT", False):
        return False
    from apps.partners.seed import GO_EMAIL, GO_PHONE

    return (
        (getattr(user, "email", "") or "").lower() == GO_EMAIL.lower()
        or getattr(user, "phone", "") == GO_PHONE
    )


def revoke_unapproved_secondary_devices(user) -> int:
    if not Device.objects.filter(user=user, is_parent=True, revoked_at__isnull=True).exists():
        return 0

    devices = list(
        Device.objects.filter(
            user=user,
            is_parent=False,
            linked_via_qr=False,
            revoked_at__isnull=True,
        )
    )
    now = timezone.now()
    for device in devices:
        device.token_version = int(device.token_version or 1) + 1
        device.revoked_at = now
        device.revoke_reason = "unapproved_secondary_device"
        device.save(update_fields=["token_version", "revoked_at", "revoke_reason", "updated_at"])
        E2EDeviceKey.objects.filter(user=user, device=device).delete()
        E2EPreKey.objects.filter(user=user, device=device).delete()
    return len(devices)


def validate_device_bound_token(user, validated_token, *, header_device_id=None, require_header=True):
    """
    The authoritative device-bound revocation check: does a live, non-revoked
    Device row exist for the token's own device_id claim, and does the
    token's token_version claim still match that device's current value.

    Shared by two callers that need the EXACT same policy:
      - DeviceBoundJWTAuthentication.authenticate() — normal Django REST
        auth. header_device_id is REQUIRED here (require_header=True): a
        request with no matching X-Device-Id is rejected, unchanged from
        before this was extracted into a shared function.
      - apps.chat.views_introspect.IntrospectView — the endpoint Nest.js
        calls to validate a token on behalf of chat/calls/notifications.
        header_device_id is OPTIONAL there (require_header=False): Nest is
        relaying a client's token rather than originating the request, so it
        may have no X-Device-Id to forward. When one IS forwarded it is
        still cross-checked for consistency. This is what makes
        introspection enforce the same revocation guarantees as normal
        Django auth — previously it only checked that a device_id claim
        existed on the token, never whether a live Device row backed it.

    Raises rest_framework.exceptions.AuthenticationFailed on any failure.
    Returns the Device row on success.
    """
    token_device_id = validated_token.get("device_id")
    if not token_device_id:
        raise AuthenticationFailed("Device-bound token required")

    if _is_go_device_binding_exempt(user):
        # Skip the live-Device-row / token_version checks entirely — the
        # whole point is to let this identity authenticate from a device
        # that was never registered/approved. Still requires a real,
        # correctly-signed JWT (this only bypasses device binding, not
        # authentication itself).
        return None

    if require_header and not header_device_id:
        raise AuthenticationFailed("Missing X-Device-Id")

    if header_device_id and str(token_device_id) != str(header_device_id):
        raise AuthenticationFailed("Device mismatch")

    device = Device.objects.filter(user=user, device_id=str(token_device_id)).first()
    if not device:
        raise AuthenticationFailed("Device session revoked")

    revoke_unapproved_secondary_devices(user)
    device.refresh_from_db()
    if device.revoked_at:
        raise AuthenticationFailed("Device session revoked")

    token_version = validated_token.get("token_version")
    if token_version is not None:
        try:
            token_version_matches = int(token_version) == int(device.token_version)
        except (TypeError, ValueError):
            token_version_matches = False
        if not token_version_matches:
            raise AuthenticationFailed("Device session expired")

    Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return device


class DeviceBoundJWTAuthentication(JWTAuthentication):
    """
    Enforce device-bound access tokens.
    Clients must send X-Device-Id to match the device_id claim in the token.
    A cryptographically-verified internal service call (Nest proxying a
    user's own JWT, e.g. for /auth/devices/ management where there's no
    natural "current device" to bind to) can bypass the device check.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if not result:
            return None

        user, validated_token = result
        if request.headers.get("X-Internal-Auth"):
            # require_internal_auth does the real check (HMAC-signed
            # token + timestamp + nonce, via apps.chat.internal_auth) —
            # previously this branch only checked that SOME value was
            # present in the header, which let anyone holding a valid but
            # otherwise-rejectable token (wrong device, or a REVOKED
            # device) skip device-binding entirely by sending any string
            # here. It raises AuthenticationFailed on anything that isn't
            # a genuine, correctly-signed internal call.
            require_internal_auth(request)
            return (user, validated_token)

        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        validate_device_bound_token(
            user, validated_token, header_device_id=header_device_id, require_header=True,
        )

        return (user, validated_token)


class DeviceBoundJWTAuthenticationAllowPhoneLookup(DeviceBoundJWTAuthentication):
    """Same as DeviceBoundJWTAuthentication but ignores missing-device errors during phone lookups."""

    def authenticate(self, request):
        phone_lookup = False
        query_params = getattr(request, "query_params", None)
        if query_params is not None:
            phone_lookup = bool(query_params.get("phone"))
        elif hasattr(request, "GET"):
            phone_lookup = bool(request.GET.get("phone"))

        try:
            return super().authenticate(request)
        except AuthenticationFailed as exc:
            # Allow anonymous phone lookups to proceed even when a stale/invalid
            # token is still attached during bootstrap. The endpoint already has
            # AllowAny permission and only returns the phone-matched public user payload.
            if phone_lookup:
                return None
            raise
