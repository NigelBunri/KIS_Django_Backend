from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0039_educationcoursereview_educationcoursequestion"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="channelcontentcomment",
            name="is_pinned",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddIndex(
            model_name="channelcontentcomment",
            index=models.Index(fields=["content", "is_pinned"], name="chan_comm_content_pinned_idx"),
        ),
        migrations.CreateModel(
            name="ChannelCommentReaction",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "comment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="broadcasts.channelcontentcomment",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="channel_comment_reactions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("reaction", models.CharField(default="like", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_comment_reaction",
                "unique_together": {("comment", "user")},
            },
        ),
        migrations.AddIndex(
            model_name="channelcommentreaction",
            index=models.Index(fields=["comment", "reaction"], name="chan_comm_react_comment_idx"),
        ),
        migrations.AddIndex(
            model_name="channelcommentreaction",
            index=models.Index(fields=["user", "created_at"], name="chan_comm_react_user_idx"),
        ),
        migrations.CreateModel(
            name="ChannelContentClip",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="clips",
                        to="broadcasts.channelcontent",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="channel_content_clips",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=255)),
                ("start_seconds", models.PositiveIntegerField()),
                ("end_seconds", models.PositiveIntegerField()),
                ("status", models.CharField(default="pending", max_length=24)),
                ("clip_url", models.URLField(blank=True)),
                ("thumbnail_url", models.URLField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "channel_content_clip",
            },
        ),
        migrations.AddIndex(
            model_name="channelcontentclip",
            index=models.Index(fields=["content", "created_at"], name="chan_clip_content_idx"),
        ),
        migrations.AddIndex(
            model_name="channelcontentclip",
            index=models.Index(fields=["created_by", "created_at"], name="chan_clip_creator_idx"),
        ),
    ]
