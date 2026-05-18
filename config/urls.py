from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path, include
from django.utils.decorators import method_decorator
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from rest_framework_simplejwt.views import (
    TokenVerifyView,       # POST: { token } -> {} if valid
)
from apps.accounts.views import DeviceBoundTokenRefreshView, LoginView
from apps.media.views import UploadFileView

from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache


def health_check(request):
    checks = {}
    ok = True

    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = str(exc)
        ok = False

    try:
        cache.set("_health_probe", "1", timeout=5)
        checks["cache"] = "ok"
    except Exception as exc:
        checks["cache"] = str(exc)
        ok = False

    status_code = 200 if ok else 503
    return JsonResponse({"status": "ok" if ok else "error", "checks": checks}, status=status_code)


class StaffOnlySpectacularAPIView(SpectacularAPIView):
    @method_decorator(staff_member_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


class StaffOnlySpectacularSwaggerView(SpectacularSwaggerView):
    @method_decorator(staff_member_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


class StaffOnlySpectacularRedocView(SpectacularRedocView):
    @method_decorator(staff_member_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


DocsSchemaView = SpectacularAPIView if settings.DEBUG else StaffOnlySpectacularAPIView
DocsSwaggerView = SpectacularSwaggerView if settings.DEBUG else StaffOnlySpectacularSwaggerView
DocsRedocView = SpectacularRedocView if settings.DEBUG else StaffOnlySpectacularRedocView

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
    path("control/admin/", include("admin_control.urls")),

    path("uploads/file", UploadFileView.as_view(), name="upload-file"),

    # --- Versioned app routes ---
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.content.urls")),
    path("api/v1/", include("apps.media.urls")),
    path("api/v1/", include("apps.events.urls")),
    path("api/v1/", include("apps.notifications.urls")),
    path("api/v1/", include("apps.moderation.urls")),
    path("api/v1/commerce/", include("apps.commerce.urls")),
    path("api/v1/", include("apps.surveys.urls")),
    path("api/v1/", include("apps.bridge.urls")),
    path("api/v1/", include("apps.analytics.urls")),
    path("api/v1/", include("apps.tiers.urls")),
    path("api/v1/", include("apps.otp.urls")),
    path("api/v1/", include("apps.chat.urls", namespace="chat-root")),
    path("api/v1/partners/", include("apps.partners.urls", namespace="partners")),
    path("api/v1/", include("apps.communities.urls", namespace="communities")),
    path("api/v1/", include("apps.groups.urls", namespace="groups")),
    path("api/v1/partner-channels/", include("apps.channels.urls", namespace="channels")),
    path("api/v1/", include("apps.broadcasts.urls", namespace="broadcasts")),
    path("api/v1/", include("apps.health_ops.urls")),
    path("api/v1/", include("apps.health_dashboard.urls")),
    path("api/v1/", include("apps.bible.urls", namespace="bible")),
    path("api/v1/feed-personalization/", include("apps.feed_personalization.urls")),
    path("api/v1/", include("apps.background_removal.urls")),
    path("api/v1/", include("apps.statuses.urls", namespace="statuses")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/verification/", include("apps.verification.urls", namespace="verification")),

    # --- JWT auth endpoints (SimpleJWT) ---
    # Obtain access/refresh with username/password
    path("api/v1/auth/jwt/create/", LoginView.as_view(), name="jwt-create"),
    # Exchange refresh for a new access
    path("api/v1/auth/jwt/refresh/", DeviceBoundTokenRefreshView.as_view(), name="jwt-refresh"),
    # Verify a token (access or refresh)
    path("api/v1/auth/jwt/verify/", TokenVerifyView.as_view(), name="jwt-verify"),

    # --- OpenAPI / Docs ---
    path("api/schema/", DocsSchemaView.as_view(), name="schema"),
    path("api/docs/", DocsSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/swagger/", DocsSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", DocsRedocView.as_view(url_name="schema"), name="redoc"),


    #chat urls

    path("api/v1/chat/", include("apps.chat.urls", namespace="chat-singular")),
    path("api/v1/chats/", include("apps.chat.urls", namespace="chat")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
