# apps/bots/urls.py
from django.urls import path

from . import views

bot_detail = views.BotViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"})
bot_list = views.BotViewSet.as_view({"get": "list", "post": "create"})
bot_activate = views.BotViewSet.as_view({"post": "activate"})
bot_pause = views.BotViewSet.as_view({"post": "pause"})
bot_resume = views.BotViewSet.as_view({"post": "resume"})
bot_disconnect = views.BotViewSet.as_view({"post": "disconnect"})
bot_credential = views.BotViewSet.as_view({"get": "credential", "post": "credential", "delete": "credential"})
bot_resource_grants = views.BotViewSet.as_view({"get": "resource_grants", "post": "resource_grants"})
bot_resource_grant_detail = views.BotViewSet.as_view({"delete": "revoke_resource_grant"})
bot_webhook = views.BotViewSet.as_view({"get": "webhook", "put": "webhook", "delete": "webhook"})
bot_webhook_rotate = views.BotViewSet.as_view({"post": "rotate_webhook_secret"})
bot_ai_connection = views.BotViewSet.as_view({"get": "ai_connection", "put": "ai_connection", "delete": "ai_connection"})
bot_activity = views.BotViewSet.as_view({"get": "activity"})
bot_approvals = views.BotViewSet.as_view({"get": "approvals"})

urlpatterns = [
    # Owner-facing management API
    path("bots/", bot_list, name="bot-list"),
    path("bots/<uuid:pk>/", bot_detail, name="bot-detail"),
    path("bots/<uuid:pk>/activate/", bot_activate, name="bot-activate"),
    path("bots/<uuid:pk>/pause/", bot_pause, name="bot-pause"),
    path("bots/<uuid:pk>/resume/", bot_resume, name="bot-resume"),
    path("bots/<uuid:pk>/disconnect/", bot_disconnect, name="bot-disconnect"),
    path("bots/<uuid:pk>/credential/", bot_credential, name="bot-credential"),
    path("bots/<uuid:pk>/resource-grants/", bot_resource_grants, name="bot-resource-grants"),
    path("bots/<uuid:pk>/resource-grants/<str:grant_id>/", bot_resource_grant_detail, name="bot-resource-grant-detail"),
    path("bots/<uuid:pk>/webhook/", bot_webhook, name="bot-webhook"),
    path("bots/<uuid:pk>/webhook/rotate-secret/", bot_webhook_rotate, name="bot-webhook-rotate"),
    path("bots/<uuid:pk>/ai-connection/", bot_ai_connection, name="bot-ai-connection"),
    path("bots/<uuid:pk>/activity/", bot_activity, name="bot-activity"),
    path("bots/<uuid:pk>/approvals/", bot_approvals, name="bot-approvals"),
    path("bots/approvals/<uuid:approval_id>/decide/", views.BotApprovalDecisionView.as_view(), name="bot-approval-decide"),

    # OAuth2 client-credentials + external bot API
    path("bots/oauth/token/", views.BotOAuthTokenView.as_view(), name="bot-oauth-token"),
    path("bots/external/me/", views.ExternalBotMeView.as_view(), name="bot-external-me"),
    path("bots/external/resources/", views.ExternalBotResourcesView.as_view(), name="bot-external-resources"),
    path(
        "bots/external/resources/<uuid:grant_id>/send-message/",
        views.ExternalBotSendMessageView.as_view(),
        name="bot-external-send-message",
    ),
    path(
        "bots/external/resources/<uuid:grant_id>/execute-action/",
        views.ExternalBotExecuteActionView.as_view(),
        name="bot-external-execute-action",
    ),
]
