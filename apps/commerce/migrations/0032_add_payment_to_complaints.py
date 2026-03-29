from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0031_migrate_booking_payments'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicebookingcomplaint',
            name='payment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='complaints',
                to='commerce.servicebookingpayment',
            ),
        ),
    ]
