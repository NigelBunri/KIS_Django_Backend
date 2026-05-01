"""Middleware that enforces extra security headers for admin routes."""

from django.conf import settings


class AdminSecurityHeadersMiddleware:
    """Adds CSP, HSTS, and XSEC headers to admin control responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(("/admin/", "/control/admin/")):
            response.setdefault("X-Content-Type-Options", "nosniff")
            response.setdefault("X-Frame-Options", "DENY")
            response.setdefault("Referrer-Policy", "same-origin")
            response.setdefault(
                "Permissions-Policy",
                "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()",
            )
            response.setdefault(
                "Content-Security-Policy",
                getattr(
                    settings,
                    "ADMIN_CONTENT_SECURITY_POLICY",
                    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
                ),
            )
            response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
            response.setdefault("Cache-Control", "no-store")
            if not settings.DEBUG:
                response.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
