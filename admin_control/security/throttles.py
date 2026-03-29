"""Custom throttles for the admin control endpoints."""
from rest_framework.throttling import SimpleRateThrottle


class AdminBurstThrottle(SimpleRateThrottle):
    scope = "admin_burst"

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": request.user.pk,
        }


class AdminSustainedThrottle(SimpleRateThrottle):
    scope = "admin_sustained"

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": request.user.pk,
        }
