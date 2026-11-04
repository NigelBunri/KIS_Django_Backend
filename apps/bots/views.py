# apps/bots/views.py
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from . import services
from .auth import BotAccessTokenAuthentication, issue_access_token
from .models import (
    Bot,
    BotApprovalRequest,
    BotResourceGrant,
    BotStatus,
)
from .permissions import IsBotCredential
from .serializers import (
    BotActivityLogSerializer,
    BotAIConnectionSerializer,
    BotApprovalRequestSerializer,
    BotCreateSerializer,
    BotCredentialSerializer,
    BotResourceGrantSerializer,
    BotSerializer,
    BotWebhookSubscriptionSerializer,
    BotWorkflowCreateSerializer,
    BotWorkflowRunSerializer,
    BotWorkflowSerializer,
    BotWorkflowUpdateSerializer,
    BotWorkflowVersionSerializer,
)


def _owned_bot_or_404(user, bot_id) -> Bot:
    bot = get_object_or_404(Bot.objects.exclude(status=BotStatus.DELETED), id=bot_id)
    if bot.owner_id != user.id:
        raise NotFound("Bot not found.")
    return bot


class BotViewSet(viewsets.ViewSet):
    """Owner-facing bot management — every endpoint here is scoped to
    bots the authenticated human user owns; there is no "list all bots"
    surface for ordinary users (see admin.py / superadmin views for
    platform-wide visibility)."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        bots = Bot.objects.filter(owner=request.user).exclude(status=BotStatus.DELETED).order_by("-created_at")
        return Response(BotSerializer(bots, many=True).data)

    def create(self, request):
        serializer = BotCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        partner = None
        partner_id = data.get("partner_id")
        if partner_id:
            from apps.partners.models import Partner

            partner = get_object_or_404(Partner, id=partner_id)
        bot = services.create_bot(
            request.user,
            name=data["name"],
            description=data.get("description", ""),
            avatar_url=data.get("avatar_url", ""),
            kind=data["kind"],
            partner=partner,
        )
        return Response(BotSerializer(bot).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        return Response(BotSerializer(bot).data)

    def partial_update(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        bot = services.update_bot(bot, request.user, **request.data)
        return Response(BotSerializer(bot).data)

    def destroy(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        services.set_status(bot, request.user, BotStatus.DELETED)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        bot = services.set_status(bot, request.user, BotStatus.ACTIVE)
        return Response(BotSerializer(bot).data)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        bot = services.set_status(bot, request.user, BotStatus.PAUSED)
        return Response(BotSerializer(bot).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        bot = services.set_status(bot, request.user, BotStatus.ACTIVE)
        return Response(BotSerializer(bot).data)

    @action(detail=True, methods=["post"])
    def disconnect(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        bot = services.set_status(bot, request.user, BotStatus.DISCONNECTED)
        return Response(BotSerializer(bot).data)

    # ---- Credentials ----

    @action(detail=True, methods=["get", "post", "delete"], url_path="credential")
    def credential(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        if request.method == "GET":
            if not hasattr(bot, "credential"):
                return Response(None)
            return Response(BotCredentialSerializer(bot.credential).data)
        if request.method == "POST":
            scopes = request.data.get("scopes") or []
            credential, plaintext = services.issue_credential(bot, request.user, scopes=scopes)
            return Response(
                BotCredentialSerializer(credential, context={"plaintext_secret": plaintext}).data,
                status=status.HTTP_201_CREATED,
            )
        services.revoke_credential(bot, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- Resource grants ----

    @action(detail=True, methods=["get", "post"], url_path="resource-grants")
    def resource_grants(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        if request.method == "GET":
            grants = bot.resource_grants.filter(is_active=True).order_by("-created_at")
            return Response(BotResourceGrantSerializer(grants, many=True).data)
        serializer = BotResourceGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant = services.grant_resource_access(
            bot, request.user,
            resource_type=serializer.validated_data["resource_type"],
            resource_id=serializer.validated_data["resource_id"],
            scopes=serializer.validated_data.get("scopes") or [],
            auto_approved_actions=serializer.validated_data.get("auto_approved_actions") or [],
        )
        return Response(BotResourceGrantSerializer(grant).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path="resource-grants/(?P<grant_id>[^/.]+)")
    def revoke_resource_grant(self, request, pk=None, grant_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        services.revoke_resource_access(bot, request.user, grant_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- Webhook subscription ----

    @action(detail=True, methods=["get", "put", "delete"], url_path="webhook")
    def webhook(self, request, pk=None):
        from .models import BotWebhookSubscription

        bot = _owned_bot_or_404(request.user, pk)
        if request.method == "GET":
            if not hasattr(bot, "webhook_subscription"):
                return Response(None)
            return Response(BotWebhookSubscriptionSerializer(bot.webhook_subscription).data)
        if request.method == "DELETE":
            BotWebhookSubscription.objects.filter(bot=bot).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        url = str(request.data.get("url") or "").strip()
        events = request.data.get("events") or []
        if not url:
            raise ValidationError({"url": "A webhook URL is required."})
        from common.url_safety import is_safe_external_url

        if not is_safe_external_url(url):
            raise ValidationError({"url": "This URL is not an allowed external address."})
        subscription, created = BotWebhookSubscription.objects.update_or_create(
            bot=bot,
            defaults={
                "url": url,
                "events": events,
                "is_active": True,
                "retry_limit": int(request.data.get("retry_limit") or 5),
                "retry_backoff_seconds": int(request.data.get("retry_backoff_seconds") or 30),
            },
        )
        if created:
            from .models import _generate_webhook_secret

            subscription.secret = _generate_webhook_secret()
            subscription.save(update_fields=["secret"])
        return Response(
            BotWebhookSubscriptionSerializer(subscription, context={"reveal_secret": created}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="webhook/rotate-secret")
    def rotate_webhook_secret(self, request, pk=None):
        from .models import BotWebhookSubscription, _generate_webhook_secret

        bot = _owned_bot_or_404(request.user, pk)
        subscription = get_object_or_404(BotWebhookSubscription, bot=bot)
        subscription.secret = _generate_webhook_secret()
        subscription.save(update_fields=["secret"])
        return Response(BotWebhookSubscriptionSerializer(subscription, context={"reveal_secret": True}).data)

    # ---- Optional AI connection ----

    @action(detail=True, methods=["get", "put", "delete"], url_path="ai-connection")
    def ai_connection(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        if request.method == "GET":
            if not hasattr(bot, "ai_connection"):
                return Response(None)
            return Response(BotAIConnectionSerializer(bot.ai_connection).data)
        if request.method == "DELETE":
            services.disconnect_ai_provider(bot, request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = BotAIConnectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = services.connect_ai_provider(
            bot, request.user,
            provider=data["provider"],
            api_key=data.get("api_key", ""),
            custom_endpoint_url=data.get("custom_endpoint_url", ""),
            model=data.get("model", ""),
            system_instructions=data.get("system_instructions", ""),
            daily_request_limit=data.get("daily_request_limit"),
        )
        return Response(BotAIConnectionSerializer(connection).data)

    # ---- Activity + approvals ----

    @action(detail=True, methods=["get"], url_path="activity")
    def activity(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        logs = bot.activity_log.order_by("-created_at")[:200]
        return Response(BotActivityLogSerializer(logs, many=True).data)

    @action(detail=True, methods=["get"], url_path="approvals")
    def approvals(self, request, pk=None):
        bot = _owned_bot_or_404(request.user, pk)
        pending = bot.approval_requests.filter(status="pending").order_by("-created_at")
        return Response(BotApprovalRequestSerializer(pending, many=True).data)

    # ---- Phase 2: no-code workflows ----

    @action(detail=True, methods=["get", "post"], url_path="workflows")
    def workflows(self, request, pk=None):
        from .models import BotWorkflowStatus

        bot = _owned_bot_or_404(request.user, pk)
        if request.method == "GET":
            qs = bot.workflows.exclude(status=BotWorkflowStatus.DELETED).order_by("-created_at")
            return Response(BotWorkflowSerializer(qs, many=True).data)
        serializer = BotWorkflowCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workflow = services.create_workflow(
            bot, request.user,
            resource_grant_id=data["resource_grant_id"], name=data["name"],
            description=data.get("description", ""), trigger_type=data["trigger_type"],
            trigger_config=data.get("trigger_config") or {}, definition=data.get("definition") or {},
        )
        return Response(BotWorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "patch", "delete"], url_path="workflows/(?P<workflow_id>[^/.]+)")
    def workflow_detail(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        if request.method == "GET":
            return Response(BotWorkflowSerializer(workflow).data)
        if request.method == "DELETE":
            services.delete_workflow(workflow, request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = BotWorkflowUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        workflow = services.update_workflow(workflow, request.user, **serializer.validated_data)
        return Response(BotWorkflowSerializer(workflow).data)

    @action(detail=True, methods=["post"], url_path="workflows/(?P<workflow_id>[^/.]+)/test")
    def workflow_test(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        result = services.test_workflow(workflow, request.user, request.data.get("sample_payload") or {})
        return Response(result)

    @action(detail=True, methods=["post"], url_path="workflows/(?P<workflow_id>[^/.]+)/publish")
    def workflow_publish(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        workflow = services.publish_workflow(workflow, request.user)
        return Response(BotWorkflowSerializer(workflow).data)

    @action(detail=True, methods=["post"], url_path="workflows/(?P<workflow_id>[^/.]+)/pause")
    def workflow_pause(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        workflow = services.pause_workflow(workflow, request.user)
        return Response(BotWorkflowSerializer(workflow).data)

    @action(detail=True, methods=["post"], url_path="workflows/(?P<workflow_id>[^/.]+)/resume")
    def workflow_resume(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        workflow = services.resume_workflow(workflow, request.user)
        return Response(BotWorkflowSerializer(workflow).data)

    @action(detail=True, methods=["get"], url_path="workflows/(?P<workflow_id>[^/.]+)/versions")
    def workflow_versions(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        versions = workflow.versions.order_by("-version")
        return Response(BotWorkflowVersionSerializer(versions, many=True).data)

    @action(detail=True, methods=["get"], url_path="workflows/(?P<workflow_id>[^/.]+)/runs")
    def workflow_runs(self, request, pk=None, workflow_id=None):
        bot = _owned_bot_or_404(request.user, pk)
        workflow = services._require_own_workflow(bot, request.user, workflow_id)
        runs = workflow.runs.order_by("-started_at")[:100]
        return Response(BotWorkflowRunSerializer(runs, many=True).data)


class BotApprovalDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, approval_id):
        approval = get_object_or_404(BotApprovalRequest, id=approval_id)
        approve = bool(request.data.get("approve"))
        approval = services.decide_approval(approval, request.user, approve)
        return Response(BotApprovalRequestSerializer(approval).data)


# ---------------------------------------------------------------------
# External bot API — OAuth2 client-credentials + bot-scoped actions.
# Entirely separate auth/permission stack from every endpoint above.
# ---------------------------------------------------------------------

class BotOAuthTokenThrottle(SimpleRateThrottle):
    """Rate-limits the token endpoint itself, independent of the
    per-client_id lockout in services.authenticate_bot_credential — that
    one protects a single client_id from being brute-forced; this one
    caps total attempts from a single IP regardless of which client_id
    it tries, so rotating through many client_ids from one source still
    gets throttled."""

    scope = "bot_oauth_token"
    rate = "30/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class BotOAuthTokenView(APIView):
    """POST client_id + client_secret -> short-lived bot access token.
    Mirrors the standard OAuth2 client_credentials grant shape so an
    external developer can use any standard OAuth2 client library
    against this one endpoint."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [BotOAuthTokenThrottle]

    def post(self, request):
        grant_type = request.data.get("grant_type", "client_credentials")
        if grant_type != "client_credentials":
            raise ValidationError({"grant_type": "Only client_credentials is supported."})
        client_id = str(request.data.get("client_id") or "").strip()
        client_secret = str(request.data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise ValidationError({"detail": "client_id and client_secret are required."})
        credential = services.authenticate_bot_credential(
            client_id, client_secret, ip=request.META.get("REMOTE_ADDR"),
        )
        if not credential:
            raise PermissionDenied("Invalid client credentials, or this bot is not active.")
        return Response(issue_access_token(credential))


class ExternalBotMeView(APIView):
    authentication_classes = [BotAccessTokenAuthentication]
    permission_classes = [IsBotCredential]

    def get(self, request):
        bot = request.auth.bot
        return Response({
            "id": str(bot.id),
            "name": bot.name,
            "handle": bot.handle,
            "status": bot.status,
            "scopes": request.auth.scopes,
        })


class ExternalBotResourcesView(APIView):
    authentication_classes = [BotAccessTokenAuthentication]
    permission_classes = [IsBotCredential]

    def get(self, request):
        bot = request.auth.bot
        grants = bot.resource_grants.filter(is_active=True)
        return Response([
            {
                "grant_id": str(g.id),
                "resource_type": g.resource_type,
                "resource_id": g.resource_id,
                "scopes": g.scopes,
            }
            for g in grants
        ])


class ExternalBotSendMessageView(APIView):
    authentication_classes = [BotAccessTokenAuthentication]
    permission_classes = [IsBotCredential]

    def post(self, request, grant_id):
        bot = request.auth.bot
        grant = get_object_or_404(BotResourceGrant, id=grant_id, bot=bot, is_active=True)
        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Message text is required."})
        result = services.send_message_as_bot(
            bot, grant, text, client_id=request.data.get("client_id"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ExternalBotExecuteActionView(APIView):
    """POST {action, details} -> either the action's result (if it ran
    immediately) or {status: "pending_approval", approval_id} (if the
    action is sensitive and the grant hasn't pre-approved it). Every
    check — live permission re-verification, required scope, daily rate
    limit, approval gating — happens inside services.execute_bot_action;
    this view does nothing but authenticate the caller and hand off."""

    authentication_classes = [BotAccessTokenAuthentication]
    permission_classes = [IsBotCredential]

    def post(self, request, grant_id):
        bot = request.auth.bot
        grant = get_object_or_404(BotResourceGrant, id=grant_id, bot=bot, is_active=True)
        action_name = str(request.data.get("action") or "").strip()
        details = request.data.get("details") or {}
        if not isinstance(details, dict):
            raise ValidationError({"details": "details must be an object."})
        result = services.execute_bot_action(bot, grant, action_name, details)
        status_code = status.HTTP_202_ACCEPTED if result.get("status") == "pending_approval" else status.HTTP_200_OK
        return Response(result, status=status_code)
