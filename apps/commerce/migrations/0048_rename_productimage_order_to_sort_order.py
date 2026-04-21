from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0047_readd_productimage_alt_text'),
    ]

    operations = [
        migrations.RenameField(
            model_name='productimage',
            old_name='order',
            new_name='sort_order',
        ),
    ]
