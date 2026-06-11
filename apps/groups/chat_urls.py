from rest_framework.routers import DefaultRouter

from .views import GroupViewSet


app_name = "chat_groups"

router = DefaultRouter()
router.register(r"", GroupViewSet, basename="chat-group")

urlpatterns = router.urls
