from django.urls import path, include
from apps.chat.views_introspect import ChatMessageReportView, IntrospectView, UserBlockCheckView
from rest_framework.routers import DefaultRouter

from .views import ConversationViewSet, MessageThreadLinkViewSet, StickerPackListView

app_name = "chat"

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'threads', MessageThreadLinkViewSet, basename='thread')

urlpatterns = [
    path('auth/introspect/', IntrospectView.as_view(), name='auth-introspect'),
    path('internal/blocked-among/', UserBlockCheckView.as_view(), name='internal-blocked-among'),
    path('internal/message-reports/', ChatMessageReportView.as_view(), name='internal-message-reports'),
    path('', include(router.urls)),
]
