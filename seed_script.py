import random
import re
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from apps.accounts.models import (
    AccountTier,
    Subscription,
    RevenueAccount,
    UsageQuota,
    Experience,
    Education,
)
from apps.commerce.models import Shop, Product, ShopService
from apps.commerce.constants import KIS_COIN_CODE
from apps.core.models import (
    HealthcareOrganization,
    MedicalProfile,
    Location,
    Ward,
    Department,
    Service as MedicalService,
    StaffProfile,
)
from apps.feed_personalization.models import FeedAffinityProfile
from apps.content.models import Content, Reaction

DOMY_USERS = [
    {
        "username": "user_alpha",
        "display_name": "Alpha Johnson",
        "phone_number": "5551001001",
        "email": "user_alpha@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_bravo",
        "display_name": "Bravo Smith",
        "phone_number": "7700100202",
        "email": "user_bravo@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_charlie",
        "display_name": "Charlie Brown",
        "phone_number": "801001003",
        "email": "user_charlie@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_delta",
        "display_name": "Delta Williams",
        "phone_number": "151001004",
        "email": "user_delta@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_echo",
        "display_name": "Echo Davis",
        "phone_number": "601001005",
        "email": "user_echo@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_foxtrot",
        "display_name": "Foxtrot Miller",
        "phone_number": "71001006",
        "email": "user_foxtrot@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_golf",
        "display_name": "Golf Wilson",
        "phone_number": "9001001007",
        "email": "user_golf@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_hotel",
        "display_name": "Hotel Moore",
        "phone_number": "410010008",
        "email": "user_hotel@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_india",
        "display_name": "India Taylor",
        "phone_number": "801001009",
        "email": "user_india@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_juliet",
        "display_name": "Juliet Anderson",
        "phone_number": "501001010",
        "email": "user_juliet@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_kilo",
        "display_name": "Kilo Thomas",
        "phone_number": "320010011",
        "email": "user_kilo@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_lima",
        "display_name": "Lima Jackson",
        "phone_number": "610010012",
        "email": "user_lima@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_mike",
        "display_name": "Mike White",
        "phone_number": "620010013",
        "email": "user_mike@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_november",
        "display_name": "November Harris",
        "phone_number": "701001014",
        "email": "user_november@kis.local",
        "password": "Test@1234",
    },
    {
        "username": "user_oscar",
        "display_name": "Oscar Martin",
        "phone_number": "810010015",
        "email": "user_oscar@kis.local",
        "password": "Test@1234",
    },
]

SUPERUSER_DATA = {
    "username": "kis_admin",
    "display_name": "KIS Admin",
    "email": "admin@kis.local",
    "phone": "+15550000000",
    "password": "Admin@1234",
    "country": "US",
}


def _clean_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _build_e164(digits: str) -> str:
    normalized = _clean_digits(digits)
    return f"+1{normalized}"


def ensure_domy_users():
    User = get_user_model()
    seeds = []
    for data in DOMY_USERS:
        digits = _clean_digits(data["phone_number"])
        phone_e164 = _build_e164(digits)
        user = User.objects.filter(username=data["username"]).first()
        if not user:
            user = User.objects.create_user(
                phone=phone_e164,
                password=data["password"],
                country="US",
                username=data["username"],
                email=data["email"],
                display_name=data["display_name"],
            )
        else:
            if data.get("password"):
                user.set_password(data["password"])
        user.display_name = data["display_name"]
        user.email = data["email"]
        user.country = "US"
        user.phone = phone_e164
        user.phone_country_code = "+1"
        user.phone_number = digits
        user.save()
        seeds.append(user)
    return seeds


def ensure_superuser():
    User = get_user_model()
    existing = User.objects.filter(email=SUPERUSER_DATA["email"], is_superuser=True).first()
    digits = _clean_digits(SUPERUSER_DATA["phone"])
    phone_e164 = _build_e164(digits)
    if existing:
        superuser = existing
        if SUPERUSER_DATA.get("password"):
            superuser.set_password(SUPERUSER_DATA["password"])
    else:
        superuser = User.objects.create_superuser(
            email=SUPERUSER_DATA["email"],
            password=SUPERUSER_DATA["password"],
            country=SUPERUSER_DATA["country"],
            phone=phone_e164,
            username=SUPERUSER_DATA["username"],
        )
    superuser.display_name = SUPERUSER_DATA["display_name"]
    superuser.phone_country_code = "+1"
    superuser.phone_number = digits
    superuser.country = SUPERUSER_DATA["country"]
    superuser.phone = phone_e164
    superuser.save()
    return superuser


def run():
    ensure_domy_users()
    ensure_superuser()
    User = get_user_model()
    tier_defs = [
        {"name": "Basic", "price_cents": 0, "features_json": {"limits": "500 actions/day"}},
        {"name": "Pro", "price_cents": 2500, "features_json": {"limits": "5000 actions/day"}},
        {"name": "Enterprise", "price_cents": 10000, "features_json": {"limits": "unlimited"}},
    ]
    tier_map = {}
    for data in tier_defs:
        tier, _ = AccountTier.objects.get_or_create(name=data["name"], defaults=data)
        tier_map[data["name"]] = tier

    users = User.objects.filter(username__startswith="user_").order_by("username")
    industries = [
        "Technology",
        "Finance",
        "Healthcare",
        "Manufacturing",
        "Design",
        "Energy",
        "Education",
        "Logistics",
        "Media",
        "Hospitality",
        "Tourism",
        "Retail",
        "Transportation",
        "Research",
        "Fintech",
    ]

    for idx, user in enumerate(users):
        tier = tier_map[list(tier_map)[idx % len(tier_map)]]
        user.tier = tier.name
        user.is_active = True
        user.save(update_fields=["tier", "is_active", "updated_at"])

        Subscription.objects.update_or_create(
            user=user,
            defaults={"tier": tier, "status": "active", "billing_meta": {"source": "seed"}},
        )
        RevenueAccount.objects.update_or_create(
            user=user,
            defaults={
                "balance_cents": random.randint(500_000, 2_000_000),
                "routing_info": {"bank": "SeedBank", "account": f"ACC{idx:04d}"},
            },
        )
        UsageQuota.objects.update_or_create(
            user=user,
            defaults={"tier": tier, "quotas_json": {"ai_queries_per_day": 1200 + idx * 50}},
        )
        FeedAffinityProfile.objects.get_or_create(
            user=user,
            defaults={
                "broadcast_score": random.uniform(1, 5),
                "community_score": random.uniform(1, 5),
                "partner_score": random.uniform(1, 5),
            },
        )

        if not user.experiences.exists():
            Experience.objects.create(
                user=user,
                title="Senior Strategy Lead",
                description="Led multi-regional programs with measurable impact.",
                start_date=date(2019, 1, 1),
                end_date=date(2023, 12, 31),
            )
        if not user.educations.exists():
            Education.objects.create(
                user=user,
                school="Global Leadership Institute",
                description="Multi-disciplinary executive studies.",
                start_date=date(2014, 9, 1),
                end_date=date(2018, 6, 30),
            )

        shop_slug = slugify(f"{user.username}-studio")
        shop, _ = Shop.objects.get_or_create(
            owner=user,
            slug=shop_slug,
            defaults={
                "name": f"{user.display_name} Studio",
                "description": f"{user.display_name} offers curated experiences and goods.",
                "branding": {"color": "#1a73e8"},
                "social_links": {"instagram": f"https://instagram.com/{user.username}"},
                "membership_public": True,
                "analytics": {"seeded_at": timezone.now().isoformat()},
            },
        )

        for p_idx in range(2):
            prod_slug = f"{shop_slug}-product-{p_idx}"
            Product.objects.update_or_create(
                shop=shop,
                slug=prod_slug,
                defaults={
                    "name": f"{user.display_name} Product {p_idx + 1}",
                    "sku": f"{user.username.upper()}-P{p_idx + 1}",
                    "price": Decimal("99.99") + Decimal(p_idx * 40),
                    "currency": KIS_COIN_CODE,
                    "description": f"Flagship offering {p_idx + 1}",
                    "service_type": "Product",
                    "availability": {"status": "available"},
                },
            )

        service_slug = f"{shop_slug}-service"
        ShopService.objects.update_or_create(
            shop=shop,
            slug=service_slug,
            defaults={
                "name": f"{user.display_name} VIP Session",
                "short_summary": "Concierge advisory",
                "description": "Reserved slot with a trusted specialist.",
                "pricing_model": "standard",
                "service_type": "Consulting",
                "delivery_modes": ["online", "onsite"],
                "tags": ["premium", "concierge"],
                "visibility": "public",
                "status": "published",
                "price": Decimal("199.99"),
                "availability": {"slots": 5},
            },
        )

        for post_i in range(2):
            content, _ = Content.objects.get_or_create(
                author=user,
                title=f"{user.display_name} Insight {post_i + 1}",
                defaults={
                    "body": f"Sharing updates from {industries[idx]} operations.",
                    "is_published": True,
                    "published_at": timezone.now() - timedelta(days=post_i),
                },
            )
            Reaction.objects.get_or_create(user=user, content=content, reaction_type="like")

        org_slug = slugify(f"{user.username}-health")
        org, _ = HealthcareOrganization.objects.get_or_create(
            slug=org_slug,
            defaults={
                "name": f"{user.display_name} Health",
                "org_type": HealthcareOrganization.TYPE_WELLNESS,
                "owner": user,
                "status": HealthcareOrganization.STATUS_ACTIVE,
                "region": "global",
                "compliance_officer": user.display_name,
                "metadata": {"seeded": True},
            },
        )

        profile, _ = MedicalProfile.objects.get_or_create(
            organization=org,
            defaults={
                "name": f"{org.name} Core Profile",
                "location": {"country": user.country},
                "created_by": user,
            },
        )

        if not profile.locations.exists():
            location = Location.objects.create(
                organization=org,
                profile=profile,
                label=f"{org.name} Campus",
                address={"line1": "100 Health Ave", "city": "Metro"},
                timezone="UTC",
            )
        else:
            location = profile.locations.first()

        Ward.objects.get_or_create(
            location=location,
            name="General Ward",
            defaults={"capacity": 15},
        )
        dept, _ = Department.objects.get_or_create(
            profile=profile,
            name="Care & Wellness",
            defaults={"services": ["coaching", "telehealth"]},
        )
        MedicalService.objects.get_or_create(
            profile=profile,
            name="Wellbeing Consultation",
            defaults={"department": dept, "description": "Comprehensive screenings"},
        )
        StaffProfile.objects.get_or_create(
            profile=profile,
            user=user,
            defaults={
                "role": "Medical Lead",
                "scope": {"tier": tier.name},
                "permissions": ["manage_staff"],
            },
        )

    print("Data seeded for", users.count(), "users")


if __name__ == "__main__":
    run()
