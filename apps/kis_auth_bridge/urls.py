from django.urls import path

from .views import KisAuthRecoveryCompleteView, KisAuthSecurityEventView

app_name = "kis_auth_bridge"

urlpatterns = [
    path("recovery/complete/", KisAuthRecoveryCompleteView.as_view(), name="recovery-complete"),
    path("security-event/", KisAuthSecurityEventView.as_view(), name="security-event"),
]
