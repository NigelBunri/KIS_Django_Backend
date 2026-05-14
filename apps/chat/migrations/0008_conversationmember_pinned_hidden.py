from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0007_conversationmember_last_read_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationmember",
            name="is_hidden",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Per-user delete-for-me state. Hidden memberships are omitted from the main chat list.",
            ),
        ),
        migrations.AddField(
            model_name="conversationmember",
            name="is_pinned",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Per-user chat-list pin state.",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationmember",
            index=models.Index(fields=["user", "is_pinned"], name="chat_conver_user_id_e5e99e_idx"),
        ),
        migrations.AddIndex(
            model_name="conversationmember",
            index=models.Index(fields=["user", "is_hidden"], name="chat_conver_user_id_99002c_idx"),
        ),
    ]
