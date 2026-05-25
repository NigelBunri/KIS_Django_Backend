import logging

from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)

KCAN_PARTNER_NAME = "KCAN, Kingdom Citizens & Ambassadors Network"
KCAN_PARTNER_SLUG = "kcan"
KCAN_PARTNER_DESCRIPTION = "Default KCAN system partner account and admin control hub for the app."
KCAN_SYSTEM_USERNAME = "kcan"
KCAN_SYSTEM_DISPLAY_NAME = "KCAN"
LEGACY_DEFAULT_PARTNER_SLUGS = ["kcan", "cc", "kis", "christian-community-cc"]

# Immutable platform General Overseer (GO) identity.
GO_EMAIL = "nigelbunribah@gmail.com"
GO_PHONE = "654008266"
GO_COUNTRY = "CM"
GO_USERNAME = "nigel"


def ensure_go_identity() -> None:
    """Enforce that the GO user always has superuser access and owns KCAN."""
    try:
        _ensure_go_identity()
    except (OperationalError, ProgrammingError):
        return
    except Exception as exc:
        logger.warning("[partners.seed] Unable to enforce GO identity: %s", exc)


def ensure_kcan_verified() -> None:
    """Ensure KCAN always has active verified_partner and official_partner badges."""
    try:
        _ensure_kcan_verified()
    except (OperationalError, ProgrammingError):
        return
    except Exception as exc:
        logger.warning("[partners.seed] Unable to verify KCAN: %s", exc)


def _ensure_kcan_verified() -> None:
    from apps.partners.models import Partner
    from apps.verification.constants import (
        VerificationBadgeCode,
        VerificationBadgeStatus,
        VerificationCaseStatus,
        VerificationSubjectType,
    )
    from apps.verification.models import VerificationBadge, VerificationSubject

    partner = Partner.objects.filter(slug=KCAN_PARTNER_SLUG).first()
    if not partner:
        return

    subject, _ = VerificationSubject.objects.get_or_create(
        subject_type=VerificationSubjectType.PARTNER,
        subject_id=partner.id,
        defaults={
            "owner": partner.owner,
            "display_name": partner.name,
            "country": "CM",
            "current_status": VerificationCaseStatus.APPROVED,
            "current_level": "platform",
        },
    )

    # Always keep status and level correct even if it already existed
    changed = []
    if subject.current_status != VerificationCaseStatus.APPROVED:
        subject.current_status = VerificationCaseStatus.APPROVED
        changed.append("current_status")
    if subject.current_level != "platform":
        subject.current_level = "platform"
        changed.append("current_level")
    if changed:
        subject.save(update_fields=[*changed, "updated_at"])

    # Issue the two platform badges
    for code in (VerificationBadgeCode.VERIFIED_PARTNER, VerificationBadgeCode.OFFICIAL_PARTNER):
        badge, created = VerificationBadge.objects.get_or_create(
            subject=subject,
            code=code,
            defaults={
                "status": VerificationBadgeStatus.ACTIVE,
                "public": True,
                "metadata": {"source": "platform", "immutable": True},
            },
        )
        if not created and badge.status != VerificationBadgeStatus.ACTIVE:
            badge.status = VerificationBadgeStatus.ACTIVE
            badge.revoked_at = None
            badge.revoke_reason = ""
            badge.save(update_fields=["status", "revoked_at", "revoke_reason", "updated_at"])
            logger.info("[partners.seed] Re-activated KCAN badge: %s", code)


def _ensure_go_identity() -> None:
    from apps.accounts.models import User
    from apps.partners.models import Partner

    user = (
        User.objects.filter(email__iexact=GO_EMAIL).first()
        or User.objects.filter(phone=GO_PHONE).first()
    )
    if not user:
        logger.info("[partners.seed] GO user not found — will be enforced on first login.")
        return

    changed = []
    if not user.is_superuser:
        user.is_superuser = True
        changed.append("is_superuser")
    if not user.is_staff:
        user.is_staff = True
        changed.append("is_staff")
    if changed:
        user.save(update_fields=changed)
        logger.info("[partners.seed] GO identity enforced on %s: %s", user.email, changed)

    # Ensure GO is the owner of KCAN
    kcan = Partner.objects.filter(slug=KCAN_PARTNER_SLUG).first()
    if kcan and kcan.owner_id != user.id:
        kcan.owner = user
        kcan.save(update_fields=["owner"])
        logger.info("[partners.seed] KCAN ownership transferred to GO (%s)", user.email)


def ensure_kis_partner() -> None:
    try:
        _ensure_kis_partner()
        ensure_go_identity()
        ensure_kcan_verified()
    except (OperationalError, ProgrammingError):
        # Database not ready (e.g. during migrations/startup).
        return
    except Exception as exc:
        logger.warning("[partners.seed] Unable to ensure Kis partner: %s", exc)


def _ensure_kis_partner() -> None:
    from apps.accounts.models import User
    from apps.chat.models import (
        BaseConversationRole,
        Conversation,
        ConversationMember,
        ConversationSendPolicy,
        ConversationSettings,
        ConversationType,
    )
    from apps.chat.signals import conversation_created_notify
    from apps.partners.models import Partner, PartnerJoinConfig

    email = getattr(settings, "KIS_SYSTEM_USER_EMAIL", "support@kis.app")
    phone = getattr(settings, "KIS_SYSTEM_USER_PHONE", None)
    country = getattr(settings, "KIS_SYSTEM_USER_COUNTRY", "CM")
    username = getattr(settings, "KIS_SYSTEM_USER_USERNAME", KCAN_SYSTEM_USERNAME)
    display_name = getattr(settings, "KIS_SYSTEM_USER_DISPLAY_NAME", KCAN_SYSTEM_DISPLAY_NAME)
    password = getattr(settings, "KIS_SYSTEM_USER_PASSWORD", None)

    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()
    if not user and username:
        user = User.objects.filter(username__iexact=username).first()
    if not user and phone:
        user = User.objects.filter(phone=phone).first()

    if not user:
        if password:
            user = User.objects.create_superuser(
                email=email,
                password=password,
                country=country,
                phone=phone,
                username=username,
                display_name=display_name,
            )
        else:
            user = User.objects.create(
                email=email,
                phone=phone,
                username=username,
                display_name=display_name,
                country=country,
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])

    if not user.is_superuser or not user.is_staff:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

    with transaction.atomic():
        partner = Partner.objects.filter(slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).first()
        if not partner:
            partner = Partner.objects.create(
                name=KCAN_PARTNER_NAME,
                slug=KCAN_PARTNER_SLUG,
                description=KCAN_PARTNER_DESCRIPTION,
                owner=user,
            )
        else:
            desired_name = KCAN_PARTNER_NAME
            desired_slug = KCAN_PARTNER_SLUG
            desired_description = KCAN_PARTNER_DESCRIPTION
            updates = {}
            if partner.name != desired_name:
                updates["name"] = desired_name
            if partner.slug != desired_slug:
                updates["slug"] = desired_slug
            if partner.description != desired_description:
                updates["description"] = desired_description
            if updates:
                Partner.objects.filter(id=partner.id).update(**updates)
                partner.refresh_from_db()

        if partner.owner_id != user.id:
            partner.owner = user
            partner.save(update_fields=["owner"])

        PartnerJoinConfig.objects.get_or_create(partner=partner)
        from apps.partners.services import ensure_partner_policy, ensure_default_partner_roles
        from apps.partners.models import PartnerOrganizationApp, PartnerOrganizationAppType
        ensure_partner_policy(partner)
        ensure_default_partner_roles(partner)
        # The "KCAN Bible" org-app (type=BIBLE) is retired from auto-creation:
        # it duplicated the main Bible tab and showed the same BibleScreen.
        # Bible access for KCAN members is provided by the Bible tab in the
        # main navigation. The Bible admin panel in PartnersCenterPane covers
        # all content management. Existing records are preserved; new KCAN
        # setups no longer auto-create this redundant floating app.

        if partner.main_conversation_id:
            _ensure_kis_members(partner.main_conversation)
            return

        # Avoid triggering Celery/Redis requirements during startup seeding.
        from django.db.models.signals import post_save
        post_save.disconnect(conversation_created_notify, sender=Conversation)
        try:
            conversation = Conversation.objects.create(
                type=ConversationType.CHANNEL,
                title="Kis Broadcasts",
                description="Official KIS announcements and updates.",
                created_by=user,
            )
        finally:
            post_save.connect(conversation_created_notify, sender=Conversation)

        ConversationMember.objects.get_or_create(
            conversation=conversation,
            user=user,
            defaults={"base_role": BaseConversationRole.OWNER},
        )

        ConversationSettings.objects.get_or_create(
            conversation=conversation,
            defaults={"send_policy": ConversationSendPolicy.ADMINS_ONLY},
        )

        partner.main_conversation = conversation
        partner.save(update_fields=["main_conversation"])

        # Ensure all active users are members of the Kis partner conversation.
        _ensure_kis_members(conversation)


def _ensure_kis_members(conversation) -> None:
    from apps.accounts.models import User
    from apps.chat.models import ConversationMember, BaseConversationRole

    active_users = User.objects.values_list("id", flat=True)
    existing = set(
        ConversationMember.objects.filter(
            conversation=conversation,
            left_at__isnull=True,
        ).values_list("user_id", flat=True)
    )
    missing_ids = [uid for uid in active_users if uid not in existing]
    if not missing_ids:
        return

    ConversationMember.objects.bulk_create(
        [
            ConversationMember(
                conversation=conversation,
                user_id=uid,
                base_role=BaseConversationRole.MEMBER,
            )
            for uid in missing_ids
        ],
        ignore_conflicts=True,
    )


def ensure_user_in_cc_partner(user) -> None:
    from apps.partners.models import Partner
    from apps.chat.models import ConversationMember, BaseConversationRole

    partner = Partner.objects.filter(slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).select_related("main_conversation").first()
    if not partner or not partner.main_conversation_id:
        return
    ConversationMember.objects.get_or_create(
        conversation=partner.main_conversation,
        user=user,
        defaults={"base_role": BaseConversationRole.MEMBER},
    )


ensure_user_in_kis_partner = ensure_user_in_cc_partner
