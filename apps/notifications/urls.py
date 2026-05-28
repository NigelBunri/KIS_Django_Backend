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

urlpatterns = router.urls + [
    path("notifications/mention/", MentionNotificationView.as_view(), name="mention-notification"),
]
