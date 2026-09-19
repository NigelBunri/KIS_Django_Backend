"""
Email delivery service for KIS notifications.

This module is the single, unified entry point for every outbound email in
the app — OTP codes, receipts, welcome/digest/invite mail, generic
notification-channel email, all of it goes through send_notification_email()
below. Production sends via Resend's HTTP API (config/settings/production.py
sets EMAIL_BACKEND to apps.notifications.resend_backend.ResendEmailBackend);
this is the one seam to repoint if email delivery moves out to its own
microservice later. Do not add a second, parallel way to send email —
route it through here instead.
"""
from __future__ import annotations

import html
import logging
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives

logger = logging.getLogger(__name__)

_TEMPLATES: dict[str, tuple[str, str]] = {
    "default": (
        "{title}",
        "<p>{body}</p>",
    ),
    # One shared "code" visual per purpose, distinct copy per purpose — the
    # audit's core finding was a single generic OTP email used for every
    # purpose (same wording for a login code and a password-reset code).
    # "otp" itself stays as a generic fallback for any purpose without its
    # own entry below, rather than crashing/looking broken.
    "otp": (
        "Your KIS verification code: {code}",
        "<h2>Your verification code is <strong>{code}</strong></h2>"
        "<p>It expires in {ttl_minutes} minutes. Do not share this code.</p>",
    ),
    "otp_register": (
        "Your KIS verification code: {code}",
        "<h2>Welcome to KIS</h2>"
        "<p>Use the code below to finish creating your account.</p>"
        "<h2 style=\"letter-spacing:4px;\">{code}</h2>"
        "<p>It expires in {ttl_minutes} minutes. If you didn't request this, "
        "you can safely ignore this email.</p>",
    ),
    "otp_login": (
        "Your KIS sign-in code: {code}",
        "<h2>Sign in to KIS</h2>"
        "<p>Use the code below to sign in.</p>"
        "<h2 style=\"letter-spacing:4px;\">{code}</h2>"
        "<p>It expires in {ttl_minutes} minutes. If this wasn't you, your "
        "account is still safe — just ignore this email.</p>",
    ),
    "otp_web_login": (
        "Your KIS web sign-in code: {code}",
        "<h2>Sign in to KIS on the web</h2>"
        "<p>Use the code below to sign in to KIS from your browser.</p>"
        "<h2 style=\"letter-spacing:4px;\">{code}</h2>"
        "<p>It expires in {ttl_minutes} minutes. If you didn't request this, "
        "you can safely ignore this email.</p>",
    ),
    "otp_email_verify": (
        "Verify your email for KIS: {code}",
        "<h2>Verify your email</h2>"
        "<p>Use the code below to confirm this email address belongs to you.</p>"
        "<h2 style=\"letter-spacing:4px;\">{code}</h2>"
        "<p>It expires in {ttl_minutes} minutes.</p>",
    ),
    "otp_reset": (
        "Reset your KIS password",
        "<h2>Password Reset</h2>"
        "<p>Use the code below to reset your password.</p>"
        "<h2 style=\"letter-spacing:4px;\">{code}</h2>"
        "<p>It expires in {ttl_minutes} minutes. If you didn't request this, "
        "someone may have mistyped your phone number — your password stays "
        "unchanged unless this code is used.</p>",
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
    "gift_membership": (
        "{gifter_name} sent you a KIS membership gift!",
        "<h2>You've received a gift 🎁</h2>"
        "<p><strong>{gifter_name}</strong> gifted you a <strong>{tier_title}</strong> membership "
        "on <strong>{channel_name}</strong>.</p>"
        "{message_html}"
        "<p>Open the KIS app, sign in (or create an account with this email address), then go to "
        "<strong>Profile → Redeem Gift</strong> and enter this code:</p>"
        "<h2 style=\"letter-spacing:2px;\">{redeem_code}</h2>"
        "<p>This gift expires on {expires_at}.</p>",
    ),
    "device_recovery": (
        "KIS Account Recovery — device transfer code",
        "<h2>Account Recovery</h2>"
        "<p>Your device recovery code is: <strong>{recovery_code}</strong></p>"
        "<p>It expires in {expires_minutes} minutes. If you didn't request this, "
        "ignore this email — your current primary device stays active.</p>",
    ),
    "website_form_submission": (
        "New form response on {website_name}",
        "<h2>New Form Response</h2>"
        "<p>Someone submitted the <strong>{form_title}</strong> form on "
        "<strong>{website_name}</strong> ({page_title}).</p>"
        "<div>{fields_html}</div>",
    ),
    "digest": (
        "Your KIS digest — {count} update(s)",
        "<h2>Your KIS Digest</h2>"
        "<p>{count} update(s) since your last visit:</p>"
        "<ul>{items_html}</ul>",
    ),
    "livestream_guest_invite": (
        "{inviter_name} invited you to join {stream_title} on KIS",
        "<h2>You're invited to a livestream</h2>"
        "<p><strong>{inviter_name}</strong> invited you as a <strong>{role}</strong> "
        "on <strong>{channel_name}</strong>'s livestream: <strong>{stream_title}</strong>.</p>"
        "{schedule_html}"
        "<p><a href=\"{invite_url}\">Join the livestream</a></p>",
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


# Maps an OTP purpose (apps.otp.views.ALLOWED_PURPOSES) to its own branded
# copy above. Deliberately not "reset" -> "otp_reset" implied by naming
# alone — kept explicit so an unrecognized/future purpose falls back to the
# generic "otp" template via .get() below instead of a KeyError.
_OTP_PURPOSE_TEMPLATE_KEYS: dict[str, str] = {
    "register": "otp_register",
    "login": "otp_login",
    "web_login": "otp_web_login",
    "email_verify": "otp_email_verify",
    "reset": "otp_reset",
}


def send_otp_email(to_email: str, code: str, ttl_minutes: int = 5, purpose: str = "login") -> bool:
    template_key = _OTP_PURPOSE_TEMPLATE_KEYS.get(purpose, "otp")
    return send_notification_email(
        to_email=to_email,
        title=f"Your KIS verification code: {code}",
        body=f"Your code is {code}. Expires in {ttl_minutes} minutes.",
        template_key=template_key,
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


def send_gift_membership_email(
    to_email: str,
    gifter_name: str,
    tier_title: str,
    channel_name: str,
    redeem_code: str,
    expires_at: str,
    message: str | None = None,
) -> bool:
    message_html = (
        f'<p style="font-style:italic;">"{html.escape(message)}"</p>' if message else ""
    )
    return send_notification_email(
        to_email=to_email,
        title=f"{gifter_name} sent you a KIS membership gift!",
        body=(
            f"{gifter_name} gifted you a {tier_title} membership on {channel_name}. "
            f"Redeem code: {redeem_code}. Expires {expires_at}."
        ),
        template_key="gift_membership",
        context={
            "gifter_name": html.escape(gifter_name),
            "tier_title": html.escape(tier_title),
            "channel_name": html.escape(channel_name),
            "redeem_code": redeem_code,
            "expires_at": expires_at,
            "message_html": message_html,
        },
    )


def send_website_form_notification_email(
    to_email: str, website_name: str, page_title: str, form_title: str, fields: dict,
) -> bool:
    fields_html = "".join(
        f"<p><strong>{html.escape(str(k))}:</strong> {html.escape(str(v))}</p>" for k, v in fields.items()
    )
    return send_notification_email(
        to_email=to_email,
        title=f"New form response on {website_name}",
        body=f"New {form_title} response on {website_name} ({page_title}).",
        template_key="website_form_submission",
        context={
            "website_name": website_name,
            "page_title": page_title,
            "form_title": form_title,
            "fields_html": fields_html,
        },
    )


def send_digest_email(to_email: str, items: list[dict]) -> bool:
    """items: [{"title": str, "summary": str}, ...]. Builds a real <ul> for
    the HTML part — the previous digest send passed a "\\n".join(...)
    plain-text blob as `body` straight into the generic "default" template
    (a single <p>{body}</p>), and HTML collapses literal newlines, so every
    digest email rendered as one run-on line instead of a list."""
    items_html = "".join(
        f"<li><strong>{html.escape(str(item.get('title') or ''))}:</strong> "
        f"{html.escape(str(item.get('summary') or ''))}</li>"
        for item in items
    )
    lines = "\n".join(f"• {item.get('title')}: {item.get('summary')}" for item in items)
    return send_notification_email(
        to_email=to_email,
        title=f"Your KIS digest — {len(items)} update(s)",
        body=lines,
        template_key="digest",
        context={"count": len(items), "items_html": items_html},
    )


def send_livestream_guest_invite_email(
    to_email: str,
    inviter_name: str,
    channel_name: str,
    stream_title: str,
    role: str,
    invite_url: str,
    scheduled_start_at: str | None = None,
) -> bool:
    schedule_html = f"<p>Scheduled to start: <strong>{html.escape(scheduled_start_at)}</strong></p>" if scheduled_start_at else ""
    return send_notification_email(
        to_email=to_email,
        title=f"{inviter_name} invited you to join {stream_title} on KIS",
        body=f"{inviter_name} invited you as a {role} on {channel_name}'s livestream: {stream_title}. Join: {invite_url}",
        template_key="livestream_guest_invite",
        context={
            "inviter_name": html.escape(inviter_name),
            "channel_name": html.escape(channel_name),
            "stream_title": html.escape(stream_title),
            "role": html.escape(role),
            "invite_url": invite_url,
            "schedule_html": schedule_html,
        },
    )
