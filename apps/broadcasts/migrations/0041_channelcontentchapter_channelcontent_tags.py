import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0040_channelcommentreaction_channelcontentclip_channelcontentcomment_is_pinned"),
    ]

    operations = [
        migrations.AddField(
            model_name="channelcontent",
            name="tags",
            field=models.JSONField(blank=True, default=list, help_text="List of string tags for discovery filtering."),
        ),
        migrations.CreateModel(
            name="ChannelContentChapter",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chapters",
                        to="broadcasts.channelcontent",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("start_seconds", models.PositiveIntegerField()),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "channel_content_chapter",
                "ordering": ["sort_order", "start_seconds"],
                "unique_together": {("content", "start_seconds")},
            },
        ),
        migrations.AddIndex(
            model_name="channelcontentchapter",
            index=models.Index(fields=["content", "sort_order"], name="chan_chapter_content_sort_idx"),
        ),
    ]
