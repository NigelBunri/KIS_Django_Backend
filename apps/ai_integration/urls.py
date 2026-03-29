from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import GroqAIStreamView, GroqAITaskView

router = DefaultRouter()
router.register(r'ai-models', views.AIModelViewSet)
router.register(r'ai-jobs', views.AIJobViewSet)
router.register(r'translations', views.TranslationRequestViewSet)
router.register(r'qna-sessions', views.QnASessionViewSet)
router.register(r'feedbacks', views.AIJobFeedbackViewSet)
router.register(r'pipelines', views.AIPipelineViewSet)
router.register(r'schedules', views.AIScheduleViewSet)

urlpatterns = router.urls + [
    path("groq-stream/", GroqAIStreamView.as_view(), name="groq-stream"),
    path("groq-task/", GroqAITaskView.as_view(), name="groq-task"),
]
from .views import GroqAITaskView
