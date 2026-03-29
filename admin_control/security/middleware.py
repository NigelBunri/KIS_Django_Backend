"""Middleware that enforces extra security headers for admin control routes."""


class AdminSecurityHeadersMiddleware:
    """Adds CSP, HSTS, and XSEC headers to admin control responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/control/admin/"):
            response.setdefault("X-Content-Type-Options", "nosniff")
            response.setdefault("X-Frame-Options", "DENY")
            response.setdefault("Referrer-Policy", "same-origin")
            response.setdefault("Permissions-Policy", "geolocation=(), camera=()")
            response.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
