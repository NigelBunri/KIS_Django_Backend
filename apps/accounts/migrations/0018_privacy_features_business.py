from django.db import migrations


def enable_privacy_features_for_business(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")

    for name in ("Business", "Business Pro"):
        tier = AccountTier.objects.filter(name__iexact=name).first()
        if not tier:
            continue
        features = tier.features_json or {}
        updated = False
        if not features.get("privacy_custom"):
            features["privacy_custom"] = True
            updated = True
        if not features.get("profile_articles"):
            features["profile_articles"] = True
            updated = True
        if updated:
            tier.features_json = features
            tier.save(update_fields=["features_json", "updated_at"])


def revert_privacy_features_for_business(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")

    for name in ("Business", "Business Pro"):
        tier = AccountTier.objects.filter(name__iexact=name).first()
        if not tier:
            continue
        features = tier.features_json or {}
        removed = False
        for key in ("privacy_custom", "profile_articles"):
            if key in features:
                del features[key]
                removed = True
        if removed:
            tier.features_json = features
            tier.save(update_fields=["features_json", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_partner_pro_tier"),
    ]

    operations = [
        migrations.RunPython(enable_privacy_features_for_business, revert_privacy_features_for_business),
    ]
