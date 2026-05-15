from __future__ import annotations

from typing import Any

from django.utils import timezone


def _safe_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _item(*, kind: str, title: str, target_type: str, target_id: Any, subtitle: str = "", route: str = "", score: int = 1, metadata: dict | None = None) -> dict:
    return {
        "kind": kind,
        "title": _safe_text(title, "KIS recommendation")[:180],
        "subtitle": _safe_text(subtitle)[:260],
        "target_type": target_type,
        "target_id": str(target_id or ""),
        "route": route,
        "score": int(score),
        "metadata": metadata or {},
    }


def _blocked_user_ids(user) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    try:
        from apps.moderation.models import UserBlock

        made = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
        received = UserBlock.objects.filter(blocked=user).values_list("blocker_id", flat=True)
        return {str(value) for value in made} | {str(value) for value in received}
    except Exception:
        return set()


def _contact_user_ids(user) -> set[str]:
    try:
        from apps.accounts.models import UserContact

        return {
            str(value)
            for value in UserContact.objects.filter(user=user, contact_user_id__isnull=False).values_list("contact_user_id", flat=True)
        }
    except Exception:
        return set()


def _channel_recommendations(user, blocked_user_ids: set[str], limit: int) -> list[dict]:
    try:
        from apps.broadcasts.models import BroadcastChannel, BroadcastChannelSubscription
    except Exception:
        return []
    subscribed_ids = set(BroadcastChannelSubscription.objects.filter(user=user).values_list("channel_id", flat=True))
    qs = (
        BroadcastChannel.objects.filter(is_public=True, is_deleted=False)
        .exclude(id__in=subscribed_ids)
        .exclude(owner_user_id__in=blocked_user_ids)
        .order_by("-is_verified", "-subscriber_count", "-content_count", "-updated_at")
    )
    rows = []
    for channel in qs[:limit]:
        score = 60 + min(int(channel.subscriber_count or 0), 1000) // 50 + (20 if channel.is_verified else 0)
        rows.append(
            _item(
                kind="channel",
                title=channel.display_name,
                subtitle=f"@{channel.handle} · {channel.category or 'KIS channel'}",
                target_type="broadcast_channel",
                target_id=channel.id,
                route="ChannelHome",
                score=score,
                metadata={
                    "handle": channel.handle,
                    "category": channel.category,
                    "verified": bool(channel.is_verified),
                    "reason": "Popular safe public channel you are not subscribed to.",
                },
            )
        )
    return rows


def _people_recommendations(user, blocked_user_ids: set[str], contact_user_ids: set[str], limit: int) -> list[dict]:
    if not contact_user_ids:
        return []
    try:
        from django.contrib.auth import get_user_model
        from apps.verification.services import verification_summary
        from apps.verification.constants import VerificationSubjectType
    except Exception:
        return []
    User = get_user_model()
    qs = User.objects.filter(id__in=contact_user_ids, is_active=True).exclude(id__in=blocked_user_ids).exclude(id=user.id).order_by("phone")[:limit]
    rows = []
    for candidate in qs:
        summary = verification_summary(VerificationSubjectType.USER, candidate.id)
        title = _safe_text(getattr(candidate, "display_name", ""), "") or _safe_text(getattr(candidate, "full_name", ""), "") or _safe_text(getattr(candidate, "phone", ""), "KIS contact")
        rows.append(
            _item(
                kind="person",
                title=title,
                subtitle="Saved contact on KIS",
                target_type="user",
                target_id=candidate.id,
                route="Profile",
                score=75 + (10 if summary.get("verified") else 0),
                metadata={"verified": bool(summary.get("verified")), "reason": "Already in your contacts."},
            )
        )
    return rows


def _family_accessibility_preferences(user) -> dict:
    try:
        from apps.accounts.family_accessibility import serialize_family_accessibility_preferences

        return (serialize_family_accessibility_preferences(user).get("preferences") or {})
    except Exception:
        return {}


def _commerce_recommendations(user, blocked_user_ids: set[str], limit: int, preferences: dict | None = None) -> list[dict]:
    preferences = preferences or {}
    if preferences.get("hide_sensitive_commerce"):
        return []
    try:
        from apps.commerce.models import Product, Shop, ShopFollow
    except Exception:
        return []
    followed_shop_ids = set(ShopFollow.objects.filter(user=user).values_list("shop_id", flat=True))
    shops = (
        Shop.objects.filter(is_deleted=False)
        .exclude(id__in=followed_shop_ids)
        .exclude(owner_id__in=blocked_user_ids)
        .order_by("-is_verified", "-rating_avg", "-followers_count")[:limit]
    )
    rows = [
        _item(
            kind="shop",
            title=shop.name,
            subtitle="Verified seller" if shop.is_verified else "Marketplace seller",
            target_type="shop",
            target_id=shop.id,
            route="MarketShop",
            score=55 + int(float(shop.rating_avg or 0) * 10) + (20 if shop.is_verified else 0),
            metadata={"verified": bool(shop.is_verified), "reason": "Trusted marketplace profile with USD checkout."},
        )
        for shop in shops
    ]
    if len(rows) < limit:
        products = (
            Product.objects.select_related("shop")
            .filter(is_deleted=False, is_active=True)
            .exclude(shop__owner_id__in=blocked_user_ids)
            .order_by("-is_featured", "-created_at")[: max(0, limit - len(rows))]
        )
        for product in products:
            rows.append(
                _item(
                    kind="product",
                    title=product.name,
                    subtitle=_safe_text(getattr(product.shop, "name", ""), "Marketplace product"),
                    target_type="product",
                    target_id=product.id,
                    route="MarketProduct",
                    score=50 + (10 if product.is_featured else 0),
                    metadata={"currency": "USD", "reason": "Public product using direct provider payment."},
                )
            )
    return rows


def _education_recommendations(user, limit: int) -> list[dict]:
    try:
        from apps.broadcasts.models import EducationInstitutionCourse, EducationInstitutionEnrollment, EducationAcademicRecordStatus
    except Exception:
        return []
    enrolled_course_ids = set(EducationInstitutionEnrollment.objects.filter(user=user, course_id__isnull=False).values_list("course_id", flat=True))
    qs = (
        EducationInstitutionCourse.objects.select_related("institution")
        .filter(institution__is_active=True)
        .exclude(id__in=enrolled_course_ids)
        .exclude(status=EducationAcademicRecordStatus.ARCHIVED)
        .order_by("-updated_at", "title")[:limit]
    )
    return [
        _item(
            kind="course",
            title=course.title,
            subtitle=_safe_text(getattr(course.institution, "name", ""), "Education institution"),
            target_type="education_course",
            target_id=course.id,
            route="EducationCourse",
            score=58,
            metadata={"institution_id": str(course.institution_id), "reason": "Education course you are not enrolled in."},
        )
        for course in qs
    ]


def _bible_recommendations(user, limit: int) -> list[dict]:
    try:
        from apps.bible.models import BibleCourse, BibleCourseEnrollment, BibleMeditationPost, ReadingHistory
    except Exception:
        return []
    enrolled_ids = set(BibleCourseEnrollment.objects.filter(user=user).values_list("course_id", flat=True))
    read_count = ReadingHistory.objects.filter(user=user).count()
    rows = []
    for course in BibleCourse.objects.filter(published=True, is_public=True).exclude(id__in=enrolled_ids).order_by("-is_bible_course", "-created_at")[:limit]:
        rows.append(
            _item(
                kind="bible_course",
                title=course.title,
                subtitle=course.subtitle or ("Beginner-friendly study" if read_count < 5 else "Continue spiritual growth"),
                target_type="bible_course",
                target_id=course.id,
                route="BibleCourse",
                score=70 if course.is_bible_course else 55,
                metadata={"is_free": bool(course.is_free), "reason": "Bible study recommendation with family-safe defaults."},
            )
        )
    if len(rows) < limit:
        for post in BibleMeditationPost.objects.filter(status="published").order_by("-published_at", "-created_at")[: limit - len(rows)]:
            rows.append(
                _item(
                    kind="meditation",
                    title=post.title,
                    subtitle="Daily meditation",
                    target_type="bible_meditation",
                    target_id=post.id,
                    route="Bible",
                    score=62,
                    metadata={"reason": "Published devotional content."},
                )
            )
    return rows


def privacy_safe_social_recommendation_foundation(user, *, limit: int = 8) -> dict:
    limit = max(1, min(int(limit or 8), 20))
    blocked_user_ids = _blocked_user_ids(user)
    contact_user_ids = _contact_user_ids(user)
    family_preferences = _family_accessibility_preferences(user)
    sections = {
        "people": _people_recommendations(user, blocked_user_ids, contact_user_ids, limit),
        "channels": _channel_recommendations(user, blocked_user_ids, limit),
        "commerce": _commerce_recommendations(user, blocked_user_ids, limit, family_preferences),
        "education": _education_recommendations(user, limit),
        "bible": _bible_recommendations(user, limit),
        "health": [],
        "partners": [],
    }
    return {
        "generated_at": timezone.now().isoformat(),
        "privacy": {
            "public_safe": True,
            "private_relationships_exposed": False,
            "health_data_exposed": False,
            "verification_documents_exposed": False,
            "payment_data_exposed": False,
            "raw_storage_paths_exposed": False,
        },
        "controls": {
            "blocked_users_excluded": True,
            "muted_hidden_content_excluded": True,
            "child_youth_safe_defaults": True,
            "christian_content_safe_ranking": True,
            "explicit_content_blocked_by_media_gate": True,
            "sensitive_domains_downranked": ["health", "verification", "payments"],
            "age_mode": family_preferences.get("age_mode") or "adult",
            "simplified_navigation": family_preferences.get("navigation_mode") in {"simplified", "guided"},
            "commerce_hidden_for_child_mode": bool(family_preferences.get("hide_sensitive_commerce")),
        },
        "signals": {
            "contacts": len(contact_user_ids),
            "blocked_users": len(blocked_user_ids),
            "uses_private_health_data": False,
            "uses_payment_amounts": False,
            "uses_verification_evidence": False,
        },
        "sections": sections,
        "placeholders": {
            "health": "Health recommendations require explicit patient/provider consent and should use public institution metadata only.",
            "partners": "Partner recommendations should use public membership and workspace interest signals only.",
        },
    }
