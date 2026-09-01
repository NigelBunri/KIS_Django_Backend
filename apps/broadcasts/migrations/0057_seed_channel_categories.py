from django.db import migrations


# ChannelCategory (added in 0046) has real list/browse endpoints
# (ChannelCategoryListView, ChannelCategoryBrowseView) but nothing ever
# populated the table - GET /broadcasts/categories/ has always returned
# an empty list. These top-level categories mirror the vocabulary KISTube
# already uses for its own sections (Education, Health, Market, Jobs,
# Testimonies) plus the general content areas the wider KIS channel system
# supports, so category browsing has real, sensible data on day one.
CATEGORIES = [
    ("Education", "education", "graduation-cap"),
    ("Health & Wellness", "health-wellness", "heart-pulse"),
    ("Market & Shopping", "market-shopping", "shopping-bag"),
    ("Jobs & Careers", "jobs-careers", "briefcase"),
    ("Ministry & Faith", "ministry-faith", "book-open"),
    ("Testimonies", "testimonies", "megaphone"),
    ("Music & Worship", "music-worship", "music"),
    ("Technology", "technology", "cpu"),
    ("Family & Parenting", "family-parenting", "users"),
    ("Community & Culture", "community-culture", "globe"),
    ("News & Updates", "news-updates", "newspaper"),
    ("Sports & Recreation", "sports-recreation", "activity"),
]


def seed_categories(apps, schema_editor):
    ChannelCategory = apps.get_model("broadcasts", "ChannelCategory")
    for sort_order, (name, slug, icon_name) in enumerate(CATEGORIES):
        ChannelCategory.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "icon_name": icon_name, "sort_order": sort_order, "is_active": True},
        )


def remove_seeded_categories(apps, schema_editor):
    ChannelCategory = apps.get_model("broadcasts", "ChannelCategory")
    ChannelCategory.objects.filter(slug__in=[slug for _, slug, _ in CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0056_channelcontentpollvote"),
    ]

    operations = [
        migrations.RunPython(seed_categories, remove_seeded_categories),
    ]
