from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0042_youtube_parity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # UserContentPlaylist
        migrations.CreateModel(
            name="UserContentPlaylist",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="content_playlists", to=settings.AUTH_USER_MODEL)),
                ("title", models.CharField(max_length=160)),
                ("description", models.TextField(blank=True, default="")),
                ("visibility", models.CharField(choices=[("public", "Public"), ("unlisted", "Unlisted"), ("private", "Private")], default="private", max_length=16)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_system", models.BooleanField(default=False)),
                ("system_key", models.CharField(blank=True, default="", max_length=32)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "user_content_playlist",
                "ordering": ["sort_order", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="usercontentplaylist",
            index=models.Index(fields=["user", "visibility"], name="ucp_user_vis_idx"),
        ),
        migrations.AddIndex(
            model_name="usercontentplaylist",
            index=models.Index(fields=["user", "system_key"], name="ucp_user_syskey_idx"),
        ),
        migrations.AddIndex(
            model_name="usercontentplaylist",
            index=models.Index(fields=["user", "sort_order"], name="ucp_user_sort_idx"),
        ),
        # UserContentPlaylistItem
        migrations.CreateModel(
            name="UserContentPlaylistItem",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("playlist", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="broadcasts.usercontentplaylist")),
                ("content", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_playlist_items", to="broadcasts.channelcontent")),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("added_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "user_content_playlist_item",
                "ordering": ["sort_order", "-added_at"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="usercontentplaylistitem",
            unique_together={("playlist", "content")},
        ),
        migrations.AddIndex(
            model_name="usercontentplaylistitem",
            index=models.Index(fields=["playlist", "sort_order"], name="ucpi_pl_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="usercontentplaylistitem",
            index=models.Index(fields=["content", "added_at"], name="ucpi_content_at_idx"),
        ),
        # LiveStreamCamera
        migrations.CreateModel(
            name="LiveStreamCamera",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("stream", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cameras", to="broadcasts.channellivestream")),
                ("source_id", models.CharField(blank=True, default="", max_length=128)),
                ("label", models.CharField(max_length=128)),
                ("facing", models.CharField(blank=True, default="", max_length=16)),
                ("is_active", models.BooleanField(default=False)),
                ("is_external", models.BooleanField(default=False)),
                ("thumbnail_url", models.URLField(blank=True, default="", max_length=1024)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "live_stream_camera",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="livestreamcamera",
            index=models.Index(fields=["stream", "is_active"], name="lsc_stream_active_idx"),
        ),
    ]
