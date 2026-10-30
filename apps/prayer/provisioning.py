# apps/prayer/provisioning.py
"""Idempotent provisioning of the 24 permanent Prayer Calendar rooms.
Safe to run any number of times — running it twice creates nothing new
for a watch that already exists; it only fills in whatever's still
missing (a group that exists but never got its welcome message, say,
after a prior partial run failed partway through)."""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from apps.chat.tasks import send_system_message_as_user
from apps.groups.models import Group, GroupJoinPolicy, GroupRole
from apps.groups.services import create_group_with_conversation, reactivate_group_membership
from apps.partners.seed import get_go_user

from .models import PrayerWatchGroup
from .watch_names import KIS_WELCOME_MESSAGE, WATCH_NAMES

logger = logging.getLogger(__name__)


class ProvisioningError(Exception):
    pass


def get_kis_system_user():
    """The official KIS announcer identity, distinct from both the GO
    (a real human's personal account) and apps.partners.seed's
    KCAN/'KIS system user' (whose Partner ownership
    apps.partners.seed.ensure_go_identity deliberately reassigns to GO
    the moment GO exists — confirmed by a direct test during this
    project: once a GO user exists, Partner.objects.get(slug__in=
    LEGACY_DEFAULT_PARTNER_SLUGS).owner IS the GO, not a separate
    account at all, which is the opposite of what an announcer identity
    needs). No pre-existing account in this codebase stays a genuinely
    separate, permanent, non-human-login identity the way this feature's
    spec requires ('do not use a developer's personal account') — so
    this implements one, reusing the exact same infrastructure pattern
    apps.partners.seed._ensure_kis_partner already uses for its own
    system user (plain User row, set_unusable_password() so it can never
    be logged into, is_staff/is_superuser left False since this account
    has no business in the staff admin), just without that function's
    Partner/GO entanglement.
    """
    from apps.accounts.models import User

    email = getattr(settings, "PRAYER_KIS_ANNOUNCER_EMAIL", "kis-announcer@kis.app")
    username = getattr(settings, "PRAYER_KIS_ANNOUNCER_USERNAME", "kis_announcer")
    display_name = getattr(settings, "PRAYER_KIS_ANNOUNCER_DISPLAY_NAME", "KIS")
    country = getattr(settings, "PRAYER_KIS_ANNOUNCER_COUNTRY", "CM")

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        user = User.objects.filter(username__iexact=username).first()
    if user:
        return user

    user = User.objects.create(
        email=email, username=username, display_name=display_name, country=country,
        is_staff=False, is_superuser=False, is_active=True,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


@transaction.atomic
def _ensure_watch_group(hour: int, name: str, go_user) -> tuple[PrayerWatchGroup, bool]:
    existing = PrayerWatchGroup.objects.filter(hour=hour).select_related("group").first()
    if existing:
        return existing, False

    group = create_group_with_conversation(
        owner=go_user, name=name, slug=f"prayer-watch-{hour:02d}",
    )
    # Open to every authenticated user — no subscription/partner-tier
    # restriction. create_group_with_conversation's own default
    # (GroupJoinPolicy.REQUEST) is correct for a normal user-created
    # group but wrong here.
    group.join_policy = GroupJoinPolicy.OPEN
    group.save(update_fields=["join_policy"])

    watch_group = PrayerWatchGroup.objects.create(hour=hour, group=group, name=name)
    return watch_group, True


def provision_prayer_watches(*, post_welcome_messages: bool = True) -> dict:
    """Creates (or, on a re-run, finds) all 24 permanent watches, and
    posts the KIS welcome message into each one exactly once. Returns a
    per-hour report so a management command / test can assert on exactly
    what happened rather than just trusting a silent success.
    """
    go_user = get_go_user()
    if not go_user:
        raise ProvisioningError(
            "The GO account has not logged in yet (apps.partners.seed.GO_EMAIL/"
            "GO_PHONE match no User row). The GO must log in at least once "
            "before Prayer Calendar groups can be provisioned, since every "
            "watch's Group.owner must be a real, existing User."
        )
    kis_user = get_kis_system_user()

    report = {"created": [], "already_existed": [], "welcome_sent": [], "welcome_failed": []}

    for hour, name in WATCH_NAMES:
        watch_group, created = _ensure_watch_group(hour, name, go_user)
        (report["created"] if created else report["already_existed"]).append(hour)

        group: Group = watch_group.group
        # GO is already Group.owner, but create_group_with_conversation
        # doesn't itself write a GroupMembership row for the owner (only
        # the backing Conversation's ConversationMember) — see
        # apps.groups.services' own docstring gap, worth knowing if
        # that's ever changed upstream. Backfilling it here keeps the
        # group's own member-list API (GroupMembership-backed) correct.
        reactivate_group_membership(group, go_user, role=GroupRole.OWNER)
        reactivate_group_membership(group, kis_user, role=GroupRole.MEMBER)

        if post_welcome_messages:
            client_id = f"prayer-watch-welcome:{group.conversation_id}"
            try:
                send_system_message_as_user(
                    conversation_id=str(group.conversation_id),
                    sender_id=str(kis_user.id),
                    text=KIS_WELCOME_MESSAGE,
                    client_id=client_id,
                )
                report["welcome_sent"].append(hour)
            except Exception as exc:
                logger.warning("[prayer.provisioning] welcome message failed for hour=%s: %s", hour, exc)
                report["welcome_failed"].append(hour)

    return report
