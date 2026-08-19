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
from apps.chat.views import StickerPackListView
from apps.media.views import UploadFileView
from apps.broadcasts.views import BroadcastVideoUploadView

import logging

from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes as drf_permission_classes
from rest_framework.permissions import IsAuthenticated as DRFIsAuthenticated
from rest_framework.response import Response as DRFResponse

health_logger = logging.getLogger("health_check")


def health_check(request):
    checks = {}
    ok = True

    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception:
        # Never return str(exc) to the client — this endpoint is
        # unauthenticated and public; a DB auth failure can name a valid
        # username, and a connection failure can leak internal hostnames.
        # Full detail still goes to the server-side structured log.
        health_logger.exception("health_check: db connectivity check failed")
        checks["db"] = "unavailable"
        ok = False

    try:
        cache.set("_health_probe", "1", timeout=5)
        checks["cache"] = "ok"
    except Exception:
        health_logger.exception("health_check: cache connectivity check failed")
        checks["cache"] = "unavailable"
        ok = False

    # Configuration-presence only — never a live connectivity probe (this
    # endpoint may be polled every few seconds by a load balancer) and
    # never a value, matching the same "booleans only" convention Nest's
    # own health controller already uses. Deliberately NOT part of the
    # `ok`/503 gate: Phase 5 built real Celery worker/beat infrastructure
    # but it isn't confirmed deployed yet, and a missing broker is not a
    # reason to fail the *web* service's own health check.
    checks["celery_broker_configured"] = bool(str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip())
    checks["resend_configured"] = bool(str(getattr(settings, "RESEND_API_KEY", "") or "").strip())

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


@api_view(['GET'])
@drf_permission_classes([DRFIsAuthenticated])
def ice_servers(request):
    """Return STUN/TURN configuration for WebRTC peer connections."""
    return DRFResponse({
        "iceServers": [
            {"urls": "stun:stun.l.google.com:19302"},
            {"urls": "stun:stun1.l.google.com:19302"},
        ]
    })


urlpatterns = [
    path("", health_check, name="root"),
    path("health/", health_check, name="health-check"),
    path("api/v1/calls/ice-servers/", ice_servers, name="calls-ice-servers"),
    path("admin/", admin.site.urls),
    path("control/admin/", include("admin_control.urls")),

    path("uploads/file", UploadFileView.as_view(), name="upload-file"),
    # Explicit top-level registration for the broadcast upload endpoints so they
    # are resolved before any app-level URL ordering can shadow them.
    path("api/v1/broadcasts/videos/upload/", BroadcastVideoUploadView.as_view(), name="broadcast-video-upload-root"),
    path("api/v1/broadcasts/upload/", BroadcastVideoUploadView.as_view(), name="broadcast-upload-root"),

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
    path("api/v1/", include("apps.ai_integration.urls")),
    # apps.tiers is quarantined — superseded by apps.accounts + apps.billing
    # (see apps/tiers/apps.py). Deliberately NOT url-exposed: 10 of its 15
    # routes (organizations/, plans/, entitlements/, usage/, invoices/,
    # plan-features/, partner-settings/, impact-settings/, campaigns/,
    # holograms/, quantum/) were live and publicly reachable, backed by a
    # fully disconnected shadow-user model system with placeholder business
    # logic. The app remains installed (migrations/table access preserved
    # for any existing data) but is no longer part of the public API surface.
    path("api/v1/", include("apps.otp.urls")),
    path("api/v1/", include("apps.chat.urls", namespace="chat-root")),
    path("api/v1/partners/", include("apps.partners.urls", namespace="partners")),
    path("api/v1/websites/", include("apps.websites.urls", namespace="websites")),
    path("api/v1/partners/", include("apps.location.urls", namespace="location")),
    # The generic core app already owns /api/v1/communities/. Chat-backed
    # communities need a separate route that creates their conversations.
    path(
        "api/v1/chat-communities/",
        include("apps.communities.chat_urls", namespace="chat-communities"),
    ),
    # Keep community post routes such as /api/v1/posts/ available.
    path("api/v1/", include("apps.communities.urls", namespace="communities")),
    # Chat-backed groups use a dedicated prefix. The generic core app already
    # owns /api/v1/groups/, and routing chat creation there produces a Group ID
    # without creating an apps.chat.Conversation.
    path(
        "api/v1/chat-groups/",
        include("apps.groups.chat_urls", namespace="chat-groups"),
    ),
    path("api/v1/partner-channels/", include("apps.channels.urls", namespace="channels")),
    path("api/v1/", include("apps.channels.subchannel_urls")),
    path("api/v1/", include("apps.broadcasts.urls", namespace="broadcasts")),
    path("api/v1/", include("apps.health_ops.urls")),
    path("api/v1/", include("apps.health_dashboard.urls")),
    path("api/v1/", include("apps.bible.urls", namespace="bible")),
    path("api/v1/feed-personalization/", include("apps.feed_personalization.urls")),
    path("api/v1/", include("apps.background_removal.urls")),
    path("api/v1/", include("apps.statuses.urls", namespace="statuses")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/", include("apps.referrals.urls")),
    path("api/v1/", include("apps.rewards.urls")),
    path("api/v1/verification/", include("apps.verification.urls", namespace="verification")),
    path("api/v1/", include("apps.testimony.urls")),
    path("api/v1/family/", include("apps.family.urls", namespace="family")),
    path("api/v1/", include("apps.government.urls", namespace="government")),
    path("api/v1/church/", include("apps.church.urls")),
    path("api/v1/localization/", include("apps.localization.urls")),
    path("api/v1/media/extended/", include("apps.broadcasts.media_extended_urls")),
    path("api/v1/business/", include("apps.commerce.business_urls")),
    path("api/v1/health/extended/", include("apps.health_ops.extended_urls")),
    path("api/v1/stickers/packs/", StickerPackListView.as_view(), name="sticker-packs"),

    # --- JWT auth endpoints (SimpleJWT) ---
    # Obtain access/refresh with username/password
    path("api/v1/auth/jwt/create/", LoginView.as_view(), name="jwt-create"),
    # Exchange refresh for a new access
    path("api/v1/auth/jwt/refresh/", DeviceBoundTokenRefreshView.as_view(), name="jwt-refresh"),
    # Verify a token (access or refresh)
    path("api/v1/auth/jwt/verify/", TokenVerifyView.as_view(), name="jwt-verify"),

    # --- OpenAPI / Docs ---
    path("api/schema/", DocsSchemaView.as_view(), name="schema"),
    path("api/docs/", DocsSwaggerView.as_view(url="/api/schema/?format=json"), name="swagger-ui"),
    path("api/docs/swagger/", DocsSwaggerView.as_view(url="/api/schema/?format=json"), name="swagger-ui"),
    path("api/docs/redoc/", DocsRedocView.as_view(url_name="schema"), name="redoc"),


    #chat urls

    path("api/v1/chat/", include("apps.chat.urls", namespace="chat-singular")),
    path("api/v1/chats/", include("apps.chat.urls", namespace="chat")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
