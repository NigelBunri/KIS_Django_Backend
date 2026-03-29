from drf_spectacular.extensions import OpenApiAuthenticationExtension


class DeviceBoundJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.jwt_auth.DeviceBoundJWTAuthentication"
    name = "bearerAuth"
    priority = 1

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Bearer token authentication with required X-Device-Id header binding.",
        }
