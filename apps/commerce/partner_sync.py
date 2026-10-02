# apps/commerce/partner_sync.py
#
# Partner Account auto-add + notification for Shops — scoped-down sibling
# of apps.broadcasts.education_communication_sync for this domain, same
# reasoning as apps.health_ops.partner_sync: Shop has no per-entity
# communication-room infrastructure (no community/group/channel FK, and
# nothing else here auto-creates one), so this only does the two things
# that don't depend on that: add the user to the shop's Partner Account,
# and notify them.
from __future__ import annotations

from apps.partners.models import PartnerMembership, PartnerMembershipStatus


def ensure_partner_membership(shop, user) -> bool:
    """Idempotent get-or-create on the partner+user unique_together.
    Never removes membership on its own, for the same reason as the
    Education/Health versions: following a shop is a lightweight action
    that shouldn't quietly evict someone who may still be a legitimate
    member elsewhere at the same partner. Returns whether a new row was
    created, so the caller can notify only once per real addition."""
    if not shop.partner_id:
        return False
    _membership, created = PartnerMembership.objects.get_or_create(
        partner=shop.partner,
        user=user,
        defaults={"status": PartnerMembershipStatus.MEMBER},
    )
    return created


def notify_added_to_partner(*, shop, user) -> None:
    if not shop.partner_id:
        return
    from apps.notifications.services import create_notification

    create_notification(
        user_id=user.id,
        type="SHOP_PARTNER_MEMBERSHIP_ADDED",
        title=f"You've been added to {shop.name}",
        body=f"{shop.name} added you to its Partner Account — tap to see your Partner sections.",
        target_type="partner",
        target_id=str(shop.partner_id),
        priority="MEDIUM",
        dedup_key=f"shop_partner_added:{shop.partner_id}:{user.id}",
        context={"shop_id": str(shop.id)},
    )


def sync_follower_partner_membership(*, shop, user) -> None:
    """Call after a user follows/joins a shop (see
    ShopViewSet.join). A no-op when the shop has no Partner Account
    connected."""
    if ensure_partner_membership(shop, user):
        notify_added_to_partner(shop=shop, user=user)
