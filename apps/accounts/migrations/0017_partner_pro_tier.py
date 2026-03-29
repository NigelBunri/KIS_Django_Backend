from django.db import migrations


def seed_partner_tiers(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")

    partner_features = {
        "communities": 50,
        "groups_per_community": 200,
        "channels_create": 50,
        "channels_follow": True,
        "media_storage_mb": 102400,
        "status_retention_days": 7,
        "ads": False,
        "support": "concierge",
        "admin_tools": True,
        "analytics_advanced": True,
        "crm_tools": True,
        "team_seats": 50,
        "revenue_tools": True,
        "api_access": True,
        "privacy_custom": True,
        "profile_articles": True,
        "partner_accounts": 5,
        "partner_integrations": True,
        "partner_webhooks": True,
        "partner_automation": True,
    }

    partner_pro_features = {
        "communities": 120,
        "groups_per_community": 500,
        "channels_create": 120,
        "channels_follow": True,
        "media_storage_mb": 256000,
        "status_retention_days": 14,
        "ads": False,
        "support": "concierge",
        "admin_tools": True,
        "analytics_advanced": True,
        "crm_tools": True,
        "team_seats": 120,
        "revenue_tools": True,
        "api_access": True,
        "privacy_custom": True,
        "profile_articles": True,
        "partner_accounts": "unlimited",
        "partner_integrations": True,
        "partner_webhooks": True,
        "partner_automation": True,
        "partner_insight": True,
        "priority_compliance": True,
        "access_control": True,
    }

    AccountTier.objects.update_or_create(
        name="Partner",
        defaults={
            "price_cents": 6000,
            "features_json": partner_features,
        },
    )

    AccountTier.objects.update_or_create(
        name="Partner Pro",
        defaults={
            "price_cents": 12000,
            "features_json": partner_pro_features,
        },
    )


def unseed_partner_tiers(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")
    AccountTier.objects.filter(name__iexact="Partner Pro").delete()
    existing_partner = AccountTier.objects.filter(name__iexact="Partner").first()
    if existing_partner:
        existing_partner.price_cents = 6000
        existing_partner.features_json = {
            "communities": 50,
            "groups_per_community": 200,
            "channels_create": 50,
            "channels_follow": True,
            "media_storage_mb": 102400,
            "status_retention_days": 7,
            "ads": False,
            "support": "concierge",
            "admin_tools": True,
            "analytics_advanced": True,
            "crm_tools": True,
            "team_seats": 50,
            "revenue_tools": True,
            "api_access": True,
        }
        existing_partner.save(update_fields=["price_cents", "features_json", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_merge_20260110_0329"),
    ]

    operations = [
        migrations.RunPython(seed_partner_tiers, unseed_partner_tiers),
    ]
