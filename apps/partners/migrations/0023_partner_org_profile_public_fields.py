from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0022_merge_0021_org_profile_and_setting_rename"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerorganizationprofile",
            name="public_fields",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
