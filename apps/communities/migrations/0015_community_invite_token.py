from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communities', '0014_alter_community_allow_join_link_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='community',
            name='invite_token',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Short random token used to build a shareable invite link.',
                max_length=64,
            ),
            preserve_default=False,
        ),
    ]
