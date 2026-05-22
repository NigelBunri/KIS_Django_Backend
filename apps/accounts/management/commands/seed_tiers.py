"""
Idempotent management command to seed AccountTier (accounts app) and BillingPlan +
Entitlement (tiers app) records.

Usage:
    python manage.py seed_tiers
    python manage.py seed_tiers --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

TIERS = [
    {
        'name': 'Basic',
        'slug': 'basic',
        'description': 'Free tier — core messaging, Bible, communities',
        'price_cents': 0,
        'currency': 'XAF',
        'features': {
            'max_shops': 0,
            'max_products_per_shop': 0,
            'max_channels': 0,
            'max_group_size': 50,
            'media_storage_mb': 500,
            'api_access': False,
            'custom_ai': False,
            'analytics': False,
            'analytics_basic': False,
            'analytics_advanced': False,
            'status_retention_days': 2,
            'broadcast_channels': 0,
            'live_streaming': False,
            'health_institution': False,
            'partner_dashboard': False,
            'partner_insight': False,
        },
    },
    {
        'name': 'Pro',
        'slug': 'pro',
        'description': 'Pro tier — content creation, analytics, channels',
        'price_cents': 490000,
        'currency': 'XAF',
        'features': {
            'max_shops': 1,
            'max_products_per_shop': 50,
            'max_channels': 2,
            'max_group_size': 200,
            'media_storage_mb': 5120,
            'api_access': False,
            'custom_ai': False,
            'analytics': True,
            'analytics_basic': True,
            'analytics_advanced': False,
            'status_retention_days': 7,
            'broadcast_channels': 2,
            'live_streaming': True,
            'health_institution': False,
            'partner_dashboard': False,
            'partner_insight': False,
        },
    },
    {
        'name': 'Business',
        'slug': 'business',
        'description': 'Business tier — marketplace, health institutions, partner tools',
        'price_cents': 1490000,
        'currency': 'XAF',
        'features': {
            'max_shops': 3,
            'max_products_per_shop': 500,
            'max_channels': 10,
            'max_group_size': 1000,
            'media_storage_mb': 51200,
            'api_access': True,
            'custom_ai': False,
            'analytics': True,
            'analytics_basic': True,
            'analytics_advanced': False,
            'status_retention_days': 30,
            'broadcast_channels': 10,
            'live_streaming': True,
            'health_institution': True,
            'partner_dashboard': True,
            'partner_insight': True,
        },
    },
    {
        'name': 'Business Pro',
        'slug': 'business-pro',
        'description': 'Business Pro — unlimited marketplace, AI integration, priority support',
        'price_cents': 4900000,
        'currency': 'XAF',
        'features': {
            'max_shops': 10,
            'max_products_per_shop': 5000,
            'max_channels': 50,
            'max_group_size': 5000,
            'media_storage_mb': 524288,
            'api_access': True,
            'custom_ai': True,
            'analytics': True,
            'analytics_basic': True,
            'analytics_advanced': True,
            'status_retention_days': 90,
            'broadcast_channels': 50,
            'live_streaming': True,
            'health_institution': True,
            'partner_dashboard': True,
            'partner_insight': True,
        },
    },
    {
        'name': 'Partner',
        'slug': 'partner',
        'description': 'Partner tier — organisation accounts with compliance tools',
        'price_cents': 9900000,
        'currency': 'XAF',
        'features': {
            'max_shops': 20,
            'max_products_per_shop': 10000,
            'max_channels': 100,
            'max_group_size': 10000,
            'media_storage_mb': 1048576,
            'api_access': True,
            'custom_ai': True,
            'analytics': True,
            'analytics_basic': True,
            'analytics_advanced': True,
            'status_retention_days': 365,
            'broadcast_channels': 100,
            'live_streaming': True,
            'health_institution': True,
            'partner_dashboard': True,
            'partner_insight': True,
            'dlp_enabled': True,
            'webhooks': True,
            'audit_log_export': True,
        },
    },
    {
        'name': 'Partner Pro',
        'slug': 'partner-pro',
        'description': 'Partner Pro — enterprise tier with dedicated support and SLA',
        'price_cents': 24900000,
        'currency': 'XAF',
        'features': {
            'max_shops': 100,
            'max_products_per_shop': 100000,
            'max_channels': 500,
            'max_group_size': 50000,
            'media_storage_mb': 10485760,
            'api_access': True,
            'custom_ai': True,
            'analytics': True,
            'analytics_basic': True,
            'analytics_advanced': True,
            'status_retention_days': 3650,
            'broadcast_channels': 500,
            'live_streaming': True,
            'health_institution': True,
            'partner_dashboard': True,
            'partner_insight': True,
            'dlp_enabled': True,
            'webhooks': True,
            'audit_log_export': True,
            'dedicated_support': True,
            'sla': True,
        },
    },
]


class Command(BaseCommand):
    help = 'Seed AccountTier and BillingPlan records. Idempotent — safe to run multiple times.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Print what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))
        self._seed_account_tiers(dry_run)
        self._seed_billing_plans(dry_run)

    # ------------------------------------------------------------------
    # AccountTier (apps.accounts)
    # ------------------------------------------------------------------
    def _seed_account_tiers(self, dry_run: bool) -> None:
        from apps.accounts.models import AccountTier

        self.stdout.write('\n=== AccountTier (apps.accounts) ===')
        created_count = 0
        updated_count = 0

        for tier_data in TIERS:
            name = tier_data['name']
            new_price = tier_data['price_cents']
            new_features = tier_data['features']

            if dry_run:
                exists = AccountTier.objects.filter(name=name).exists()
                action = 'UPDATE' if exists else 'CREATE'
                self.stdout.write(f'  [{action}] {name}  price_cents={new_price}')
                continue

            with transaction.atomic():
                obj, created = AccountTier.objects.get_or_create(
                    name=name,
                    defaults={
                        'price_cents': new_price,
                        'features_json': new_features,
                    },
                )
                if not created:
                    changed = False
                    if obj.price_cents != new_price:
                        obj.price_cents = new_price
                        changed = True
                    if obj.features_json != new_features:
                        obj.features_json = new_features
                        changed = True
                    if changed:
                        obj.save(update_fields=['price_cents', 'features_json', 'updated_at'])
                        updated_count += 1
                        self.stdout.write(f'  [UPDATED] {name}')
                    else:
                        self.stdout.write(f'  [NO CHANGE] {name}')
                else:
                    created_count += 1
                    self.stdout.write(f'  [CREATED] {name}')

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'AccountTier: {created_count} created, {updated_count} updated.')
            )

    # ------------------------------------------------------------------
    # BillingPlan + Entitlement (apps.tiers)
    # ------------------------------------------------------------------
    def _seed_billing_plans(self, dry_run: bool) -> None:
        from apps.tiers.models import BillingPlan

        self.stdout.write('\n=== BillingPlan (apps.tiers) ===')
        created_count = 0
        updated_count = 0

        for tier_data in TIERS:
            slug = tier_data['slug']
            display_name = tier_data['name']
            price_per_month = tier_data['price_cents'] / 100
            currency = tier_data['currency']
            description = tier_data['description']
            is_default = slug == 'basic'

            if dry_run:
                exists = BillingPlan.objects.filter(slug=slug).exists()
                action = 'UPDATE' if exists else 'CREATE'
                self.stdout.write(
                    f'  [{action}] {slug}  display_name={display_name}  '
                    f'price_per_month={price_per_month} {currency}'
                )
                continue

            with transaction.atomic():
                plan, created = BillingPlan.objects.get_or_create(
                    slug=slug,
                    defaults={
                        'display_name': display_name,
                        'price_per_month': price_per_month,
                        'currency': currency,
                        'description': description,
                        'is_default': is_default,
                    },
                )
                if not created:
                    changed = False
                    updates = []
                    if plan.display_name != display_name:
                        plan.display_name = display_name
                        updates.append('display_name')
                        changed = True
                    if float(plan.price_per_month) != float(price_per_month):
                        plan.price_per_month = price_per_month
                        updates.append('price_per_month')
                        changed = True
                    if plan.currency != currency:
                        plan.currency = currency
                        updates.append('currency')
                        changed = True
                    if plan.description != description:
                        plan.description = description
                        updates.append('description')
                        changed = True
                    if changed:
                        plan.save(update_fields=updates)
                        updated_count += 1
                        self.stdout.write(f'  [UPDATED] {slug}')
                    else:
                        self.stdout.write(f'  [NO CHANGE] {slug}')
                else:
                    created_count += 1
                    self.stdout.write(f'  [CREATED] {slug}')

                self._sync_entitlements(plan, tier_data['features'])

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'BillingPlan: {created_count} created, {updated_count} updated.')
            )

    def _sync_entitlements(self, plan, features: dict) -> None:
        from apps.tiers.models import Entitlement

        for feature_key, feature_value in features.items():
            if isinstance(feature_value, bool):
                str_value = 'true' if feature_value else 'false'
                unit = 'boolean'
            else:
                str_value = str(feature_value)
                unit = 'count'

            entitlement, created = Entitlement.objects.get_or_create(
                plan=plan,
                feature_key=feature_key,
                defaults={'value': str_value, 'unit': unit},
            )
            if not created and (entitlement.value != str_value or entitlement.unit != unit):
                entitlement.value = str_value
                entitlement.unit = unit
                entitlement.save(update_fields=['value', 'unit'])
