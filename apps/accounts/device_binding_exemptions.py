"""
Identities exempt from device-binding while settings.GO_DEVICE_BINDING_EXEMPT
is on: the GO (General Overseer) platform-owner account, and the dedicated
Google Play / Apple App Review test account (reviewers sign in from their own
unregistered devices and must never be QR-gated). Both share the single
env-var switch — flip GO_DEVICE_BINDING_EXEMPT to false to revoke both
exemptions at once, no redeploy needed.

Not a partners/seed concern (unlike GO_EMAIL/GO_PHONE), so the review-account
identity lives here rather than in apps.partners.seed.
"""
from django.conf import settings

APP_REVIEW_EMAIL = "nigle.bah+appreview@gmail.com"
APP_REVIEW_PHONE = "+237600000001"


def is_device_binding_exempt(user) -> bool:
    if not getattr(settings, "GO_DEVICE_BINDING_EXEMPT", False):
        return False

    from apps.partners.seed import GO_EMAIL, GO_PHONE

    email = (getattr(user, "email", "") or "").lower()
    phone = getattr(user, "phone", "") or ""
    return (
        email == GO_EMAIL.lower()
        or phone == GO_PHONE
        or email == APP_REVIEW_EMAIL.lower()
        or phone == APP_REVIEW_PHONE
    )
