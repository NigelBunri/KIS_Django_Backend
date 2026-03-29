from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


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
            # Allow anonymous phone lookups when the only failure is the missing device header.
            if phone_lookup and str(exc.detail) == "Missing X-Device-Id":
                return None
            raise
