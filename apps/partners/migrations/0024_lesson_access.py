from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0023_partner_org_profile_public_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnermembership",
            name="lesson_access_only",
            field=models.BooleanField(
                default=False,
                help_text="True when membership exists solely because of a lesson enrollment.",
            ),
        ),
    ]
