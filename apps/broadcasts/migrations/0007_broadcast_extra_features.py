from django.db import migrations


def add_extra_broadcast_features(apps, schema_editor):
    BroadcastFeature = apps.get_model("broadcasts", "BroadcastFeature")
    features = [
        {
            "slug": "lesson_mode",
            "name": "Lesson mode",
            "description": "Treat a broadcast as a structured lesson with swipeable modules.",
            "category": "Education",
            "default_enabled": True,
        },
        {
            "slug": "lesson_enrollment",
            "name": "Lesson enrollment automation",
            "description": "Auto-enroll viewers and track lesson-only memberships.",
            "category": "Learning",
            "default_enabled": True,
        },
        {
            "slug": "lesson_only_membership",
            "name": "Lesson-only membership",
            "description": "Grant access only to the lesson segment regardless of broader partner feeds.",
            "category": "Access",
            "default_enabled": False,
        },
        {
            "slug": "broadcast_dropkit",
            "name": "Broadcast drop kit",
            "description": "Drop digital kits or products tied to the broadcast in-view.",
            "category": "Commerce",
            "default_enabled": False,
        },
        {
            "slug": "ai_moderator_insights",
            "name": "AI moderator insights",
            "description": "Surface AI-curated moderation cues and risk signals mid-session.",
            "category": "Safety",
            "default_enabled": True,
        },
        {
            "slug": "co_host_scheduler",
            "name": "Co-host scheduler",
            "description": "Queue co-hosts and guests, then transition them live with confirmations.",
            "category": "Production",
            "default_enabled": False,
        },
        {
            "slug": "vaulted_replay",
            "name": "Vaulted replay",
            "description": "Store replays behind a vault that unlocks per membership or purchase.",
            "category": "Discovery",
            "default_enabled": False,
        },
        {
            "slug": "broadcast_storefront",
            "name": "Broadcast storefront",
            "description": "Show a curated storefront inside the broadcast feed for instant purchases.",
            "category": "Commerce",
            "default_enabled": True,
        },
        {
            "slug": "real_time_transcriptions",
            "name": "Real-time transcriptions",
            "description": "Deliver on-screen captions plus downloadable transcripts.",
            "category": "Accessibility",
            "default_enabled": True,
        },
        {
            "slug": "subscriber_only_comments",
            "name": "Subscriber-only comments",
            "description": "Restrict commenting to subscribers to keep chats premium.",
            "category": "Access",
            "default_enabled": False,
        },
        {
            "slug": "broadcast_rewards",
            "name": "Broadcast rewards",
            "description": "Issue credits or badges for attendees who complete an experience.",
            "category": "Engagement",
            "default_enabled": False,
        },
        {
            "slug": "viewer_progress_tracker",
            "name": "Viewer progress tracker",
            "description": "Track watched segments, highlight drop-in/out points, and resume.",
            "category": "Insights",
            "default_enabled": True,
        },
        {
            "slug": "auto_mixer",
            "name": "Auto mixer",
            "description": "Let the system balance audio/video feeds and add transitions.",
            "category": "Production",
            "default_enabled": False,
        },
        {
            "slug": "global_chat_rooms",
            "name": "Global chat rooms",
            "description": "Spawn regional chat rooms to pair with the broadcast view.",
            "category": "Community",
            "default_enabled": True,
        },
        {
            "slug": "audience_heatmap",
            "name": "Audience heatmap",
            "description": "Visualize who is watching and where engagement spikes happen.",
            "category": "Insights",
            "default_enabled": True,
        },
    ]
    for feature in features:
        BroadcastFeature.objects.update_or_create(
            slug=feature["slug"],
            defaults={
                "name": feature["name"],
                "description": feature["description"],
                "category": feature["category"],
                "default_enabled": feature["default_enabled"],
            },
        )


def remove_extra_broadcast_features(apps, schema_editor):
    BroadcastFeature = apps.get_model("broadcasts", "BroadcastFeature")
    BroadcastFeature.objects.filter(
        slug__in=[
            "lesson_mode",
            "lesson_enrollment",
            "lesson_only_membership",
            "broadcast_dropkit",
            "ai_moderator_insights",
            "co_host_scheduler",
            "vaulted_replay",
            "broadcast_storefront",
            "real_time_transcriptions",
            "subscriber_only_comments",
            "broadcast_rewards",
            "viewer_progress_tracker",
            "auto_mixer",
            "global_chat_rooms",
            "audience_heatmap",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0006_rename_broadcast_lesson_les_type_idx_broadcast_l_lesson__2b4344_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(add_extra_broadcast_features, remove_extra_broadcast_features),
    ]
