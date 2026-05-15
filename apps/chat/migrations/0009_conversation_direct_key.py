from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0008_conversationmember_pinned_hidden"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="direct_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Canonical participant key for direct 1:1 conversations. Prevents duplicate direct rooms.",
                max_length=160,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(fields=["type", "direct_key"], name="chat_conver_type_9eb3a7_idx"),
        ),
    ]
