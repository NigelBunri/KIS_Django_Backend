from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0033_add_preference_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profilepreferences',
            name='quicklock_pin_hash',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
    ]
