from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.partners.models import Partner
from apps.partners.services import ensure_default_organization_app


@receiver(post_save, sender=Partner)
def ensure_partner_bible_app(sender, instance: Partner, created: bool, **kwargs):
    if not created:
        return
    ensure_default_organization_app(instance)
