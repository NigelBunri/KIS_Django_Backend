from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views_internal import NotificationPreferencesInternalView
from .views import (
    CheckContact,
    EducationViewSet,
    ExperienceViewSet,
    ProjectViewSet,
    RecommendationViewSet,
    RegisterView,     # ViewSet (create -> JWTs)
    LoginView,        # APIView (returns JWTs)
    LogoutView,       # APIView (blacklists refresh if enabled)
    TwoFactorSetupView,
    TwoFactorEnableView,
    TwoFactorDisableView,
    TwoFactorStatusView,
    OTPStatusView,
    E2EERegisterKeysView,
    E2EEFetchBundleView,
    E2EEFetchDeviceBundlesView,
    FamilyAccessibilityPreferencesView,
    DeviceSessionsView,
    DeviceSessionDetailView,
    DeviceBoundTokenRefreshView,
    UserSkillViewSet,
    UserViewSet,
    ProfileViewSet,
    ProfileFieldVisibilityViewSet,
    ProfileArticleViewSet,
    ProfilePreferencesViewSet,
    ProfileLanguageViewSet,
    ProfileShowcaseViewSet,
    AccountTierViewSet,
    SubscriptionViewSet,
    SessionViewSet,
    ApiTokenViewSet,
    ConnectionViewSet,
    GlobalJobBoardView,
    MyApplicationsView,
    # Multi-device QR login
    DeviceQRGenerateView,
    DeviceQRLoginView,
    DeviceWebPairingGenerateView,
    DeviceWebPairingRedeemView,
    TransferParentDeviceView,
    RevokeAllSecondaryView,
    DeviceRenameView,
    ParentRecoveryInitView,
    ParentRecoveryConfirmView,
    # QuickLock PIN
    QuickLockPinView,
    QuickLockPinVerifyView,
    # Password change
    PasswordChangeView,
    # Account deletion
    AccountDeletionView,
    # GDPR data export
    DataExportView,
)

# Optional: SimpleJWT endpoints (convenience here too)
from rest_framework_simplejwt.views import (
    TokenVerifyView,       # {token} -> {} if valid
)

router = DefaultRouter()

# Auth (registration via ViewSet create)
router.register(r"auth/register", RegisterView, basename="auth-register")

# ApiToken access (requires `api_access` feature)
router.register(r"auth/tokens", ApiTokenViewSet, basename="auth-tokens")

# Core resources
router.register(r"users", UserViewSet, basename="users")
router.register(r"profiles", ProfileViewSet, basename="profiles")
router.register(r"profile-privacy", ProfileFieldVisibilityViewSet, basename="profile-privacy")
router.register(r"profile-articles", ProfileArticleViewSet, basename="profile-articles")
router.register(r"profile-preferences", ProfilePreferencesViewSet, basename="profile-preferences")
router.register(r"profile-languages", ProfileLanguageViewSet, basename="profile-languages")
router.register(r"profile-showcases", ProfileShowcaseViewSet, basename="profile-showcases")
router.register(r"tiers", AccountTierViewSet, basename="tiers")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscriptions")
router.register(r"sessions", SessionViewSet, basename="sessions")
router.register(r"experiences", ExperienceViewSet, basename="experiences")
router.register(r"educations", EducationViewSet, basename="educations")
router.register(r"skills", UserSkillViewSet, basename="skills")
router.register(r"projects", ProjectViewSet, basename="projects")
router.register(r"recommendations", RecommendationViewSet, basename="recommendations")
router.register(r"connections", ConnectionViewSet, basename="connections")

urlpatterns = [
    # JWT login/logout you defined in views.py
    path("auth/login/",  LoginView.as_view(),  name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/2fa/setup/", TwoFactorSetupView.as_view(), name="auth-2fa-setup"),
    path("auth/2fa/enable/", TwoFactorEnableView.as_view(), name="auth-2fa-enable"),
    path("auth/2fa/disable/", TwoFactorDisableView.as_view(), name="auth-2fa-disable"),
    path("auth/2fa/status/", TwoFactorStatusView.as_view(), name="auth-2fa-status"),
    path("auth/otp/status/", OTPStatusView.as_view(), name="auth-otp-status"),
    path("auth/e2ee/keys/", E2EERegisterKeysView.as_view(), name="auth-e2ee-keys"),
    path("auth/e2ee/keys/<uuid:user_id>/", E2EEFetchBundleView.as_view(), name="auth-e2ee-keys-user"),
    path("auth/e2ee/keys/<uuid:user_id>/devices/", E2EEFetchDeviceBundlesView.as_view(), name="auth-e2ee-keys-user-devices"),
    path("auth/devices/", DeviceSessionsView.as_view(), name="auth-devices"),
    # Specific device sub-paths MUST come before the <str:device_id> wildcard
    path("auth/devices/qr/", DeviceQRGenerateView.as_view(), name="device-qr-generate"),
    path("auth/devices/qr-login/", DeviceQRLoginView.as_view(), name="device-qr-login"),
    path("auth/devices/web-pairing/", DeviceWebPairingGenerateView.as_view(), name="device-web-pairing-generate"),
    path("auth/devices/web-pairing/redeem/", DeviceWebPairingRedeemView.as_view(), name="device-web-pairing-redeem"),
    path("auth/devices/transfer-parent/", TransferParentDeviceView.as_view(), name="device-transfer-parent"),
    path("auth/devices/revoke-all-secondary/", RevokeAllSecondaryView.as_view(), name="device-revoke-all-secondary"),
    path("auth/devices/<str:device_id>/rename/", DeviceRenameView.as_view(), name="device-rename"),
    path("auth/devices/<str:device_id>/", DeviceSessionDetailView.as_view(), name="auth-device-detail"),
    path("profile-preferences/family-accessibility/", FamilyAccessibilityPreferencesView.as_view(), name="family-accessibility-preferences"),
    # Trusted-internal only (apps.chat.internal_auth) — Nest calls this to
    # check a user's chat/call notification preferences before sending a
    # push. See apps/accounts/views_internal.py.
    path(
        "profile-preferences/internal/notification-prefs/",
        NotificationPreferencesInternalView.as_view(),
        name="profile-preferences-internal-notification-prefs",
    ),

    # Optional: direct SimpleJWT endpoints (tooling-friendly)
    path("auth/jwt/create/",  LoginView.as_view(), name="jwt-create"),
    path("auth/jwt/refresh/", DeviceBoundTokenRefreshView.as_view(), name="jwt-refresh"),
    path("auth/jwt/verify/",  TokenVerifyView.as_view(),    name="jwt-verify"),
    path("contacts/check", CheckContact.as_view(), name="check_contact"),
    path("users/check-contacts/", CheckContact.as_view(), name="check_contact_legacy"),
    path("jobs/", GlobalJobBoardView.as_view(), name="global-job-board"),
    path("my-applications/", MyApplicationsView.as_view(), name="my-applications"),
    path("auth/recovery/initiate/", ParentRecoveryInitView.as_view(), name="parent-recovery-init"),
    path("auth/recovery/confirm/", ParentRecoveryConfirmView.as_view(), name="parent-recovery-confirm"),
    path("auth/quicklock-pin/", QuickLockPinView.as_view(), name="auth-quicklock-pin"),
    path("auth/quicklock-pin/verify/", QuickLockPinVerifyView.as_view(), name="auth-quicklock-pin-verify"),
    path("auth/password/change/", PasswordChangeView.as_view(), name="auth-password-change"),
    path("auth/account/", AccountDeletionView.as_view(), name="auth-account-delete"),
    path("auth/data-export/", DataExportView.as_view(), name="auth-data-export"),

    path("", include(router.urls)),
]
