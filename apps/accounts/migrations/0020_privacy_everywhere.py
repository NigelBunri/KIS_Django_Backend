from django.db import migrations


def add_privacy_features_for_all_tiers(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")
    for tier in AccountTier.objects.all():
        features = tier.features_json or {}
        updated = False
        for key in ("privacy_custom", "profile_articles"):
            if not features.get(key):
                features[key] = True
                updated = True
        if updated:
            tier.features_json = features
            tier.save(update_fields=["features_json", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_shop_limits"),
    ]

    operations = [
        migrations.RunPython(add_privacy_features_for_all_tiers, migrations.RunPython.noop),
    ]
