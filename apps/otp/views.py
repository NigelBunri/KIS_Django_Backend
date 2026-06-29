# app/views.py
import hmac, secrets, string, logging
from hashlib import sha256

from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from django.db.models import Q
from apps.core.phone_utils import to_e164
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

import phonenumbers as _phonenumbers

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from .models import PhoneOTP

logger = logging.getLogger(__name__)
User = get_user_model()


def _build_phone_variants(phone: str, country: str = "CM") -> tuple[set, set]:
    """
    Returns (phone_variants, national_variants).
    phone_variants  → candidate values for User.phone
    national_variants → candidate values for User.phone_number (national part only)
    """
    phone_variants: set[str] = set()
    national_variants: set[str] = set()

    phone = (phone or "").strip()
    if not phone:
        return phone_variants, national_variants

    phone_variants.add(phone)
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits:
        phone_variants.add(f"+{digits}")
        phone_variants.add(digits)

    try:
        if phone.startswith("+"):
            num = _phonenumbers.parse(phone, None)
        else:
            num = _phonenumbers.parse(phone, country or "CM")
        if _phonenumbers.is_possible_number(num):
            e164 = _phonenumbers.format_number(num, _phonenumbers.PhoneNumberFormat.E164)
            phone_variants.add(e164)
            if e164.startswith("+"):
                phone_variants.add(e164[1:])
            national = str(num.national_number)
            if national:
                national_variants.add(national)
    except Exception:
        pass

    return {v for v in phone_variants if v}, {v for v in national_variants if v}


def _find_user_by_phone(phone: str, country: str = "CM"):
    """Robust user lookup that tries multiple phone format variants."""
    phone_variants, national_variants = _build_phone_variants(phone, country)

    q = Q()
    for v in phone_variants:
        q |= Q(phone=v)
    for v in national_variants:
        q |= Q(phone_number=v)

    if not q:
        return None
    return User.objects.filter(q).order_by("created_at").first()


def _find_otp(phone: str, purpose: str, country: str = "CM"):
    """Robust OTP lookup that tries multiple phone format variants."""
    phone_variants, _ = _build_phone_variants(phone, country)
    if not phone_variants:
        return None
    return (
        PhoneOTP.objects
        .filter(phone__in=phone_variants, purpose=purpose)
        .order_by("-created_at")
        .first()
    )

OTP_LENGTH = 6
OTP_TTL_SECONDS = 5 * 60
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5

ALLOWED_PURPOSES = {"register", "login", "reset"}
ALLOWED_CHANNELS = {"sms", "email", "whatsapp"}


def _masked_phone(phone: str) -> str:
    text = str(phone or "")
    if len(text) <= 4:
        return "****"
    return f"{text[:3]}***{text[-2:]}"


def _debug_otp_logging_enabled() -> bool:
    return bool(settings.DEBUG and getattr(settings, "OTP_DEBUG_LOG_CODES", False))

def generate_otp(length: int = OTP_LENGTH) -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(length))

def make_code_hash(phone: str, purpose: str, code: str) -> str:
    msg = f"{phone}|{purpose}|{code}".encode("utf-8")
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, msg, sha256).hexdigest()

OVERRIDE_OTP_CODE = getattr(settings, "OTP_OVERRIDE_CODE", "676139")

def sms_configured() -> bool:
    return bool(
        getattr(settings, "INFOBIP_API_KEY", "") and
        getattr(settings, "INFOBIP_BASE", "")
    )

def whatsapp_configured() -> bool:
    return bool(
        getattr(settings, "INFOBIP_API_KEY", "") and
        getattr(settings, "INFOBIP_BASE", "") and
        getattr(settings, "WHATSAPP_SENDER_NUMBER", "")
    )

def email_configured() -> bool:
    return bool(
        getattr(settings, "SENDGRID_API_KEY", "") or
        (getattr(settings, "EMAIL_HOST", "") and getattr(settings, "EMAIL_HOST_USER", ""))
    )

def send_sms_via_provider(phone: str, body: str) -> None:
    """Send SMS via Twilio Programmable Messaging. Silently skips if not configured."""
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", "") or ""
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", "") or ""
    from_number = getattr(settings, "TWILIO_FROM_NUMBER", "") or ""

    if not account_sid or not auth_token or not from_number:
        logger.info("SMS not sent (Twilio not configured): %s", _masked_phone(phone))
        return

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=body,
            from_=from_number,
            to=phone,
        )
        logger.info("SMS sent via Twilio to %s; sid=%s", _masked_phone(phone), getattr(message, "sid", "unknown"))
    except Exception as exc:
        logger.warning("Twilio SMS failed for %s: %s", _masked_phone(phone), exc)

def send_email_otp(to_email: str, code: str, purpose: str) -> bool:
    """Send OTP via email. Returns True if sent, False if not configured or failed."""
    subject = "KIS verification code"
    body = f"Your KIS verification code is: {code}\n\nThis code expires in 5 minutes. Do not share it with anyone."
    sendgrid_key = getattr(settings, "SENDGRID_API_KEY", "") or ""
    if sendgrid_key:
        try:
            import urllib.request as _req
            import json as _json
            payload = _json.dumps({
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@kis.app")},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            }).encode("utf-8")
            req = _req.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=payload,
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with _req.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 202):
                    logger.info("Email OTP sent via SendGrid to %s", to_email)
                    return True
        except Exception as exc:
            logger.warning("SendGrid email failed for %s: %s", to_email, exc)
    # Fallback: Django email backend
    email_host = getattr(settings, "EMAIL_HOST", "") or ""
    email_user = getattr(settings, "EMAIL_HOST_USER", "") or ""
    if email_host and email_user:
        try:
            from django.core.mail import send_mail
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", email_user),
                recipient_list=[to_email],
                fail_silently=False,
            )
            logger.info("Email OTP sent via Django backend to %s", to_email)
            return True
        except Exception as exc:
            logger.warning("Django email OTP failed for %s: %s", to_email, exc)
    return False

def send_whatsapp_otp(phone: str, code: str) -> bool:
    """
    Send OTP via Infobip WhatsApp Business API.

    If WHATSAPP_TEMPLATE_NAME is set in settings, sends using an approved template
    (required for first-contact messages / users who have never messaged the sender).
    Otherwise falls back to a free-form text message (only works in a 24h reply window).
    """
    import urllib.request as _req
    import json as _json

    api_key = getattr(settings, "INFOBIP_API_KEY", "") or ""
    base = getattr(settings, "INFOBIP_BASE", "") or ""
    sender = getattr(settings, "WHATSAPP_SENDER_NUMBER", "") or ""
    template_name = getattr(settings, "WHATSAPP_TEMPLATE_NAME", "") or ""
    template_lang = getattr(settings, "WHATSAPP_TEMPLATE_LANGUAGE", "en") or "en"

    if not api_key or not base or not sender:
        logger.info("WhatsApp OTP not sent (Infobip WhatsApp not configured): %s", _masked_phone(phone))
        return False

    to_number = phone.lstrip("+")
    from_number = sender.lstrip("+")

    if template_name:
        # Template message — works for first-contact / session-less delivery.
        # The template must be pre-approved by Meta and have one body parameter (the code).
        url = f"{base.rstrip('/')}/whatsapp/1/message/template"
        body = _json.dumps({
            "messages": [{
                "from": from_number,
                "to": to_number,
                "content": {
                    "templateName": template_name,
                    "templateData": {
                        "body": {"placeholders": [code]},
                    },
                    "language": template_lang,
                },
            }]
        }).encode("utf-8")
    else:
        # Free-form text — only works if the user has messaged your WhatsApp number in the last 24h.
        url = f"{base.rstrip('/')}/whatsapp/1/message/text"
        body = _json.dumps({
            "from": from_number,
            "to": to_number,
            "content": {
                "text": f"Your KIS verification code is: *{code}*\n\nIt expires in 5 minutes. Do not share it with anyone."
            },
        }).encode("utf-8")

    req = _req.Request(
        url,
        data=body,
        headers={
            "Authorization": f"App {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _req.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            if resp.status in (200, 201):
                logger.info("WhatsApp OTP sent via Infobip to %s", _masked_phone(phone))
                return True
            logger.warning(
                "Infobip WhatsApp returned status %s for %s: %s",
                resp.status, _masked_phone(phone), resp_body[:300],
            )
    except Exception as exc:
        logger.warning("Infobip WhatsApp send failed for %s: %s", _masked_phone(phone), exc)
    return False


def normalize_phone_input(phone: str, country: str = "CM") -> str:
    phone = (phone or "").strip()
    if not phone:
        return ""
    try:
        return to_e164(phone, default_region=country)
    except Exception:
        digits_only = ''.join(ch for ch in phone if ch.isdigit())
        return digits_only or phone


@method_decorator(csrf_exempt, name="dispatch")
class ChannelsView(APIView):
    """GET /api/v1/auth/otp/channels/ — returns which delivery channels are available."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "sms": sms_configured(),
            "email": email_configured(),
            "whatsapp": whatsapp_configured(),
        })


@method_decorator(csrf_exempt, name="dispatch")
class OtpInitiateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "otp"

    def post(self, request):
        try:
            return self._handle(request)
        except Exception as exc:
            logger.exception("OtpInitiateView unhandled error: %s", exc)
            return Response(
                {"success": False, "message": "Server error — please try again.", "detail": str(exc) if settings.DEBUG else ""},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _handle(self, request):
        country = (request.data.get("country") or "CM").strip().upper()
        phone_raw = (request.data.get("phone") or "").strip()
        phone = normalize_phone_input(phone_raw, country)
        purpose = (request.data.get("purpose") or "register").strip()
        channel = (request.data.get("channel") or "sms").strip()

        if not phone:
            return Response({"success": False, "message": "phone required"}, status=400)
        if purpose not in ALLOWED_PURPOSES:
            return Response({"success": False, "message": "invalid purpose"}, status=400)
        if channel not in ALLOWED_CHANNELS:
            return Response({"success": False, "message": "invalid channel"}, status=400)

        # Channel availability checks
        if channel == "sms" and not sms_configured():
            return Response({"success": False, "message": "SMS is not available"}, status=400)
        if channel == "email":
            if not email_configured():
                return Response({"success": False, "message": "Email is not available"}, status=400)
            user = _find_user_by_phone(phone, country)
            if not user or not user.email:
                return Response({"success": False, "message": "No email address on this account"}, status=400)

        now = timezone.now()

        # Cooldown check
        try:
            last = _find_otp(phone, purpose, country)
            if last and (now - last.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
                retry_after = RESEND_COOLDOWN_SECONDS - int((now - last.created_at).total_seconds())
                return Response(
                    {"success": False, "message": "Please wait before requesting another code.", "retry_after": max(retry_after, 1)},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
        except Exception as exc:
            logger.warning("OTP cooldown check failed (non-fatal): %s", exc)

        # Generate and store OTP
        code = generate_otp(OTP_LENGTH)
        code_hash = make_code_hash(phone, purpose, code)
        expires_at = PhoneOTP.new_expiry(OTP_TTL_SECONDS)

        try:
            phone_variants, _ = _build_phone_variants(phone, country)
            PhoneOTP.objects.filter(phone__in=phone_variants, purpose=purpose, expires_at__gt=now).delete()
            PhoneOTP.objects.create(phone=phone, purpose=purpose, code_hash=code_hash, expires_at=expires_at, attempts=0)
        except Exception as exc:
            logger.exception("OTP DB write failed: %s", exc)
            return Response(
                {"success": False, "message": "Could not create verification code — database error.", "detail": str(exc) if settings.DEBUG else ""},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if _debug_otp_logging_enabled():
            logger.warning("[DEV ONLY] OTP for %s (%s): %s", _masked_phone(phone), purpose, code)
        else:
            logger.info("OTP generated for %s (%s) via %s", _masked_phone(phone), purpose, channel)

        response_data: dict = {
            "success": True,
            "expires_in": OTP_TTL_SECONDS,
            "cooldown": RESEND_COOLDOWN_SECONDS,
            "channel": channel,
        }

        # Delivery — never let a delivery failure block the success response
        try:
            if channel == "sms":
                send_sms_via_provider(phone, f"Your KIS verification code is {code}. It expires in 5 minutes.")
            elif channel == "email":
                user = _find_user_by_phone(phone, country)
                if user and user.email:
                    if not send_email_otp(user.email, code, purpose):
                        logger.warning("Email OTP delivery failed for %s", _masked_phone(phone))
            elif channel == "whatsapp":
                if not send_whatsapp_otp(phone, code) and _debug_otp_logging_enabled():
                    response_data["whatsapp_code"] = code
        except Exception as exc:
            logger.warning("OTP delivery error (non-fatal): %s", exc)

        return Response(response_data, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class OtpVerifyView(APIView):
    """
    POST /api/v1/auth/otp/verify/
    body: { phone, purpose='register', code, device_id? }
    On success for 'register' purpose: marks phone verified, issues auth tokens.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "otp"

    def post(self, request):
        country = (request.data.get("country") or "CM").strip().upper()
        phone_raw = (request.data.get("phone") or "").strip()
        phone = normalize_phone_input(phone_raw, country)
        purpose = (request.data.get("purpose") or "register").strip()
        code = (request.data.get("code") or "").strip()
        device_id = (request.data.get("device_id") or "").strip() or "unknown-device"
        device_platform = (request.data.get("device_platform") or "").strip() or None

        if not phone or not code:
            return Response({"success": False, "message": "phone and code required"}, status=400)
        if purpose not in ALLOWED_PURPOSES:
            return Response({"success": False, "message": "invalid purpose"}, status=400)

        # Override code: always passes verification (used for testing/QA).
        is_override = hmac.compare_digest(code, OVERRIDE_OTP_CODE)

        now = timezone.now()
        # Use variant-aware OTP lookup so slight format differences don't block verification
        otp = _find_otp(phone, purpose, country)

        if not is_override:
            if not otp or otp.expires_at <= now:
                return Response({"success": False, "message": "code expired or not found"}, status=400)
            if otp.attempts >= MAX_ATTEMPTS:
                return Response({"success": False, "message": "too many attempts"}, status=429)

            # Hash must be computed against the phone key that was used when the code was generated
            expected_hash = make_code_hash(otp.phone, purpose, code)
            if not hmac.compare_digest(expected_hash, otp.code_hash):
                otp.attempts += 1
                otp.save(update_fields=["attempts"])
                return Response({"success": False, "message": "invalid code"}, status=400)

        # ✅ Code verified — consume any pending OTP
        if otp:
            otp.delete()

        if purpose == "reset":
            return Response({"success": True, "verified": True}, status=200)

        # Robust user lookup: tries E.164, digits-only, with-plus, and national-number variants
        user = _find_user_by_phone(phone, country)
        if not user:
            phone_variants, national_variants = _build_phone_variants(phone, country)
            logger.warning(
                "OTP verify: user not found. phone_raw=%r phone_normalized=%r phone_variants=%r national_variants=%r country=%s",
                phone_raw, phone, sorted(phone_variants), sorted(national_variants), country,
            )
            # In DEBUG mode, return diagnostic info so the problem can be identified quickly.
            error_payload: dict = {"success": False, "message": "user not found"}
            if settings.DEBUG:
                recent_users = list(
                    User.objects.order_by("-created_at")[:5].values("id", "phone", "phone_number", "phone_country_code", "created_at")
                )
                for u in recent_users:
                    u["id"] = str(u["id"])
                    u["created_at"] = str(u["created_at"])
                error_payload["debug"] = {
                    "phone_raw": phone_raw,
                    "phone_normalized": phone,
                    "phone_variants_searched": sorted(phone_variants),
                    "national_variants_searched": sorted(national_variants),
                    "country_used": country,
                    "total_users_in_db": User.objects.count(),
                    "recent_users_phones": recent_users,
                }
            return Response(error_payload, status=404)

        # Mark phone as verified and activate account
        v = dict(user.verification or {})
        v["phone"] = {"verified": True, "verified_at": timezone.now().isoformat()}
        user.verification = v
        user.status = "active"
        user.is_active = True
        user.save(update_fields=["verification", "status", "is_active", "updated_at"])

        # Issue auth tokens now that the account is verified
        from apps.accounts.views import issue_tokens_for_user, upsert_device
        upsert_device(user, device_id, device_platform, None, request)
        tokens = issue_tokens_for_user(user, device_id=device_id)

        return Response(
            {
                "success": True,
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": {
                    "id": user.id,
                    "phone": user.phone,
                    "status": user.status,
                    "is_active": user.is_active,
                    "phone_verified": True,
                    "verification": user.verification,
                },
            },
            status=200,
        )


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetInitiateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    def post(self, request):
        country = (request.data.get("country") or "CM").strip().upper()
        phone_raw = (request.data.get("phone") or "").strip()
        phone = normalize_phone_input(phone_raw, country)
        if not phone:
            return Response({"success": False, "message": "phone required"}, status=400)

        purpose = "reset"
        channel = (request.data.get("channel") or "sms").strip()
        if channel not in ALLOWED_CHANNELS:
            return Response({"success": False, "message": "invalid channel"}, status=400)

        now = timezone.now()
        last = _find_otp(phone, purpose, country)
        if last and (now - last.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
            retry_after = RESEND_COOLDOWN_SECONDS - int((now - last.created_at).total_seconds())
            return Response(
                {"success": False, "message": "Please wait before requesting another code.", "retry_after": max(retry_after, 1)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        code = generate_otp(OTP_LENGTH)
        code_hash = make_code_hash(phone, purpose, code)
        expires_at = PhoneOTP.new_expiry(OTP_TTL_SECONDS)

        phone_variants, _ = _build_phone_variants(phone, country)
        PhoneOTP.objects.filter(phone__in=phone_variants, purpose=purpose, expires_at__gt=now).delete()
        PhoneOTP.objects.create(phone=phone, purpose=purpose, code_hash=code_hash, expires_at=expires_at, attempts=0)

        if _debug_otp_logging_enabled():
            logger.warning("[DEV ONLY] OTP generated for %s (%s): %s", _masked_phone(phone), purpose, code)
        else:
            logger.info("OTP generated for %s (%s)", _masked_phone(phone), purpose)

        response_data = {"success": True, "expires_in": OTP_TTL_SECONDS, "cooldown": RESEND_COOLDOWN_SECONDS, "channel": channel}

        if channel == "sms":
            try:
                send_sms_via_provider(phone, f"Your KIS password reset code is {code}. It expires in 5 minutes.")
            except Exception as e:
                logger.exception("Provider send failed: %s", e)
        elif channel == "email":
            user = _find_user_by_phone(phone, country)
            if user and user.email:
                send_email_otp(user.email, code, purpose)
        elif channel == "whatsapp":
            sent = send_whatsapp_otp(phone, code)
            if not sent and _debug_otp_logging_enabled():
                response_data["whatsapp_code"] = code

        return Response(response_data, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetView(APIView):
    """
    POST /api/v1/auth/password/reset/
    body: { phone, code, new_password }
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    def post(self, request):
        country = (request.data.get("country") or "CM").strip().upper()
        phone_raw = (request.data.get("phone") or "").strip()
        phone = normalize_phone_input(phone_raw, country)
        code = (request.data.get("code") or "").strip()
        new_password = (request.data.get("new_password") or "").strip()

        if not phone or not code or not new_password:
            return Response({"success": False, "message": "phone, code and new_password required"}, status=400)

        now = timezone.now()
        otp = _find_otp(phone, "reset", country)
        if not otp or otp.expires_at <= now:
            return Response({"success": False, "message": "code expired or not found"}, status=400)
        if otp.attempts >= MAX_ATTEMPTS:
            return Response({"success": False, "message": "too many attempts"}, status=429)

        # Hash against the phone key used when the OTP was stored, not the request's phone
        expected_hash = make_code_hash(otp.phone, "reset", code)
        if not hmac.compare_digest(expected_hash, otp.code_hash):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return Response({"success": False, "message": "invalid code"}, status=400)

        user = _find_user_by_phone(phone, country)
        if not user:
            otp.delete()
            return Response({"success": True}, status=200)

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"success": False, "message": e.messages}, status=400)

        user.set_password(new_password)
        if hasattr(user, "last_password_change_at"):
            user.last_password_change_at = timezone.now()
        user.save(update_fields=["password", "last_password_change_at", "updated_at"])

        otp.delete()

        return Response({"success": True}, status=200)
