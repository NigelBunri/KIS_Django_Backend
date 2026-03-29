from django.db import migrations


def apply_shop_limits(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")
    updates = {
        "Business": {"shops_limit": 2, "products_per_shop_limit": 60},
        "Business Pro": {
            "shops_limit": "unlimited",
            "products_per_shop_limit": 200,
            "market_analytics": True,
            "market_pro_insights": True,
        },
        "Partner": {
            "shops_limit": "unlimited",
            "products_per_shop_limit": "unlimited",
            "market_analytics": True,
            "market_pro_insights": True,
        },
    }
    for name, limits in updates.items():
        tier = AccountTier.objects.filter(name__iexact=name).first()
        if not tier:
            continue
        features = tier.features_json or {}
        updated = False
        for key, value in limits.items():
            if key not in features or features.get(key) != value:
                features[key] = value
                updated = True
        if updated:
            tier.features_json = features
            tier.save(update_fields=["features_json", "updated_at"])


def remove_shop_limits(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")
    updates = {
        "Business": ["shops_limit", "products_per_shop_limit"],
        "Business Pro": [
            "shops_limit",
            "products_per_shop_limit",
            "market_analytics",
            "market_pro_insights",
        ],
        "Partner": [
            "shops_limit",
            "products_per_shop_limit",
            "market_analytics",
            "market_pro_insights",
        ],
    }
    for name, keys in updates.items():
        tier = AccountTier.objects.filter(name__iexact=name).first()
        if not tier:
            continue
        features = tier.features_json or {}
        removed = False
        for key in keys:
            if key in features:
                features.pop(key)
                removed = True
        if removed:
            tier.features_json = features
            tier.save(update_fields=["features_json", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_privacy_features_business"),
    ]

    operations = [
        migrations.RunPython(apply_shop_limits, remove_shop_limits),
    ]
