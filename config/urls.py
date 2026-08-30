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


DELETE_ACCOUNT_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Delete Your KIS Account</title>
<style>
  :root { color-scheme: light; --bg:#faf8f3; --card:#fff; --border:#e7e0d2; --text:#241f16; --subtext:#574f3f; --primary:#9a6a1f; --danger:#b3261e; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; line-height:1.6; }
  .wrap { max-width:520px; margin:0 auto; padding:32px 20px 80px; }
  h1 { font-size:1.5rem; margin:0 0 8px; }
  .lead { color:var(--subtext); font-size:0.95rem; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; }
  .card h2 { font-size:1rem; margin:0 0 10px; }
  .card ul { margin:0 0 16px; padding-left:1.2em; color:var(--subtext); font-size:0.9rem; }
  label { display:block; font-size:0.85rem; font-weight:600; margin:14px 0 4px; }
  input[type=text], input[type=password], input[type=tel] {
    width:100%; padding:10px 12px; border:1px solid var(--border); border-radius:8px; font-size:0.95rem;
  }
  .checkbox-row { display:flex; align-items:flex-start; gap:8px; margin-top:16px; }
  .checkbox-row input { margin-top:3px; }
  .checkbox-row label { margin:0; font-weight:400; font-size:0.88rem; color:var(--subtext); }
  button { margin-top:20px; width:100%; padding:12px; border:none; border-radius:8px; background:var(--danger); color:#fff; font-size:1rem; font-weight:600; cursor:pointer; }
  button:disabled { opacity:0.6; cursor:not-allowed; }
  .msg { margin-top:16px; padding:12px; border-radius:8px; font-size:0.9rem; display:none; }
  .msg.success { display:block; background:#e8f3e8; color:#1e5c1e; border:1px solid #b7d9b7; }
  .msg.error { display:block; background:#fcebea; color:var(--danger); border:1px solid #f0bcb8; }
  footer { margin-top:32px; text-align:center; color:var(--subtext); font-size:0.82rem; }
  footer a { color:var(--primary); }
</style>
</head>
<body>
<div class="wrap">
  <h1>Delete Your KIS Account</h1>
  <p class="lead">Use this page to permanently delete your Kingdom Impact Social (KIS) account and its associated data — no need to have the app installed.</p>

  <div class="card">
    <h2>What gets deleted</h2>
    <ul>
      <li>Your profile, account credentials, and settings</li>
      <li>Messages, testimonies, posts, and media you've uploaded</li>
      <li>Marketplace listings, health module data, and Family Hub profiles you own</li>
      <li>This action is immediate and cannot be undone</li>
    </ul>
    <p style="color:var(--subtext);font-size:0.85rem;margin:0 0 4px;">
      Prefer to do this from the app instead? Open KIS &rarr; Settings &rarr; Privacy &amp; Compliance &rarr; Delete All My Data.
    </p>

    <form id="delete-form">
      <label for="phone">Phone number (the one you sign in with)</label>
      <input type="tel" id="phone" name="phone" placeholder="+2376XXXXXXXX" required />

      <label for="password">Password</label>
      <input type="password" id="password" name="password" required />

      <div class="checkbox-row">
        <input type="checkbox" id="confirm-check" required />
        <label for="confirm-check">I understand this permanently deletes my account and data, and cannot be undone.</label>
      </div>

      <button type="submit" id="submit-btn">Delete my account</button>
      <div class="msg" id="result-msg"></div>
    </form>
  </div>

  <footer>
    Questions? <a href="mailto:nigle.bah@gmail.com">nigle.bah@gmail.com</a> &middot;
    <a href="/privacy/">Privacy Policy</a>
  </footer>
</div>

<script>
(function () {
  var form = document.getElementById('delete-form');
  var btn = document.getElementById('submit-btn');
  var msg = document.getElementById('result-msg');

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var phone = document.getElementById('phone').value.trim();
    var password = document.getElementById('password').value;
    var confirmed = document.getElementById('confirm-check').checked;

    if (!confirmed) return;
    if (!window.confirm('This will permanently delete your KIS account and all associated data. This cannot be undone. Continue?')) return;

    btn.disabled = true;
    btn.textContent = 'Deleting…';
    msg.className = 'msg';
    msg.textContent = '';

    fetch('/api/v1/auth/account/delete-request/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: phone, password: password, confirm: 'DELETE' }),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (result) {
        if (result.ok) {
          msg.className = 'msg success';
          msg.textContent = result.data.detail || 'Your account has been deleted.';
          form.reset();
          btn.style.display = 'none';
        } else {
          msg.className = 'msg error';
          msg.textContent = (result.data && result.data.detail) || 'Something went wrong. Please try again.';
          btn.disabled = false;
          btn.textContent = 'Delete my account';
        }
      })
      .catch(function () {
        msg.className = 'msg error';
        msg.textContent = 'Network error. Please try again.';
        btn.disabled = false;
        btn.textContent = 'Delete my account';
      });
  });
})();
</script>
</body>
</html>
"""


def delete_account_page(request):
    from django.http import HttpResponse

    return HttpResponse(DELETE_ACCOUNT_PAGE_HTML, content_type="text/html")


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
    # Both forms resolve directly (no redirect) — some URL validators
    # (e.g. Play Console's Data Safety form) reject a URL that 301s.
    path("delete-account/", delete_account_page, name="delete-account-page"),
    path("delete-account", delete_account_page, name="delete-account-page-no-slash"),
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
