from django.urls import path

from .views import (
    KisAuthLinkCompleteView,
    KisAuthLinkInitiateView,
    KisAuthRecoveryCompleteView,
    KisAuthRegistrationCompleteView,
    KisAuthSecurityEventView,
    KisAuthSsoConfigView,
)

app_name = "kis_auth_bridge"

urlpatterns = [
    path("recovery/complete/", KisAuthRecoveryCompleteView.as_view(), name="recovery-complete"),
    path("link/initiate/", KisAuthLinkInitiateView.as_view(), name="link-initiate"),
    path("link/complete/", KisAuthLinkCompleteView.as_view(), name="link-complete"),
    path("registration/complete/", KisAuthRegistrationCompleteView.as_view(), name="registration-complete"),
    path("security-event/", KisAuthSecurityEventView.as_view(), name="security-event"),
    path("sso-config/", KisAuthSsoConfigView.as_view(), name="sso-config"),
]
