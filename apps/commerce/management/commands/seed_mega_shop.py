"""Amazon-benchmark mega-shop test data: a spectrum of sellers (new/small/
established/high-volume/poor-rated/excellent-rated/many-products/few-products)
with a large, realistic catalog spread across the existing product category
catalog (apps/commerce/category_catalog.py) - enough scale to exercise
pagination, search, filtering, and performance, not just a handful of demo
items. Complements seed_demo_merchants (which creates 15 uniform "good"
single-product shops) rather than replacing it - run both for full coverage.

Idempotent: safe to re-run, keyed by shop slug / product sku.
"""
from __future__ import annotations

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models.signals import post_save
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User
from apps.commerce.category_catalog import ensure_catalog_categories
from apps.commerce.signals import on_product_save
from apps.commerce.models import (
    CatalogCategory,
    Product,
    ProductReview,
    ProductVariant,
    Promotion,
    Shop,
    ShopPayoutAccountStatus,
)

ADJECTIVES = [
    "Premium", "Classic", "Compact", "Heavy-Duty", "Portable", "Pro", "Essential",
    "Deluxe", "Everyday", "Studio", "Travel", "Rugged", "Lightweight", "Signature",
    "Modular", "All-Weather", "Precision", "Artisan", "Ultra", "Core",
]

SIZES = ["XS", "S", "M", "L", "XL"]
COLORS = ["Charcoal", "Ivory", "Forest Green", "Slate Blue", "Terracotta", "Graphite"]


def _noun_from_category(category_name: str) -> str:
    # "Smart Home Electronics" -> "Electronics", "Acoustic Musical Instruments" -> "Instruments"
    return category_name.split()[-1].rstrip("s") or category_name


def _product_name(category_name: str, index: int) -> str:
    adjective = ADJECTIVES[index % len(ADJECTIVES)]
    noun = _noun_from_category(category_name)
    model_no = 100 + index
    return f"{adjective} {noun} {model_no}"


# (key, display name, seller_profile)
# seller_profile tunes rating/verification/payout/product_count/stock
# distribution so the roster spans the full spectrum the acceptance spec
# calls for, not just uniformly "good" sellers.
SHOPS = [
    {
        "key": "new-horizon-finds",
        "display": "New Horizon Finds",
        "owner_phone": "5566010001",
        "profile": "new_unverified",
        "product_count": 2,
        "categories": ["minimalist-stationery", "compact-travel-accessories"],
        "rating_avg": 0.0,
        "rating_count": 0,
        "is_verified": False,
        "verification_status": "UNVERIFIED",
        "followers_count": 0,
        "payout_connected": False,
    },
    {
        "key": "the-minimalist-shelf",
        "display": "The Minimalist Shelf",
        "owner_phone": "5566010002",
        "profile": "few_products",
        "product_count": 2,
        "categories": ["minimalist-stationery"],
        "rating_avg": 4.3,
        "rating_count": 9,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 64,
        "payout_connected": True,
    },
    {
        "key": "pixel-thread-studio",
        "display": "Pixel & Thread Studio",
        "owner_phone": "5566010003",
        "profile": "small",
        "product_count": 10,
        "categories": ["minimalist-stationery", "contemporary-wall-art", "custom-jewelry-pieces"],
        "rating_avg": 4.5,
        "rating_count": 38,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 210,
        "payout_connected": True,
    },
    {
        "key": "heritage-oak-co",
        "display": "Heritage & Oak Co.",
        "owner_phone": "5566010004",
        "profile": "established",
        "product_count": 45,
        "categories": [
            "ergonomic-office-furniture", "modular-kitchenware", "artisanal-handcrafted-decor",
            "luxury-leather-goods", "gourmet-pantry-staples",
        ],
        "rating_avg": 4.6,
        "rating_count": 412,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 3100,
        "payout_connected": True,
    },
    {
        "key": "quickfix-essentials",
        "display": "QuickFix Essentials",
        "owner_phone": "5566010005",
        "profile": "poor_rated",
        "product_count": 20,
        "categories": ["high-performance-power-tools", "specialized-automotive-parts"],
        "rating_avg": 2.3,
        "rating_count": 156,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 340,
        "payout_connected": True,
    },
    {
        "key": "golden-era-vintage",
        "display": "Golden Era Vintage",
        "owner_phone": "5566010006",
        "profile": "excellent_rated",
        "product_count": 6,
        "categories": ["vintage-collectibles", "luxury-leather-goods"],
        "rating_avg": 4.9,
        "rating_count": 520,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 8900,
        "payout_connected": True,
        "backdate_days": 1500,
    },
    {
        "key": "northstar-outdoor-supply",
        "display": "Northstar Outdoor Supply",
        "owner_phone": "5566010007",
        "profile": "established",
        "product_count": 55,
        "categories": [
            "outdoor-adventure-equipment", "high-performance-power-tools",
            "specialized-automotive-parts", "hydroponic-gardening-kits",
        ],
        "rating_avg": 4.4,
        "rating_count": 890,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 5200,
        "payout_connected": True,
    },
    {
        "key": "sunrise-wellness-collective",
        "display": "Sunrise Wellness Collective",
        "owner_phone": "5566010008",
        "profile": "small",
        "product_count": 25,
        "categories": ["organic-skincare", "yoga-mindfulness-gear", "pet-wellness-products"],
        "rating_avg": 4.7,
        "rating_count": 203,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 1450,
        "payout_connected": True,
    },
    {
        "key": "ironclad-tools-co",
        "display": "Ironclad Tools Co",
        "owner_phone": "5566010009",
        "profile": "established",
        "product_count": 35,
        "categories": ["high-performance-power-tools", "specialized-automotive-parts"],
        "rating_avg": 4.1,
        "rating_count": 275,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 980,
        "payout_connected": True,
    },
    {
        "key": "nimbus-tech-bazaar",
        "display": "Nimbus Tech Bazaar",
        "owner_phone": "5566010010",
        "profile": "high_volume",
        "product_count": 160,
        "categories": [
            "smart-home-electronics", "wearable-fitness-tech", "professional-photography-gear",
            "acoustic-musical-instruments", "educational-stem-toys",
        ],
        "rating_avg": 4.4,
        "rating_count": 6200,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 41000,
        "payout_connected": True,
    },
    {
        "key": "evergreen-mega-mart",
        "display": "Evergreen Mega Mart",
        "owner_phone": "5566010011",
        "profile": "high_volume",
        "product_count": 160,
        "categories": [
            "eco-friendly-packaging", "sustainable-apparel", "biodegradable-cleaning-supplies",
            "gourmet-pantry-staples", "modular-kitchenware", "pet-wellness-products",
        ],
        "rating_avg": 4.2,
        "rating_count": 5100,
        "is_verified": True,
        "verification_status": "VERIFIED",
        "followers_count": 28500,
        "payout_connected": True,
    },
]


class Command(BaseCommand):
    help = (
        "Seed a mega-shop test marketplace: sellers spanning the full spectrum "
        "(new/small/established/high-volume/poor-rated/excellent-rated/"
        "many-products/few-products) with a large catalog across the existing "
        "product category catalog. Complements seed_demo_merchants."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed", type=int, default=20260928,
            help="Random seed for deterministic re-runs (default: fixed).",
        )

    def handle(self, *args, **options):
        # See seed_demo_merchants.py for why this signal is disconnected
        # during bulk seeding - compute_recommendations.delay() blocked
        # indefinitely outside a request/task context in this environment.
        post_save.disconnect(on_product_save, sender=Product)
        try:
            self._run(*args, **options)
        finally:
            post_save.connect(on_product_save, sender=Product)

    def _run(self, *args, **options):
        random.seed(options["seed"])
        ensure_catalog_categories()

        buyer = self._ensure_buyer()
        critic = self._ensure_critic()

        summary = []
        total_products = 0
        for spec in SHOPS:
            shop, shop_created = self._ensure_shop(spec)
            categories = list(
                CatalogCategory.objects.filter(slug__in=spec["categories"])
            )
            if not categories:
                self.stdout.write(self.style.WARNING(
                    f"No matching categories for {spec['key']}; skipping products."
                ))
                continue

            created_count, updated_count = self._ensure_products(shop, spec, categories)
            total_products += created_count + updated_count

            if spec["profile"] == "poor_rated":
                self._seed_bad_reviews(shop, critic)
            elif spec["profile"] == "excellent_rated":
                self._seed_great_reviews(shop, buyer)

            summary.append(
                f"{shop.slug}: shop={'created' if shop_created else 'updated'}, "
                f"products created={created_count} updated={updated_count}"
            )

        self._seed_promotions()

        self.stdout.write("\n".join(summary))
        self.stdout.write(self.style.SUCCESS(
            f"Mega-shop seed complete: {len(SHOPS)} shops, {total_products} products touched."
        ))

    # -- sellers -----------------------------------------------------

    def _ensure_buyer(self):
        user, _ = self._get_or_create_user(
            phone="5566020001", username="mega_buyer_demo", display_name="Mega Shop Reviewer",
        )
        return user

    def _ensure_critic(self):
        user, _ = self._get_or_create_user(
            phone="5566020002", username="mega_critic_demo", display_name="Unhappy Customer",
        )
        return user

    def _get_or_create_user(self, phone, username, display_name):
        # The KIS app's login screen always sends a full E.164 number
        # (dialCode + digits, e.g. "+1" + "5566020001" for a US account) -
        # storing the bare digits here would create an account the real
        # app's login form could never actually reach.
        e164_phone = phone if phone.startswith('+') else f'+1{phone}'
        normalized_phone = User.objects.normalize_phone(e164_phone)
        user = User.objects.filter(phone=normalized_phone).first()
        if user:
            return user, False
        user = User.objects.create_user(
            phone=e164_phone, password="Test@1234", username=username,
            display_name=display_name, email=f"{username}@demo.kis",
            country="US", is_active=True,
        )
        return user, True

    def _ensure_shop(self, spec):
        owner, _ = self._get_or_create_user(
            phone=spec["owner_phone"], username=f"owner_{spec['key'].replace('-', '_')}",
            display_name=f"{spec['display']} Owner",
        )
        defaults = {
            "owner": owner,
            "name": spec["display"],
            "description": f"{spec['display']} - a {spec['profile'].replace('_', ' ')} seller on the KIS marketplace.",
            "is_verified": spec["is_verified"],
            "verification_status": spec["verification_status"],
            "rating_avg": spec["rating_avg"],
            "rating_count": spec["rating_count"],
            "followers_count": spec["followers_count"],
            "trust_badges": ["kyc-verified"] if spec["is_verified"] else [],
            "payout_account_status": (
                ShopPayoutAccountStatus.ACTIVE if spec["payout_connected"]
                else ShopPayoutAccountStatus.NOT_CONNECTED
            ),
            "flutterwave_subaccount_id": f"RS_MEGA_{spec['key'].upper().replace('-', '_')}" if spec["payout_connected"] else "",
        }
        shop, created = Shop.objects.get_or_create(slug=spec["key"], defaults=defaults)
        if not created:
            for field, value in defaults.items():
                setattr(shop, field, value)
            shop.save(update_fields=list(defaults.keys()))

        backdate_days = spec.get("backdate_days")
        if backdate_days:
            Shop.objects.filter(id=shop.id).update(
                created_at=timezone.now() - timezone.timedelta(days=backdate_days)
            )
        return shop, created

    # -- catalog -------------------------------------------------------

    def _ensure_products(self, shop, spec, categories):
        created_count = 0
        updated_count = 0
        shop_prefix = spec["key"].upper().replace("-", "")[:12]

        for index in range(spec["product_count"]):
            category = categories[index % len(categories)]
            name = _product_name(category.name, index)
            sku = f"{shop_prefix}-{index:04d}"
            slug = slugify(f"{spec['key']}-{name}-{index}")

            stock_qty = self._stock_for(spec["profile"], index)
            price = Decimal("12.00") + Decimal((index * 37) % 480)
            on_sale = index % 5 == 0
            sale_price = (price * Decimal("0.85")).quantize(Decimal("0.01")) if on_sale else None

            defaults = {
                "shop": shop,
                "name": name,
                "description": (
                    f"{name} from {shop.name}. Part of the {category.name} collection."
                ),
                "price": price,
                "sale_price": sale_price,
                "currency": "USD",
                "inventory_type": "PHYSICAL",
                "stock_qty": stock_qty,
                "low_stock_threshold": 5,
                "attributes": {
                    "category": category.name,
                },
                "is_active": True,
                "is_featured": index % 11 == 0,
            }
            product, created = Product.objects.update_or_create(sku=sku, defaults={**defaults, "slug": slug})
            product.catalog_categories.set([category])

            if index % 4 == 0:
                self._ensure_variants(product, sku)

            if created:
                created_count += 1
            else:
                updated_count += 1

        return created_count, updated_count

    def _stock_for(self, profile, index):
        if profile == "new_unverified":
            return 15
        if index % 23 == 0:
            return 0  # out of stock - exercises OOS UI/filtering
        if index % 13 == 0:
            return random.randint(1, 4)  # low stock - exercises low-stock badges
        return random.randint(10, 250)

    def _ensure_variants(self, product, sku):
        ProductVariant.objects.update_or_create(
            product=product, sku=f"{sku}-S",
            defaults={"size": SIZES[0], "color": COLORS[0], "price": product.price, "stock_qty": max(product.stock_qty // 2, 0), "is_active": True},
        )
        ProductVariant.objects.update_or_create(
            product=product, sku=f"{sku}-L",
            defaults={"size": SIZES[3], "color": COLORS[1], "price": product.price + Decimal("5.00"), "stock_qty": max(product.stock_qty - product.stock_qty // 2, 0), "is_active": True},
        )

    # -- reviews ---------------------------------------------------------

    def _seed_bad_reviews(self, shop, critic):
        product = shop.products.order_by("sku").first()
        if not product:
            return
        ProductReview.objects.update_or_create(
            product=product, user=critic,
            defaults={
                "rating": 1,
                "title": "Arrived damaged, slow response",
                "body": "Item was broken on arrival and support took over a week to respond. Would not order again.",
                "status": ProductReview.STATUS_PUBLISHED,
                "helpful_count": 14,
            },
        )

    def _seed_great_reviews(self, shop, buyer):
        product = shop.products.order_by("sku").first()
        if not product:
            return
        ProductReview.objects.update_or_create(
            product=product, user=buyer,
            defaults={
                "rating": 5,
                "title": "Exactly as described, beautifully packaged",
                "body": "Authentic piece, fast shipping, and the seller followed up to make sure everything arrived safely.",
                "status": ProductReview.STATUS_PUBLISHED,
                "helpful_count": 51,
            },
        )

    # -- promotions ------------------------------------------------------

    def _seed_promotions(self):
        now = timezone.now()
        heritage = Shop.objects.filter(slug="heritage-oak-co").first()
        nimbus = Shop.objects.filter(slug="nimbus-tech-bazaar").first()
        if heritage:
            Promotion.objects.update_or_create(
                shop=heritage, code="HERITAGE10",
                defaults={
                    "description": "10% off storewide",
                    "discount_type": "PERCENT",
                    "discount_value": Decimal("10"),
                    "start_date": now - timezone.timedelta(days=5),
                    "end_date": now + timezone.timedelta(days=30),
                    "usage_limit": 500,
                },
            )
            Promotion.objects.update_or_create(
                shop=heritage, code="HERITAGE_EXPIRED",
                defaults={
                    "description": "Expired promo for negative-path testing",
                    "discount_type": "FIXED",
                    "discount_value": Decimal("15"),
                    "start_date": now - timezone.timedelta(days=60),
                    "end_date": now - timezone.timedelta(days=30),
                    "usage_limit": None,
                },
            )
        if nimbus:
            Promotion.objects.update_or_create(
                shop=nimbus, code="TECHFLASH25",
                defaults={
                    "description": "Flash sale - $25 off, limited redemptions",
                    "discount_type": "FIXED",
                    "discount_value": Decimal("25"),
                    "start_date": now - timezone.timedelta(days=1),
                    "end_date": now + timezone.timedelta(days=3),
                    "usage_limit": 10,
                    "used_count": 10,
                },
            )
