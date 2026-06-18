from rest_framework.routers import DefaultRouter
from .views import BridgeAccountViewSet, BridgeThreadViewSet, BridgeMessageViewSet, BridgeAutomationViewSet, BridgeAnalyticsViewSet

router = DefaultRouter()
router.register('bridge/accounts', BridgeAccountViewSet, basename='bridgeaccount')
router.register('bridge/threads', BridgeThreadViewSet, basename='bridgethread')
router.register('bridge/messages', BridgeMessageViewSet, basename='bridgemessage')
router.register('bridge/automations', BridgeAutomationViewSet, basename='bridgeautomation')
router.register('bridge/analytics', BridgeAnalyticsViewSet, basename='bridgeanalytics')

urlpatterns = router.urls