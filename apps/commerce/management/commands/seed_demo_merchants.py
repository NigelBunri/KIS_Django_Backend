from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User
from apps.commerce.constants import KIS_COIN_CODE
from apps.commerce.category_catalog import ensure_catalog_categories
from apps.commerce.models import (
    CatalogCategory,
    Product,
    Shop,
    ShopLandingPage,
    ShopLandingTestimonial,
    ShopService,
)

MERCHANTS = [
    {
        "username": "user_alpha",
        "visible_name": "Alpha Johnson",
        "country_code": "+1",
        "phone": "5551001001",
        "country": "US",
        "city": "Austin",
        "state": "Texas",
        "postal_code": "78701",
        "timezone": "America/Chicago",
        "street": "210 Commerce Blvd",
        "suite": "Suite 360",
    },
    {
        "username": "user_bravo",
        "visible_name": "Bravo Smith",
        "country_code": "+1",
        "phone": "7700100202",
        "country": "US",
        "city": "Seattle",
        "state": "Washington",
        "postal_code": "98101",
        "timezone": "America/Los_Angeles",
        "street": "602 Rainier Way",
        "suite": "Floor 16",
    },
    {
        "username": "user_charlie",
        "visible_name": "Charlie Brown",
        "country_code": "+1",
        "phone": "801001003",
        "country": "US",
        "city": "Denver",
        "state": "Colorado",
        "postal_code": "80202",
        "timezone": "America/Denver",
        "street": "1430 Mile High Rd",
        "suite": "Suite 500",
    },
    {
        "username": "user_delta",
        "visible_name": "Delta Williams",
        "country_code": "+1",
        "phone": "151001004",
        "country": "US",
        "city": "Miami",
        "state": "Florida",
        "postal_code": "33130",
        "timezone": "America/New_York",
        "street": "403 Biscayne Ave",
        "suite": "Room 210",
    },
    {
        "username": "user_echo",
        "visible_name": "Echo Davis",
        "country_code": "+1",
        "phone": "601001005",
        "country": "US",
        "city": "Chicago",
        "state": "Illinois",
        "postal_code": "60601",
        "timezone": "America/Chicago",
        "street": "77 Loop Plaza",
        "suite": "Unit 12",
    },
    {
        "username": "user_foxtrot",
        "visible_name": "Foxtrot Miller",
        "country_code": "+1",
        "phone": "71001006",
        "country": "US",
        "city": "New York",
        "state": "New York",
        "postal_code": "10001",
        "timezone": "America/New_York",
        "street": "15 Broadway",
        "suite": "Penthouse 3",
    },
    {
        "username": "user_golf",
        "visible_name": "Golf Wilson",
        "country_code": "+1",
        "phone": "9001001007",
        "country": "US",
        "city": "Atlanta",
        "state": "Georgia",
        "postal_code": "30303",
        "timezone": "America/New_York",
        "street": "25 Peachtree St",
        "suite": "Suite 1400",
    },
    {
        "username": "user_hotel",
        "visible_name": "Hotel Moore",
        "country_code": "+1",
        "phone": "410010008",
        "country": "US",
        "city": "Los Angeles",
        "state": "California",
        "postal_code": "90012",
        "timezone": "America/Los_Angeles",
        "street": "500 Sunset Blvd",
        "suite": "Studio 8",
    },
    {
        "username": "user_india",
        "visible_name": "India Taylor",
        "country_code": "+1",
        "phone": "801001009",
        "country": "US",
        "city": "Boston",
        "state": "Massachusetts",
        "postal_code": "02108",
        "timezone": "America/New_York",
        "street": "5 Beacon St",
        "suite": "Suite 210",
    },
    {
        "username": "user_juliet",
        "visible_name": "Juliet Anderson",
        "country_code": "+1",
        "phone": "501001010",
        "country": "US",
        "city": "Phoenix",
        "state": "Arizona",
        "postal_code": "85004",
        "timezone": "America/Phoenix",
        "street": "300 Central Ave",
        "suite": "Suite 102",
    },
    {
        "username": "user_kilo",
        "visible_name": "Kilo Thomas",
        "country_code": "+1",
        "phone": "320010011",
        "country": "US",
        "city": "Minneapolis",
        "state": "Minnesota",
        "postal_code": "55402",
        "timezone": "America/Chicago",
        "street": "99 Nicollet Mall",
        "suite": "Loft 420",
    },
    {
        "username": "user_lima",
        "visible_name": "Lima Jackson",
        "country_code": "+1",
        "phone": "610010012",
        "country": "US",
        "city": "San Diego",
        "state": "California",
        "postal_code": "92101",
        "timezone": "America/Los_Angeles",
        "street": "705 Harbor Dr",
        "suite": "Dockside 1",
    },
    {
        "username": "user_mike",
        "visible_name": "Mike White",
        "country_code": "+1",
        "phone": "620010013",
        "country": "US",
        "city": "St. Louis",
        "state": "Missouri",
        "postal_code": "63101",
        "timezone": "America/Chicago",
        "street": "400 Arch St",
        "suite": "Suite 220",
    },
    {
        "username": "user_november",
        "visible_name": "November Harris",
        "country_code": "+1",
        "phone": "701001014",
        "country": "US",
        "city": "Philadelphia",
        "state": "Pennsylvania",
        "postal_code": "19103",
        "timezone": "America/New_York",
        "street": "40 Chestnut St",
        "suite": "Suite 3B",
    },
    {
        "username": "user_oscar",
        "visible_name": "Oscar Martin",
        "country_code": "+1",
        "phone": "810010015",
        "country": "US",
        "city": "Nashville",
        "state": "Tennessee",
        "postal_code": "37203",
        "timezone": "America/Chicago",
        "street": "220 Music Row",
        "suite": "Studio 200",
    },
]


class Command(BaseCommand):
    help = "Create demo merchants with fully populated shops, products, and services for QA."

    def handle(self, *args, **options):
        summary_lines = []
        ensure_catalog_categories()
        for index, entry in enumerate(MERCHANTS):
            phone = f"{entry['country_code']}{entry['phone']}"
            email = f"{entry['username']}@demo.kis"
            user, user_created = self.get_or_create_user(entry, phone, email)

            shop_slug = slugify(f"{entry['username']}-shop")
            shop_name = f"{entry['visible_name']} Studio"
            product_price = Decimal("149.00") + Decimal(index) * Decimal("3.50")
            service_price = Decimal("249.00") + Decimal(index) * Decimal("5.00")

            shop_defaults = self.build_shop_defaults(entry, shop_name, shop_slug, index)
            shop, shop_created = Shop.objects.get_or_create(
                slug=shop_slug,
                defaults={"owner": user, **shop_defaults},
            )

            if not shop_created and shop.owner_id != user.id:
                self.stdout.write(
                    self.style.WARNING(
                        f"Shop slug {shop_slug} already belongs to another user; skipping updates."
                    )
                )
                continue

            if not shop_created:
                self.update_model(shop, shop_defaults)

            landing_defaults = self.build_landing_defaults(entry, shop)
            landing_page, landing_created = ShopLandingPage.objects.update_or_create(
                shop=shop,
                defaults=landing_defaults,
            )

            self.sync_testimonials(landing_page, entry)

            product_defaults = self.build_product_defaults(
                entry,
                shop,
                product_price,
                shop_slug,
            )
            product, product_created = Product.objects.update_or_create(
                sku=f"{shop_slug.upper()}-SIGNATURE",
                defaults=product_defaults,
            )
            product_catalogs = self.select_catalog_categories('product', 2, index)
            if product_catalogs:
                product.catalog_categories.set(product_catalogs)

            service_defaults = self.build_service_defaults(
                entry,
                shop,
                service_price,
                shop_slug,
            )
            service, service_created = ShopService.objects.update_or_create(
                shop=shop,
                slug=slugify(f"{shop_slug}-service"),
                defaults=service_defaults,
            )
            service_catalogs = self.select_catalog_categories('service', 2, index)
            if service_catalogs:
                service.catalog_categories.set(service_catalogs)

            summary_lines.append(
                f"{user.username}: user={'created' if user_created else 'updated'}, "
                f"shop={'created' if shop_created else 'updated'}, "
                f"landing={'created' if landing_created else 'updated'}, "
                f"product={'created' if product_created else 'updated'}, "
                f"service={'created' if service_created else 'updated'}"
            )

        self.stdout.write("\n".join(summary_lines))

    def get_or_create_user(self, entry, phone, email):
        normalized_phone = User.objects.normalize_phone(phone)
        user = User.objects.filter(phone=normalized_phone).first()
        password = "Test@1234"

        if user:
            updated = False
            if user.username != entry["username"]:
                user.username = entry["username"]
                updated = True
            if user.display_name != entry["visible_name"]:
                user.display_name = entry["visible_name"]
                updated = True
            if user.email != email:
                user.email = email
                updated = True
            if user.country != entry["country"]:
                user.country = entry["country"]
                updated = True
            if updated:
                user.save(update_fields=["username", "display_name", "email", "country"])
            if not user.check_password(password):
                user.set_password(password)
                user.save(update_fields=["password"])
            return user, False

        user = User.objects.create_user(
            phone=phone,
            password=password,
            username=entry["username"],
            display_name=entry["visible_name"],
            email=email,
            country=entry["country"],
            is_active=True,
        )
        return user, True

    def build_shop_defaults(self, entry, name, slug, idx):
        base_color = "#1f2937"
        accent = "#f59e0b"
        return {
            "name": name,
            "description": (
                f"{entry['visible_name']} builds curated experiences in {entry['city']} "
                f"that blend craft goods, thoughtful booking, and measurable outcomes."
            ),
            "employee_slots": 3 + (idx % 3),
            "branding": {
                "primary_color": base_color,
                "accent_color": accent,
                "logo_text": f"{entry['visible_name']} Atelier",
            },
            "is_verified": True,
            "verification_status": "VERIFIED",
            "rating_avg": round(4.6 + idx * 0.02, 2),
            "rating_count": 40 + idx,
            "followers_count": 110 + idx * 9,
            "membership_discount_pct": min(20, 5 + idx),
            "social_links": {
                "website": f"https://{slug}.demo.kis",
                "instagram": f"https://instagram.com/{entry['username']}",
                "linkedin": f"https://linkedin.com/company/{entry['username']}",
                "tiktok": f"https://tiktok.com/@{entry['username']}",
            },
            "analytics": {
                "monthly_visitors": 1200 + idx * 120,
                "conversion_rate": round(3.9 + idx * 0.05, 2),
                "last_month": {"orders": 14 + idx, "revenue_cents": (1200 + idx * 80) * 100},
            },
            "trust_badges": ["kyc-verified", "authenticity-checked", "secure-pay-ready"],
            "membership_public": True,
        }

    def build_landing_defaults(self, entry, shop):
        headline = f"{entry['visible_name']} orchestrates elevated living in {entry['city']}."
        return {
            "headline": headline,
            "subheadline": (
                f"Browse tailored products, book signature consultations, and tap into the {entry['visible_name']} methodology."
            ),
            "hero_image_url": f"https://images.kis.test/{shop.slug}-hero.jpg",
            "hero_cta_text": "Schedule a discovery call",
            "hero_cta_url": f"https://{shop.slug}.demo.kis/book",
            "is_public": True,
            "is_published": True,
            "created_by": shop.owner,
            "updated_by": shop.owner,
        }

    def sync_testimonials(self, landing_page, entry):
        quotes = [
            (
                f"{entry['visible_name']} delivered a flawless launch for our team.",
                "Morgan Rivera",
                "Creative Director",
                5,
            ),
            (
                f"The process from intake to delivery in {entry['city']} was exceptionally smooth.",
                "Jordan Lee",
                "Operations Lead",
                4,
            ),
        ]
        for order, (quote, author, role, rating) in enumerate(quotes):
            ShopLandingTestimonial.objects.update_or_create(
                landing_page=landing_page,
                quote=quote,
                defaults={
                    "author": author,
                    "role": role,
                    "rating": rating,
                    "sort_order": order,
                },
            )

    def build_product_defaults(self, entry, shop, price, slug):
        return {
            "shop": shop,
            "name": f"{entry['visible_name']} Signature Box",
            "slug": slugify(f"{slug}-product"),
            "image_url": f"https://images.kis.test/products/{shop.slug}-box.jpg",
            "description": (
                f"A {entry['visible_name']}-curated kit with artisan tools, bespoke documentation, "
                f"and premium packaging created for {entry['city']} and remote collaborators."
            ),
            "price": price,
            "currency": KIS_COIN_CODE,
            "inventory_type": "PHYSICAL",
            "stock_qty": 25 + entry_index(entry),
            "variants": [
                {"name": "Standard", "sku": f"{slug.upper()}-STD"},
                {"name": "Deluxe", "sku": f"{slug.upper()}-DLX"},
            ],
            "attributes": {
                "material": "Carbon fiber weave",
                "finish": "Satin graphite",
                "size": "One size fits most",
            },
            "is_active": True,
            "is_featured": True,
            "rating_avg": round(4.7 + entry_index(entry) * 0.01, 2),
            "rating_count": 32 + entry_index(entry),
            "ai_score": round(0.92 + entry_index(entry) * 0.003, 3),
            "ar_preview_url": f"https://ar.kis.test/{shop.slug}/product",
            "authenticity_status": "VERIFIED",
            "authenticity_proof": {
                "certificate_id": f"{shop.slug.upper()}-AUTH",
                "verified_at": timezone.now().isoformat(),
                "issuer": "KIS Auth Vault",
            },
            "availability": "Ships within 2 business days.",
            "coverage": f"{entry['city']}, {entry['state']}, United States",
            "location": f"{entry['city']}, {entry['state']}",
            "service_type": "Lifestyle Product",
            "other_shops_discount": Decimal("6.50"),
            "availability_rules": [{"name": "max_per_order", "value": 3}],
        }

    def build_service_defaults(self, entry, shop, price, slug):
        availability = {
            "slots": [
                {"day": "monday", "start": "09:00", "end": "18:00"},
                {"day": "wednesday", "start": "09:00", "end": "18:00"},
                {"day": "friday", "start": "09:00", "end": "18:00"},
            ],
            "timezone": entry["timezone"],
        }
        packages = [
            {
                "name": "Signature Session",
                "price": str(price.quantize(Decimal("0.01"))),
                "duration_minutes": 120,
                "description": "Discovery, immersive planning, and follow-up delivery.",
                "includes": ["Discovery call", "Deliverable deck", "60-day follow-up"],
            },
            {
                "name": "Immersive Day",
                "price": str((price + Decimal("180.00")).quantize(Decimal("0.01"))),
                "duration_minutes": 360,
                "description": "Half-day immersion for leadership teams.",
                "includes": ["Workshop", "Strategy document", "Executive recap"],
            },
        ]
        addons = [
            {
                "name": "Executive Recap",
                "price": "95.00",
                "description": "Detailed slide deck + 30-minute follow-up recap call.",
                "duration_minutes": 30,
            },
            {
                "name": "Creative Capture",
                "price": "65.00",
                "description": "On-site visual capture + highlight reel.",
                "duration_minutes": 45,
            },
        ]
        return {
            "shop": shop,
            "name": f"{entry['visible_name']} Advisory Studio",
            "short_summary": (
                f"Strategic sessions that pair thoughtful research with {entry['visible_name']}'s craft."
            ),
            "slug": slugify(f"{slug}-service"),
            "tags": ["bespoke", "membership", "strategy"],
            "description": (
                f"{entry['visible_name']} leads a multi-disciplinary team to translate business signals into "
                f"tactile experiences in {entry['city']} and online."
            ),
            "pricing_model": "membership",
            "service_type": "Consultation",
            "delivery_modes": ["in-person", "virtual"],
            "visibility": "public",
            "status": "published",
            "featured": True,
            "price": price,
            "compare_at_price": price + Decimal("120.00"),
            "deposit_amount": (price * Decimal("0.2")).quantize(Decimal("0.01")),
            "deposit_percent": Decimal("20.00"),
            "minimum_charge": price,
            "negotiable": False,
            "tax_inclusive": True,
            "quote_required": False,
            "availability": availability,
            "availability_rules": [
                {"rule": "min_notice_hours", "value": 24},
                {"rule": "max_advance_days", "value": 120},
            ],
            "blackout_dates": [
                timezone.now().date(),
                (timezone.now() + timedelta(days=14)).date(),
            ],
            "coverage": ["Metro area", "Regional travel"],
            "remote_regions": ["North America", "UK & EU"],
            "remote_meeting_link": f"https://meet.jit.si/{slug}-session",
            "address_line1": entry["street"],
            "address_line2": entry.get("suite", ""),
            "city": entry["city"],
            "state": entry["state"],
            "country": "USA",
            "postal_code": entry["postal_code"],
            "travel_radius_km": Decimal("70.00"),
            "timezone": entry["timezone"],
            "duration_minutes": 120,
            "prep_buffer_minutes": 15,
            "cleanup_buffer_minutes": 10,
            "turnaround_hours": 24,
            "max_bookings_per_slot": 2,
            "group_booking_allowed": True,
            "allow_multiple_attendees_per_slot": True,
            "max_participants": 8,
            "staff_required": 1,
            "min_notice_hours": 24,
            "max_advance_booking_days": 120,
            "cancellation_window_hours": 24,
            "reschedule_window_hours": 24,
            "auto_confirm_booking": True,
            "approval_required": False,
            "packages": packages,
            "addons": addons,
            "requirements": ["Completed intake form", "Materials submitted 24h before kickoff"],
            "refund_policy": "Full refund when canceled at least 48 hours before the booked slot.",
            "warranty_policy": "Deliverables supported for 30 days after completion.",
            "service_terms": "Client is responsible for providing workspace access and intake materials ahead of time.",
            "seo_title": f"{entry['visible_name']} Strategic Sessions",
            "seo_description": (
                f"Book {entry['visible_name']}'s signature consulting to translate ideas into action in {entry['city']}."
            ),
            "published_at": timezone.now(),
            "image_url": f"https://images.kis.test/services/{shop.slug}-cover.jpg",
            "rating_avg": round(4.8 + entry_index(entry) * 0.01, 2),
            "rating_count": 18 + entry_index(entry),
            "is_active": True,
            "is_featured": True,
            "other_shops_discount": Decimal("4.00"),
        }

    def select_catalog_categories(self, category_type: str, count: int, offset: int):
        categories = list(CatalogCategory.objects.filter(category_type=category_type).order_by('slug'))
        if not categories:
            return []
        return [
            categories[(offset + idx) % len(categories)]
            for idx in range(min(count, len(categories)))
        ]

    def update_model(self, instance, defaults):
        for key, value in defaults.items():
            setattr(instance, key, value)
        instance.save(update_fields=list(defaults.keys()))


def entry_index(entry):
    return next(
        (idx for idx, data in enumerate(MERCHANTS) if data["username"] == entry["username"]),
        0,
    )
