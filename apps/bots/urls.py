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
bot_webhook_deliveries = views.BotViewSet.as_view({"get": "webhook_deliveries"})
bot_webhook_delivery_retry = views.BotViewSet.as_view({"post": "webhook_delivery_retry"})
bot_ai_connection = views.BotViewSet.as_view({"get": "ai_connection", "put": "ai_connection", "delete": "ai_connection"})
bot_ai_usage = views.BotViewSet.as_view({"get": "ai_usage"})
bot_activity = views.BotViewSet.as_view({"get": "activity"})
bot_approvals = views.BotViewSet.as_view({"get": "approvals"})
bot_workflows = views.BotViewSet.as_view({"get": "workflows", "post": "workflows"})
bot_workflow_detail = views.BotViewSet.as_view({"get": "workflow_detail", "patch": "workflow_detail", "delete": "workflow_detail"})
bot_workflow_test = views.BotViewSet.as_view({"post": "workflow_test"})
bot_workflow_publish = views.BotViewSet.as_view({"post": "workflow_publish"})
bot_workflow_pause = views.BotViewSet.as_view({"post": "workflow_pause"})
bot_workflow_resume = views.BotViewSet.as_view({"post": "workflow_resume"})
bot_workflow_versions = views.BotViewSet.as_view({"get": "workflow_versions"})
bot_workflow_runs = views.BotViewSet.as_view({"get": "workflow_runs"})

# Phase 4: Knowledge and Memory
bot_knowledge_sources = views.BotViewSet.as_view({"get": "knowledge_sources", "post": "knowledge_sources"})
bot_knowledge_source_detail = views.BotViewSet.as_view({"get": "knowledge_source_detail", "delete": "knowledge_source_detail"})
bot_knowledge_source_deactivate = views.BotViewSet.as_view({"post": "knowledge_source_deactivate"})
bot_knowledge_source_reactivate = views.BotViewSet.as_view({"post": "knowledge_source_reactivate"})
bot_knowledge_test_retrieval = views.BotViewSet.as_view({"post": "knowledge_test_retrieval"})
bot_memory_config = views.BotViewSet.as_view({"get": "memory_config", "put": "memory_config"})
bot_memory_entries = views.BotViewSet.as_view({"get": "memory_entries", "delete": "memory_entries"})
bot_memory_entry_detail = views.BotViewSet.as_view({"delete": "memory_entry_detail"})

urlpatterns = [
    # Owner-facing management API
    path("bots/", bot_list, name="bot-list"),
    path("bots/overview/", views.BotOverviewView.as_view(), name="bot-overview"),
    path("bots/analytics/", views.BotAnalyticsView.as_view(), name="bot-analytics"),
    path("bots/analytics/export/", views.BotAnalyticsExportView.as_view(), name="bot-analytics-export"),
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
    path("bots/<uuid:pk>/webhook/deliveries/", bot_webhook_deliveries, name="bot-webhook-deliveries"),
    path("bots/<uuid:pk>/webhook/deliveries/<uuid:delivery_id>/retry/", bot_webhook_delivery_retry, name="bot-webhook-delivery-retry"),
    path("bots/<uuid:pk>/ai-connection/", bot_ai_connection, name="bot-ai-connection"),
    path("bots/<uuid:pk>/ai-usage/", bot_ai_usage, name="bot-ai-usage"),
    path("bots/<uuid:pk>/activity/", bot_activity, name="bot-activity"),
    path("bots/<uuid:pk>/approvals/", bot_approvals, name="bot-approvals"),
    path("bots/approvals/<uuid:approval_id>/decide/", views.BotApprovalDecisionView.as_view(), name="bot-approval-decide"),

    # Phase 2: no-code workflows
    path("bots/<uuid:pk>/workflows/", bot_workflows, name="bot-workflows"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/", bot_workflow_detail, name="bot-workflow-detail"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/test/", bot_workflow_test, name="bot-workflow-test"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/publish/", bot_workflow_publish, name="bot-workflow-publish"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/pause/", bot_workflow_pause, name="bot-workflow-pause"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/resume/", bot_workflow_resume, name="bot-workflow-resume"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/versions/", bot_workflow_versions, name="bot-workflow-versions"),
    path("bots/<uuid:pk>/workflows/<uuid:workflow_id>/runs/", bot_workflow_runs, name="bot-workflow-runs"),

    # Phase 4: Knowledge and Memory
    path("bots/<uuid:pk>/knowledge/", bot_knowledge_sources, name="bot-knowledge-sources"),
    path("bots/<uuid:pk>/knowledge/test-retrieval/", bot_knowledge_test_retrieval, name="bot-knowledge-test-retrieval"),
    path("bots/<uuid:pk>/knowledge/<uuid:source_id>/", bot_knowledge_source_detail, name="bot-knowledge-source-detail"),
    path("bots/<uuid:pk>/knowledge/<uuid:source_id>/deactivate/", bot_knowledge_source_deactivate, name="bot-knowledge-source-deactivate"),
    path("bots/<uuid:pk>/knowledge/<uuid:source_id>/reactivate/", bot_knowledge_source_reactivate, name="bot-knowledge-source-reactivate"),
    path("bots/<uuid:pk>/memory-config/", bot_memory_config, name="bot-memory-config"),
    path("bots/<uuid:pk>/memory/", bot_memory_entries, name="bot-memory-entries"),
    path("bots/<uuid:pk>/memory/<uuid:entry_id>/", bot_memory_entry_detail, name="bot-memory-entry-detail"),

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
