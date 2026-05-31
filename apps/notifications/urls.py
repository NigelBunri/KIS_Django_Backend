from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    NotificationDeviceTokenViewSet,
    NotificationViewSet,
    NotificationTemplateViewSet,
    NotificationRuleViewSet,
    MentionNotificationView,
)

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notifications")
router.register(r"notification-templates", NotificationTemplateViewSet, basename="notification-templates")
router.register(r"notification-rules", NotificationRuleViewSet, basename="notification-rules")
router.register(r"notification-device-tokens", NotificationDeviceTokenViewSet, basename="notification-device-tokens")

urlpatterns = [
    # Custom paths must come BEFORE router.urls — the router's notifications/<pk>/ pattern
    # would otherwise swallow "mention" as a pk and return 405.
    path("notifications/mention/", MentionNotificationView.as_view(), name="mention-notification"),
] + router.urls
