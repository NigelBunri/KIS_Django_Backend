"""
Email delivery service for KIS notifications.
Uses Django's configured EMAIL_HOST (SMTP). Works with any SMTP provider:
SendGrid, Mailgun, AWS SES, Postfix, etc. Set EMAIL_HOST / EMAIL_HOST_USER /
EMAIL_HOST_PASSWORD / DEFAULT_FROM_EMAIL in .env.
"""
from __future__ import annotations

import logging
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives

logger = logging.getLogger(__name__)

_TEMPLATES: dict[str, tuple[str, str]] = {
    "default": (
        "{title}",
        "<p>{body}</p>",
    ),
    "otp": (
        "Your KIS verification code: {code}",
        "<h2>Your verification code is <strong>{code}</strong></h2>"
        "<p>It expires in {ttl_minutes} minutes. Do not share this code.</p>",
    ),
    "payment_receipt": (
        "Payment confirmed — {amount} {currency}",
        "<h2>Payment Confirmed</h2>"
        "<p>Amount: <strong>{amount} {currency}</strong></p>"
        "<p>Reference: {tx_ref}</p>"
        "<p>Thank you for your purchase.</p>",
    ),
    "welcome": (
        "Welcome to KIS!",
        "<h2>Welcome to KIS</h2>"
        "<p>Your account has been created. Start exploring today.</p>",
    ),
    "membership_joined": (
        "You joined {tier_title} on {channel_name}",
        "<h2>Membership Confirmed</h2>"
        "<p>You are now a <strong>{tier_title}</strong> member of <strong>{channel_name}</strong>.</p>",
    ),
    "password_reset": (
        "Reset your KIS password",
        "<h2>Password Reset</h2>"
        "<p>Your password reset code is: <strong>{code}</strong></p>"
        "<p>It expires in {ttl_minutes} minutes.</p>",
    ),
    "device_recovery": (
        "KIS Account Recovery — device transfer code",
        "<h2>Account Recovery</h2>"
        "<p>Your device recovery code is: <strong>{recovery_code}</strong></p>"
        "<p>It expires in {expires_minutes} minutes. If you didn't request this, "
        "ignore this email — your current primary device stays active.</p>",
    ),
}


def _from_email() -> str:
    return str(getattr(settings, "DEFAULT_FROM_EMAIL", "KIS <no-reply@kis.app>"))


def send_notification_email(
    to_email: str,
    title: str,
    body: str,
    template_key: str = "default",
    context: dict | None = None,
) -> bool:
    """Send a single notification email. Returns True on success."""
    if not to_email:
        return False
    ctx = {**(context or {}), "title": title, "body": body}
    subject_tpl, html_tpl = _TEMPLATES.get(template_key, _TEMPLATES["default"])
    try:
        subject = subject_tpl.format(**ctx)
        html_body = html_tpl.format(**ctx)
    except KeyError:
        subject = title
        html_body = f"<p>{body}</p>"

    text_body = f"{title}\n\n{body}"

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=_from_email(),
            to=[to_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True
    except Exception as exc:
        logger.warning("Email send failed to %s: %s", to_email[:8] + "...", exc)
        return False


def send_otp_email(to_email: str, code: str, ttl_minutes: int = 5) -> bool:
    return send_notification_email(
        to_email=to_email,
        title=f"Your KIS verification code: {code}",
        body=f"Your code is {code}. Expires in {ttl_minutes} minutes.",
        template_key="otp",
        context={"code": code, "ttl_minutes": ttl_minutes},
    )


def send_payment_receipt_email(to_email: str, amount: str, currency: str, tx_ref: str) -> bool:
    return send_notification_email(
        to_email=to_email,
        title=f"Payment confirmed — {amount} {currency}",
        body=f"Amount: {amount} {currency}. Reference: {tx_ref}.",
        template_key="payment_receipt",
        context={"amount": amount, "currency": currency, "tx_ref": tx_ref},
    )


def send_welcome_email(to_email: str) -> bool:
    return send_notification_email(
        to_email=to_email,
        title="Welcome to KIS!",
        body="Your account has been created. Start exploring today.",
        template_key="welcome",
    )


def send_device_recovery_email(to_email: str, recovery_code: str, expires_minutes: int = 15) -> bool:
    return send_notification_email(
        to_email=to_email,
        title="KIS Account Recovery — device transfer code",
        body=f"Your device recovery code is {recovery_code}. Expires in {expires_minutes} minutes.",
        template_key="device_recovery",
        context={"recovery_code": recovery_code, "expires_minutes": expires_minutes},
    )


def send_membership_email(to_email: str, tier_title: str, channel_name: str) -> bool:
    return send_notification_email(
        to_email=to_email,
        title=f"You joined {tier_title} on {channel_name}",
        body=f"You are now a {tier_title} member of {channel_name}.",
        template_key="membership_joined",
        context={"tier_title": tier_title, "channel_name": channel_name},
    )
