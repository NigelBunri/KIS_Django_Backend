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


def _send_with_firebase_admin(token: str, notification: models.Notification) -> tuple[bool, str]:
    global _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except Exception as exc:
        return False, f"firebase-admin is not installed: {exc}"

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
                    return False, "Firebase credentials are not configured."
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
        return True, message_id
    except Exception as exc:
        logger.exception("Firebase Admin push send failed for notification %s", notification.id)
        return False, str(exc)


def _send_with_legacy_server_key(token: str, notification: models.Notification) -> tuple[bool, str]:
    server_key = getattr(settings, "FCM_SERVER_KEY", "") or getattr(settings, "FIREBASE_SERVER_KEY", "")
    if not server_key:
        return False, "Firebase credentials are not configured."

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
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                return True, body
            return False, body
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)


def send_push(token: str, notification: models.Notification) -> tuple[bool, str]:
    provider = str(getattr(settings, "NOTIFICATIONS_PUSH_PROVIDER", "firebase")).lower()
    if provider not in {"firebase", "fcm"}:
        return False, f"Unsupported push provider: {provider}"

    if getattr(settings, "FIREBASE_CREDENTIALS_JSON", "") or getattr(settings, "FIREBASE_CREDENTIALS_FILE", ""):
        return _send_with_firebase_admin(token, notification)
    return _send_with_legacy_server_key(token, notification)
