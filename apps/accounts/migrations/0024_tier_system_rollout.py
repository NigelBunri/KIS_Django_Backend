from django.db import migrations


def _mb(value_gb):
    return int(value_gb) * 1024


def rollout_tiers(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")

    presets = [
        {
            "name": "Free",
            "price_cents": 0,
            "features_json": {
                "feature_tagline": "Start free, upgrade anytime",
                "feature_highlight": "Everything you need to begin",
                "feature_list": [
                    "Direct messaging",
                    "Core community access",
                    "Standard profile",
                    "Basic storage (1 GB for broadcast feeds and profile gallery)",
                    "Search & discovery",
                    "Standard support",
                    "Limited community/group creation (create 1 community and 2 groups)",
                ],
                "communities": 1,
                "groups_per_community": 2,
                "channels_create": 0,
                "channels_follow": True,
                "media_storage_mb": _mb(1),
                "storage_gb": 1,
                "status_retention_days": 2,
                "ads": True,
                "support": "standard",
                "profile_articles": True,
                "privacy_custom": True,
                "shops_limit": 0,
                "products_per_shop_limit": 0,
                "market_profiles": 0,
                "health_profiles": 0,
                "education_profiles": 0,
                "partner_accounts": 0,
            },
        },
        {
            "name": "Pro",
            "price_cents": 1000,
            "features_json": {
                "feature_tagline": "Creators and power users",
                "feature_highlight": "Enhanced profile + higher limits",
                "feature_list": [
                    "Free +",
                    "More communities & groups",
                    "Enhanced profile visibility",
                    "Higher media limits (5 GB for broadcast feeds and profile gallery)",
                    "Advanced messaging tools",
                    "Priority search ranking",
                    "Extended support",
                ],
                "communities": 3,
                "groups_per_community": 20,
                "channels_create": 1,
                "channels_follow": True,
                "media_storage_mb": _mb(5),
                "storage_gb": 5,
                "status_retention_days": 3,
                "ads": False,
                "support": "extended",
                "profile_articles": True,
                "privacy_custom": True,
                "shops_limit": 0,
                "products_per_shop_limit": 0,
                "market_profiles": 0,
                "health_profiles": 0,
                "education_profiles": 0,
                "partner_accounts": 0,
            },
        },
        {
            "name": "Business",
            "price_cents": 2500,
            "features_json": {
                "feature_tagline": "Teams, growth, and visibility",
                "feature_highlight": "KIS Business broadcast + storefront",
                "feature_list": [
                    "Pro +",
                    "KIS Business broadcast channel",
                    "Business profile + CTA buttons",
                    "Multiple admins for business page",
                    "Business insights & audience metrics",
                    "Basic catalog for services/products",
                    "Higher media limits (7 GB for broadcast feeds and profile gallery)",
                    "Promo codes + offers",
                    "Auto-reply & business hours",
                    "Featured discovery boost",
                ],
                "communities": 10,
                "groups_per_community": 50,
                "channels_create": 5,
                "channels_follow": True,
                "media_storage_mb": _mb(7),
                "storage_gb": 7,
                "status_retention_days": 4,
                "ads": False,
                "support": "priority",
                "admin_tools": True,
                "analytics_basic": True,
                "shops_limit": 2,
                "products_per_shop_limit": 60,
                "market_profiles": 1,
                "health_profiles": 0,
                "education_profiles": 0,
                "partner_accounts": 0,
            },
        },
        {
            "name": "Business Pro",
            "price_cents": 3500,
            "features_json": {
                "feature_tagline": "High-impact teams and creators",
                "feature_badge": "Most popular",
                "feature_highlight": "Advanced analytics + team workflows",
                "feature_list": [
                    "Business +",
                    "Unlimited communities & groups",
                    "Team collaboration tools",
                    "Advanced insights & reporting",
                    "Priority moderation tools",
                    "Limited health profiles creation (1)",
                    "Limited education profiles creation (1)",
                    "Higher media limits (10 GB for broadcast feeds, profile gallery, and market items)",
                    "Unlimited market profile creation",
                    "Branding controls",
                    "Faster support response",
                ],
                "communities": "unlimited",
                "groups_per_community": "unlimited",
                "channels_create": 15,
                "channels_follow": True,
                "media_storage_mb": _mb(10),
                "storage_gb": 10,
                "status_retention_days": 7,
                "ads": False,
                "support": "priority",
                "admin_tools": True,
                "analytics_advanced": True,
                "crm_tools": True,
                "team_seats": 20,
                "verification_badge": True,
                "shops_limit": "unlimited",
                "products_per_shop_limit": 200,
                "market_analytics": True,
                "market_pro_insights": True,
                "market_profiles": "unlimited",
                "health_profiles": 1,
                "education_profiles": 1,
                "partner_accounts": 0,
            },
        },
        {
            "name": "Partner",
            "price_cents": 6000,
            "features_json": {
                "feature_tagline": "Organizations, ministries, and enterprises",
                "feature_badge": "Partner",
                "feature_highlight": "Multi-account orgs + revenue tools",
                "feature_list": [
                    "Business Pro +",
                    "Verified organization profile",
                    "Multiple admins & roles",
                    "Live streaming + events",
                    "Donations & revenue tools",
                    "Advanced analytics dashboard",
                    "Community & group management at scale",
                    "Priority support",
                    "Partner webhooks & automations",
                    "Higher media limits (13 GB for broadcast feeds and profile gallery)",
                    "Unlimited health profiles creation",
                    "Unlimited education profiles creation",
                ],
                "communities": "unlimited",
                "groups_per_community": "unlimited",
                "channels_create": 50,
                "channels_follow": True,
                "media_storage_mb": _mb(13),
                "storage_gb": 13,
                "status_retention_days": 10,
                "ads": False,
                "support": "priority",
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
                "partner_insight": True,
                "access_control": True,
                "shops_limit": "unlimited",
                "products_per_shop_limit": "unlimited",
                "market_analytics": True,
                "market_pro_insights": True,
                "market_profiles": "unlimited",
                "health_profiles": "unlimited",
                "education_profiles": "unlimited",
                "live_streaming": True,
                "events": True,
            },
        },
        {
            "name": "Partner Pro",
            "price_cents": 12000,
            "features_json": {
                "feature_tagline": "Global partner networks & enterprise ops",
                "feature_badge": "Partner Pro",
                "feature_highlight": "Unlimited partner orgs + automation ops",
                "feature_list": [
                    "Partner +",
                    "Unlimited partner organizations",
                    "Enterprise automation & API/webhooks",
                    "Dedicated revenue & giving ops hub",
                    "Advanced analytics & forecasting",
                    "Priority compliance & governance controls",
                    "Global roles & permission teams",
                    "Concierge onboarding & migration",
                    "Unlimited media limits",
                    "Better groups, communities, channels, and feeds management",
                    "Organizational management tools",
                ],
                "communities": "unlimited",
                "groups_per_community": "unlimited",
                "channels_create": "unlimited",
                "channels_follow": True,
                "media_storage_mb": "unlimited",
                "storage_gb": "unlimited",
                "status_retention_days": 14,
                "ads": False,
                "support": "concierge",
                "admin_tools": True,
                "analytics_advanced": True,
                "crm_tools": True,
                "team_seats": "unlimited",
                "revenue_tools": True,
                "api_access": True,
                "privacy_custom": True,
                "profile_articles": True,
                "partner_accounts": "unlimited",
                "partner_integrations": True,
                "partner_webhooks": True,
                "partner_automation": True,
                "partner_insight": True,
                "partner_forecasting": True,
                "priority_compliance": True,
                "access_control": True,
                "shops_limit": "unlimited",
                "products_per_shop_limit": "unlimited",
                "market_analytics": True,
                "market_pro_insights": True,
                "market_profiles": "unlimited",
                "health_profiles": "unlimited",
                "education_profiles": "unlimited",
                "live_streaming": True,
                "events": True,
            },
        },
    ]

    free = AccountTier.objects.filter(name__iexact="Free").first()
    basic = AccountTier.objects.filter(name__iexact="Basic").first()
    if basic and not free:
        basic.name = "Free"
        basic.save(update_fields=["name", "updated_at"])

    for preset in presets:
        name = preset["name"]
        tier = AccountTier.objects.filter(name__iexact=name).first()
        if tier:
            tier.name = name
            tier.price_cents = preset["price_cents"]
            tier.features_json = preset["features_json"]
            tier.save(update_fields=["name", "price_cents", "features_json", "updated_at"])
        else:
            AccountTier.objects.create(
                name=name,
                price_cents=preset["price_cents"],
                features_json=preset["features_json"],
            )


def rollback_tiers(apps, schema_editor):
    # Keep current rows unchanged on rollback.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0023_normalize_phone_parts"),
    ]

    operations = [
        migrations.RunPython(rollout_tiers, rollback_tiers),
    ]
