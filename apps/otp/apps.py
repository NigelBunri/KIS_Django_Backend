from django.apps import AppConfig

class OtpConfig(AppConfig):
    # Email/SMS OTP authentication was fully removed in the KIS Google Auth
    # migration (2026-10) — this app is kept installed only so its migration
    # history stays applyable and the PhoneOTP table can be dropped via a
    # normal `migrate`. No views/urls/tasks remain. Safe to delete this
    # package entirely once the drop migration has been applied in every
    # environment, including production.
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.otp'
    label = 'otp'
    verbose_name = "Otp (KIS) — deprecated, pending removal"
