from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.utils import timezone

from .models import Device, E2EDeviceKey, E2EPreKey


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


class DeviceBoundJWTAuthentication(JWTAuthentication):
    """
    Enforce device-bound access tokens.
    Clients must send X-Device-Id to match the device_id claim in the token.
    Internal service calls can bypass by using X-Internal-Auth.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if not result:
            return None

        user, validated_token = result
        if request.headers.get("X-Internal-Auth"):
            return (user, validated_token)

        token_device_id = validated_token.get("device_id")
        if not token_device_id:
            raise AuthenticationFailed("Device-bound token required")

        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        if not header_device_id:
            raise AuthenticationFailed("Missing X-Device-Id")

        if str(token_device_id) != str(header_device_id):
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
