from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("health_ops", "0007_wellnessprogramsession_notificationremindersession"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PaymentBillingSession",
        ),
        migrations.DeleteModel(
            name="WalletTransaction",
        ),
        migrations.DeleteModel(
            name="Wallet",
        ),
    ]
