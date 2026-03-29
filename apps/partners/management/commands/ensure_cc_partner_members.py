from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure every user is a member of the Christian Community (CC) partner."

    def handle(self, *args, **options):
        from apps.partners.seed import ensure_kis_partner
        from apps.partners.models import Partner, PartnerJoinConfig
        from apps.partners.services import (
            ensure_partner_policy,
            ensure_default_partner_roles,
            apply_partner_policy,
        )

        ensure_kis_partner()
        for partner in Partner.objects.all():
            PartnerJoinConfig.objects.get_or_create(partner=partner)
            ensure_partner_policy(partner)
            ensure_default_partner_roles(partner)
            apply_partner_policy(partner)
        self.stdout.write(self.style.SUCCESS("CC partner membership enforced for all users."))
