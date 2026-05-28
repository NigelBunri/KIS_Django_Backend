import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0041_channelcontentchapter_channelcontent_tags"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── New fields on existing models ──────────────────────────────────────

        migrations.AddField(
            model_name="channelcontent",
            name="comments_disabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="broadcastchannel",
            name="trailer_content",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="trailer_for_channels",
                to="broadcasts.channelcontent",
            ),
        ),
        migrations.AddField(
            model_name="broadcastchannel",
            name="featured_content",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="featured_for_channels",
                to="broadcasts.channelcontent",
            ),
        ),
        migrations.AddField(
            model_name="broadcastplaylist",
            name="shuffle_enabled",
            field=models.BooleanField(default=False),
        ),

        # ── New models ─────────────────────────────────────────────────────────

        migrations.CreateModel(
            name="ChannelContentSubtitle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subtitles",
                        to="broadcasts.channelcontent",
                    ),
                ),
                ("language", models.CharField(max_length=16)),
                ("label", models.CharField(blank=True, default="", max_length=80)),
                ("vtt_url", models.URLField(blank=True, default="")),
                ("segments", models.JSONField(blank=True, default=list)),
                ("is_auto_generated", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_content_subtitle",
                "unique_together": {("content", "language")},
            },
        ),
        migrations.CreateModel(
            name="ChannelContentEndScreen",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="end_screen",
                        to="broadcasts.channelcontent",
                    ),
                ),
                ("config", models.JSONField(blank=True, default=list)),
                ("is_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "channel_content_end_screen",
            },
        ),
        migrations.CreateModel(
            name="ChannelContentCard",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "content",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cards",
                        to="broadcasts.channelcontent",
                    ),
                ),
                (
                    "card_type",
                    models.CharField(
                        choices=[
                            ("video", "Video"),
                            ("poll", "Poll"),
                            ("link", "Link"),
                            ("playlist", "Playlist"),
                            ("channel", "Channel"),
                        ],
                        max_length=16,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=140)),
                ("start_seconds", models.PositiveIntegerField(default=0)),
                ("end_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("target_id", models.CharField(blank=True, default="", max_length=80)),
                ("url", models.URLField(blank=True, default="")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "db_table": "channel_content_card",
                "ordering": ["start_seconds", "sort_order"],
            },
        ),
        migrations.CreateModel(
            name="ChannelActivityEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="activity_events",
                        to="broadcasts.broadcastchannel",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("new_content", "New Content"),
                            ("new_subscriber", "New Subscriber"),
                            ("new_comment", "New Comment"),
                            ("new_reaction", "New Reaction"),
                            ("live_started", "Live Started"),
                            ("milestone", "Milestone"),
                        ],
                        db_index=True,
                        max_length=24,
                    ),
                ),
                ("actor_display", models.CharField(blank=True, default="", max_length=120)),
                ("target_type", models.CharField(blank=True, default="", max_length=32)),
                ("target_id", models.CharField(blank=True, default="", max_length=80)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "channel_activity_event",
            },
        ),
        migrations.AddIndex(
            model_name="channelactivityevent",
            index=models.Index(fields=["channel", "created_at"], name="chan_activity_channel_created_idx"),
        ),
        migrations.CreateModel(
            name="ChannelLivePoll",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="polls",
                        to="broadcasts.channellivestream",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_live_polls",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("question", models.CharField(max_length=300)),
                ("options", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("ended", "Ended")],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "channel_live_poll",
            },
        ),
        migrations.CreateModel(
            name="ChannelLivePollVote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "poll",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="broadcasts.channellivepoll",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="live_poll_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("option_index", models.PositiveSmallIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_live_poll_vote",
                "unique_together": {("poll", "user")},
            },
        ),
        migrations.CreateModel(
            name="ChannelLiveQA",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qa_sessions",
                        to="broadcasts.channellivestream",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_qa_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("ended", "Ended")],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "channel_live_qa",
            },
        ),
        migrations.CreateModel(
            name="ChannelLiveQAQuestion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        to="broadcasts.channelliveqa",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="live_qa_questions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("user_display", models.CharField(blank=True, default="", max_length=120)),
                ("question_text", models.TextField()),
                ("upvote_count", models.PositiveIntegerField(default=0)),
                ("is_answered", models.BooleanField(default=False)),
                ("is_pinned", models.BooleanField(default=False)),
                ("is_hidden", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_live_qa_question",
                "ordering": ["-is_pinned", "-upvote_count", "created_at"],
            },
        ),
        migrations.CreateModel(
            name="ChannelLiveQAQuestionUpvote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="upvotes",
                        to="broadcasts.channelliveqaquestion",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qa_question_upvotes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_live_qa_question_upvote",
                "unique_together": {("question", "user")},
            },
        ),
        migrations.CreateModel(
            name="CommentCreatorHeart",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "comment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="creator_heart",
                        to="broadcasts.channelcontentcomment",
                    ),
                ),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hearted_comments",
                        to="broadcasts.broadcastchannel",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "comment_creator_heart",
            },
        ),
        migrations.CreateModel(
            name="ChannelWatchHistorySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="watch_history_settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("is_paused", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "channel_watch_history_settings",
            },
        ),
    ]
