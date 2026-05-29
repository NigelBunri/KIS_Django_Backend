from django.urls import path
from .views import ChannelsView, OtpInitiateView, OtpVerifyView, PasswordResetInitiateView, PasswordResetView

urlpatterns = [
    path("auth/otp/channels/", ChannelsView.as_view()),
    path("auth/otp/initiate/", OtpInitiateView.as_view()),
    path("auth/otp/verify/", OtpVerifyView.as_view()),
    path("auth/password/forgot/", PasswordResetInitiateView.as_view()),
    path("auth/password/reset/", PasswordResetView.as_view()),
]
