import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from . import models

logger = logging.getLogger(__name__)

_firebase_app = None


def _notification_data(notification: models.Notification) -> dict[str, str]:
    data = {
        "notification_id": str(notification.id),
        "type": notification.type,
        "target_type": notification.target_type or "",
        "target_id": str(notification.target_id or ""),
    }
    for key, value in (notification.context_data or {}).items():
        if value is not None:
            data[str(key)] = str(value)
    return data


# Exceptions that mean "this token will never work again" — the caller
# should deactivate it, not just log the error and retry it forever on the
# next notification. Mirrors Nest's identical STALE_ERROR_CODES set
# (fcm.provider.ts) so both backends treat the same class of FCM rejection
# the same way. Deliberately narrow: only genuinely permanent rejections,
# never transient ones (rate limits, unavailable, quota).
STALE_TOKEN_EXCEPTION_NAMES = {"UnregisteredError", "InvalidArgumentError"}


def is_stale_token_error(exc: Exception) -> bool:
    return type(exc).__name__ in STALE_TOKEN_EXCEPTION_NAMES


def _send_with_firebase_admin(token: str, notification: models.Notification) -> tuple[bool, str, bool]:
    global _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except Exception as exc:
        return False, f"firebase-admin is not installed: {exc}", False

    try:
        if _firebase_app is None:
            app_name = getattr(settings, "FIREBASE_APP_NAME", "kis-backend")
            existing = {app.name: app for app in firebase_admin._apps.values()}  # type: ignore[attr-defined]
            if app_name in existing:
                _firebase_app = existing[app_name]
            else:
                credential_json = getattr(settings, "FIREBASE_CREDENTIALS_JSON", "")
                credential_file = getattr(settings, "FIREBASE_CREDENTIALS_FILE", "")
                if credential_json:
                    cert = credentials.Certificate(json.loads(credential_json))
                elif credential_file:
                    cert = credentials.Certificate(credential_file)
                else:
                    return False, "Firebase credentials are not configured.", False
                _firebase_app = firebase_admin.initialize_app(cert, name=app_name)

        message = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title=notification.title,
                body=notification.body,
            ),
            data=_notification_data(notification),
        )
        message_id = messaging.send(message, app=_firebase_app)
        return True, message_id, False
    except Exception as exc:
        stale = is_stale_token_error(exc)
        if stale:
            # Expected/routine (uninstalled app, rotated token) — not a
            # server-side failure, so no stack trace noise.
            logger.info("Push token rejected as stale for notification %s: %s", notification.id, exc)
        else:
            logger.exception("Firebase Admin push send failed for notification %s", notification.id)
        return False, str(exc), stale


# Legacy HTTP API error codes that mean the same "permanently invalid token"
# thing as the Admin SDK's UnregisteredError/InvalidArgumentError above.
# https://firebase.google.com/docs/cloud-messaging/http-server-ref#error-codes
_LEGACY_STALE_ERROR_CODES = {"NotRegistered", "InvalidRegistration"}


def _send_with_legacy_server_key(token: str, notification: models.Notification) -> tuple[bool, str, bool]:
    server_key = getattr(settings, "FCM_SERVER_KEY", "") or getattr(settings, "FIREBASE_SERVER_KEY", "")
    if not server_key:
        return False, "Firebase credentials are not configured.", False

    payload = {
        "to": token,
        "notification": {
            "title": notification.title,
            "body": notification.body,
        },
        "data": _notification_data(notification),
    }
    request = urllib.request.Request(
        "https://fcm.googleapis.com/fcm/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"key={server_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def _is_stale_body(body: str) -> bool:
        try:
            parsed = json.loads(body)
            results = parsed.get("results") or []
            error = (results[0].get("error") if results else None) or parsed.get("error")
            return str(error) in _LEGACY_STALE_ERROR_CODES
        except Exception:
            return False

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            stale = _is_stale_body(body)
            success = 200 <= response.status < 300 and not stale
            return success, body, stale
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, body, _is_stale_body(body)
    except Exception as exc:
        return False, str(exc), False


def send_push(token: str, notification: models.Notification) -> tuple[bool, str, bool]:
    """Returns (success, message_id_or_error, is_stale_token). `is_stale_token`
    is only ever True alongside success=False, and means the caller should
    deactivate this exact token — it will never succeed again."""
    provider = str(getattr(settings, "NOTIFICATIONS_PUSH_PROVIDER", "firebase")).lower()
    if provider not in {"firebase", "fcm"}:
        return False, f"Unsupported push provider: {provider}", False

    if getattr(settings, "FIREBASE_CREDENTIALS_JSON", "") or getattr(settings, "FIREBASE_CREDENTIALS_FILE", ""):
        return _send_with_firebase_admin(token, notification)
    return _send_with_legacy_server_key(token, notification)
