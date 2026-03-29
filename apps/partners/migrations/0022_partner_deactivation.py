from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0021_partner_organization_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="partner",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="partner",
            name="deactivated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="deactivated_partners",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="partner",
            name="deactivation_source",
            field=models.CharField(
                blank=True,
                choices=[("user", "User"), ("system", "System")],
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="partner",
            name="grace_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
