from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0062_coworkingspace_crowdfundcampaign_savingsgroup_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='shop',
            name='status',
            field=models.CharField(
                choices=[('draft', 'Draft'), ('active', 'Active'), ('paused', 'Paused')],
                db_index=True,
                default='active',
                max_length=16,
            ),
        ),
    ]
