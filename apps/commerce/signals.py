import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Product, Shop, ShopFollow, AIRecommendation, ShopRole, ShopTeamMember
from .tasks import compute_recommendations
from apps.notifications.services import notify_engagement

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Product)
def on_product_save(sender, instance, created, **kwargs):
    # update AIRecommendation cache or enqueue recompute for followers
    if created:
        # naive: compute recommendations for users following shop (background).
        # This runs synchronously inside whatever request/command saved the
        # Product, so a broker outage must not propagate - bounded by
        # CELERY_BROKER_CONNECTION_TIMEOUT, caught here rather than left to
        # crash the save() call.
        try:
            compute_recommendations.delay(str(instance.shop.owner.id))
        except Exception:
            logger.exception("Failed to enqueue recommendation recompute for shop owner %s", instance.shop.owner_id)


@receiver(post_save, sender=Shop)
def ensure_owner_membership(sender, instance, **kwargs):
    if not instance.owner_id:
        return
    ShopTeamMember.objects.update_or_create(
        shop=instance,
        user=instance.owner,
        defaults={'role': ShopRole.OWNER, 'is_active': True},
    )


@receiver(post_save, sender=ShopFollow)
def notify_shop_followed(sender, instance, created, **kwargs):
    if not created:
        return
    notify_engagement(
        owner_user_id=instance.shop.owner_id,
        actor_user=instance.user,
        notification_type="commerce.shop.followed",
        verb=f"started following {instance.shop.name}",
        target_type="shop",
        target_id=instance.shop_id,
        target_title=instance.shop.name,
    )
