
from django.db import transaction
from django.utils import timezone
from . import models
from .realtime import notify_main_tab_badges_updated
import datetime
import logging

logger = logging.getLogger(__name__)

MANDATORY_DELIVERY_CHANNELS = ("IN_APP", "PUSH")

BADGE_SOURCE_TOKENS: dict[str, tuple[str, ...]] = {
    "bible": ("bible", "reading", "meditation", "devotional"),
    "broadcast": ("broadcast", "channel", "course", "lesson", "product", "market", "shop", "event", "education", "health", "institution"),
    "partners": ("partner", "community", "partner_group", "group_message"),
    "messages": ("message", "chat", "conversation"),
    "profile": ("profile", "account", "verification", "general"),
}

URGENCY_LABELS: dict[str, tuple[str, ...]] = {
    "spiritual": ("bible", "prayer", "meditation", "devotional", "reading"),
    "health": ("health", "medical", "appointment", "session", "emergency", "clinical"),
    "learning": ("education", "course", "lesson", "class", "assessment", "learning"),
    "commerce": ("market", "shop", "product", "service", "order", "payment"),
    "social": ("message", "chat", "conversation", "partner", "community", "broadcast", "channel"),
    "trust": ("profile", "account", "verification", "security", "badge"),
}


def infer_urgency_label(notification_type=None, target_type=None, context=None) -> str:
    context = context or {}
    explicit = str(context.get("urgency_label") or context.get("category") or "").strip().lower()
    if explicit:
        return explicit[:40]
    haystack = f"{notification_type or ''} {target_type or ''} ".lower()
    haystack += " ".join(str(value).lower() for value in context.values() if isinstance(value, (str, int, float)))
    for label, tokens in URGENCY_LABELS.items():
        if any(token in haystack for token in tokens):
            return label
    return "general"


def should_suppress(user_id, notification_type, context=None):
    # Basic suppression logic: quiet hours, snoozes, and user rules.
    # In production, tie into user preferences from account app and ML suppression models.
    rules = models.NotificationRule.objects.filter(user_id=user_id, enabled=True)
    now = timezone.now()
    for r in rules:
        if r.type and r.type != notification_type:
            continue
        # check quiet hours
        q = r.schedule_json.get("quiet_hours")
        if q:
            start = q.get("start")
            end = q.get("end")
            if start and end:
                # naive local time check — production should normalize timezones
                now_time = now.time()
                start_t = datetime.time.fromisoformat(start)
                end_t = datetime.time.fromisoformat(end)
                if start_t <= now_time <= end_t:
                    return True
    return False


def user_notification_preferences(user_id) -> dict:
    rules = models.NotificationRule.objects.filter(user_id=user_id, is_deleted=False)
    global_rule = rules.filter(
        type__isnull=True,
        target_type__isnull=True,
        target_id__isnull=True,
        condition_json={},
    ).order_by("-updated_at").first()
    schedule = global_rule.schedule_json if global_rule and isinstance(global_rule.schedule_json, dict) else {}
    quiet_hours = schedule.get("quiet_hours") if isinstance(schedule.get("quiet_hours"), dict) else {}
    digest = schedule.get("digest") if isinstance(schedule.get("digest"), dict) else {}
    source_rules = []
    for rule in rules.exclude(condition_json={}).order_by("-updated_at")[:100]:
        condition = rule.condition_json if isinstance(rule.condition_json, dict) else {}
        source = condition.get("source") or condition.get("badge_source")
        if not source:
            continue
        source_rules.append(
            {
                "id": str(rule.id),
                "source": str(source),
                "enabled": rule.enabled,
                "priority": rule.priority or "",
                "channels": rule.channels_json or [],
                "schedule": rule.schedule_json or {},
            }
        )
    return {
        "quiet_hours": quiet_hours,
        "digest": digest,
        "source_rules": source_rules,
        "child_youth_safe_defaults": True,
    }



def _normalize_channels(channel=None, preferred_channels=None) -> list[str]:
    channels: list[str] = []
    for required in MANDATORY_DELIVERY_CHANNELS:
        if required not in channels:
            channels.append(required)
    for candidate in preferred_channels or []:
        normalized = str(candidate or "").strip().upper()
        if normalized and normalized not in channels:
            channels.append(normalized)
    normalized_channel = str(channel or "").strip().upper()
    if normalized_channel and normalized_channel not in channels:
        channels.append(normalized_channel)
    return channels


def infer_badge_source(notification_type=None, target_type=None, context=None, explicit_source=None) -> str:
    """Infer a stable main-tab badge source from existing notification fields."""
    context = context or {}
    source = str(explicit_source or context.get("source") or context.get("badge_source") or "").strip().lower()
    if source:
        aliases = {
            "education": "broadcast",
            "course": "broadcast",
            "lesson": "broadcast",
            "health": "broadcast",
            "market": "broadcast",
            "shop": "broadcast",
            "product": "broadcast",
            "service": "broadcast",
            "order": "broadcast",
            "channel_content": "broadcast",
            "community": "partners",
            "partner_group": "partners",
            "account": "profile",
            "verification": "profile",
        }
        return aliases.get(source, source)

    haystack = f"{notification_type or ''} {target_type or ''} ".lower()
    haystack += " ".join(str(value).lower() for value in context.values() if isinstance(value, (str, int, float)))
    for badge_source, tokens in BADGE_SOURCE_TOKENS.items():
        if any(token in haystack for token in tokens):
            return badge_source
    return "profile"


def normalize_notification_context(notification_type=None, target_type=None, target_id=None, context=None, source=None) -> dict:
    context_data = dict(context or {})
    badge_source = infer_badge_source(notification_type=notification_type, target_type=target_type, context=context_data, explicit_source=source)
    context_data.setdefault("source", badge_source)
    context_data.setdefault("badge_source", badge_source)
    if notification_type:
        context_data.setdefault("type", str(notification_type))
        context_data.setdefault("notification_type", str(notification_type))
    context_data.setdefault(
        "urgency_label",
        infer_urgency_label(notification_type=notification_type, target_type=target_type, context=context_data),
    )
    if target_type:
        context_data.setdefault("target_type", str(target_type))
    if target_id:
        context_data.setdefault("target_id", str(target_id))
    return context_data


def ensure_notification_deliveries(notification: models.Notification, channels=None):
    """Ensure the central in-app notification has its required delivery rows."""
    existing = set(notification.deliveries.values_list("channel", flat=True))
    for ch in _normalize_channels(notification.channel, channels):
        if ch not in existing:
            models.NotificationDelivery.objects.create(notification=notification, channel=ch)
            existing.add(ch)


@transaction.atomic
def create_notification(user_id, type, template_key=None, context=None, channel=None, priority="MEDIUM", dedup_key=None, **kwargs):
    """Create a notification and schedule deliveries. Deduplication key avoids duplicates."""
    context_data = normalize_notification_context(
        notification_type=type,
        target_type=kwargs.get("target_type"),
        target_id=kwargs.get("target_id"),
        context=context,
        source=kwargs.get("source"),
    )

    # dedupe
    if dedup_key:
        existing = models.Notification.objects.filter(user_id=user_id, dedup_key=dedup_key, is_deleted=False)
        if existing.exists():
            notif = existing.first()
            ensure_notification_deliveries(notif, kwargs.get("channels"))
            return notif

    title = kwargs.get("title")
    body = kwargs.get("body")
    template = None
    if template_key:
        try:
            template = models.NotificationTemplate.objects.get(key=template_key)
            t, b = template.render(context_data)
            title = title or t
            body = body or b
        except models.NotificationTemplate.DoesNotExist:
            template = None

    notif = models.Notification.objects.create(
        user_id=user_id,
        template=template,
        type=type,
        title=title or "",
        body=body or "",
        target_type=kwargs.get("target_type"),
        target_id=kwargs.get("target_id"),
        channel="IN_APP",
        priority=priority,
        actions_json=kwargs.get("actions_json", []),
        personalization_score=kwargs.get("personalization_score", 0.0),
        sentiment=kwargs.get("sentiment", "NEUTRAL"),
        reward_points=kwargs.get("reward_points"),
        is_snoozed=kwargs.get("is_snoozed", False),
        snoozed_until=kwargs.get("snoozed_until"),
        context_data=context_data,
        dedup_key=dedup_key,
    )

    # Create delivery records. Every in-app notification also gets a related push delivery.
    preferred_channels = []
    rules = models.NotificationRule.objects.filter(user_id=user_id, enabled=True)
    if rules.exists():
        preferred = rules.first().channels_json or []
        if preferred:
            preferred_channels = preferred

    requested_channels = kwargs.get("channels") or preferred_channels or [channel or (template.default_channel if template else None)]
    ensure_notification_deliveries(notif, requested_channels)

    # Immediately schedule a worker to process deliveries (or enqueue Celery task in prod)
    from .tasks import process_notification_delivery

    try:
        process_notification_delivery.delay(str(notif.id))
    except Exception:
        logger.exception("Unable to enqueue notification delivery for %s", notif.id)

    notify_main_tab_badges_updated(
        [str(user_id)],
        source="notifications",
        reason="created",
        extra={"notification_id": str(notif.id), "type": str(type or "")},
    )

    return notif
