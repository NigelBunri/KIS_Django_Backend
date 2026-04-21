"""Fixed category catalog shared by the marketplace."""

from __future__ import annotations

from typing import Iterable

from .models import CatalogCategory, Shop, ShopCategory

PRODUCT_CATALOG_DEFINITIONS = [
    {"slug": "eco-friendly-packaging", "name": "Eco-Friendly Packaging", "category_type": "product", "sort_order": 10},
    {"slug": "smart-home-electronics", "name": "Smart Home Electronics", "category_type": "product", "sort_order": 20},
    {"slug": "artisanal-handcrafted-decor", "name": "Artisanal Handcrafted Decor", "category_type": "product", "sort_order": 30},
    {"slug": "organic-skincare", "name": "Organic Skincare", "category_type": "product", "sort_order": 40},
    {"slug": "ergonomic-office-furniture", "name": "Ergonomic Office Furniture", "category_type": "product", "sort_order": 50},
    {"slug": "gourmet-pantry-staples", "name": "Gourmet Pantry Staples", "category_type": "product", "sort_order": 60},
    {"slug": "wearable-fitness-tech", "name": "Wearable Fitness Tech", "category_type": "product", "sort_order": 70},
    {"slug": "sustainable-apparel", "name": "Sustainable Apparel", "category_type": "product", "sort_order": 80},
    {"slug": "modular-kitchenware", "name": "Modular Kitchenware", "category_type": "product", "sort_order": 90},
    {"slug": "pet-wellness-products", "name": "Pet Wellness Products", "category_type": "product", "sort_order": 100},
    {"slug": "educational-stem-toys", "name": "Educational STEM Toys", "category_type": "product", "sort_order": 110},
    {"slug": "professional-photography-gear", "name": "Professional Photography Gear", "category_type": "product", "sort_order": 120},
    {"slug": "outdoor-adventure-equipment", "name": "Outdoor Adventure Equipment", "category_type": "product", "sort_order": 130},
    {"slug": "vintage-collectibles", "name": "Vintage Collectibles", "category_type": "product", "sort_order": 140},
    {"slug": "hydroponic-gardening-kits", "name": "Hydroponic Gardening Kits", "category_type": "product", "sort_order": 150},
    {"slug": "luxury-leather-goods", "name": "Luxury Leather Goods", "category_type": "product", "sort_order": 160},
    {"slug": "acoustic-musical-instruments", "name": "Acoustic Musical Instruments", "category_type": "product", "sort_order": 170},
    {"slug": "minimalist-stationery", "name": "Minimalist Stationery", "category_type": "product", "sort_order": 180},
    {"slug": "high-performance-power-tools", "name": "High-Performance Power Tools", "category_type": "product", "sort_order": 190},
    {"slug": "biodegradable-cleaning-supplies", "name": "Biodegradable Cleaning Supplies", "category_type": "product", "sort_order": 200},
    {"slug": "yoga-mindfulness-gear", "name": "Yoga & Mindfulness Gear", "category_type": "product", "sort_order": 210},
    {"slug": "contemporary-wall-art", "name": "Contemporary Wall Art", "category_type": "product", "sort_order": 220},
    {"slug": "specialized-automotive-parts", "name": "Specialized Automotive Parts", "category_type": "product", "sort_order": 230},
    {"slug": "custom-jewelry-pieces", "name": "Custom Jewelry Pieces", "category_type": "product", "sort_order": 240},
    {"slug": "compact-travel-accessories", "name": "Compact Travel Accessories", "category_type": "product", "sort_order": 250},
]
SERVICE_PARENT_CATALOG_DEFINITIONS = [
    {"slug": "home-services", "name": "Home Services", "category_type": "service", "description": "Services delivered in or around the home.", "sort_order": 10},
    {"slug": "repairs-maintenance", "name": "Repairs & Maintenance", "category_type": "service", "description": "Repair, maintenance, and technical upkeep work.", "sort_order": 20},
    {"slug": "cleaning-services", "name": "Cleaning Services", "category_type": "service", "description": "Residential and commercial cleaning offers.", "sort_order": 30},
    {"slug": "beauty-personal-care", "name": "Beauty & Personal Care", "category_type": "service", "description": "Personal grooming, styling, and self-care services.", "sort_order": 40},
    {"slug": "health-wellness", "name": "Health & Wellness", "category_type": "service", "description": "Coaching, fitness, and wellness support services.", "sort_order": 50},
    {"slug": "professional-services", "name": "Professional Services", "category_type": "service", "description": "Business, legal, financial, and admin support.", "sort_order": 60},
    {"slug": "tech-digital-services", "name": "Tech & Digital Services", "category_type": "service", "description": "Technology, software, marketing, and digital operations.", "sort_order": 70},
    {"slug": "education-training", "name": "Education & Training", "category_type": "service", "description": "Tutoring, coaching, and structured learning services.", "sort_order": 80},
    {"slug": "events-entertainment", "name": "Events & Entertainment", "category_type": "service", "description": "Services for events, production, and entertainment.", "sort_order": 90},
    {"slug": "auto-services", "name": "Auto Services", "category_type": "service", "description": "Vehicle care, detailing, and maintenance services.", "sort_order": 100},
    {"slug": "installation-services", "name": "Installation Services", "category_type": "service", "description": "Setup and installation services for homes and businesses.", "sort_order": 110},
    {"slug": "creative-services", "name": "Creative Services", "category_type": "service", "description": "Design, content, media, and visual creative work.", "sort_order": 120},
]
SERVICE_CHILD_CATALOG_DEFINITIONS = [
    {"slug": "professional-interior-staging", "name": "Interior Styling & Home Staging", "category_type": "service", "parent_slug": "home-services", "sort_order": 11},
    {"slug": "landscape-architecture-design", "name": "Landscaping & Outdoor Design", "category_type": "service", "parent_slug": "home-services", "sort_order": 12},
    {"slug": "subscription-box-curation", "name": "Home Organization & Lifestyle Setup", "category_type": "service", "parent_slug": "home-services", "sort_order": 13},
    {"slug": "handyman-general-repairs", "name": "Handyman & General Repairs", "category_type": "service", "parent_slug": "repairs-maintenance", "sort_order": 21},
    {"slug": "plumbing-electrical-repair", "name": "Plumbing & Electrical Repairs", "category_type": "service", "parent_slug": "repairs-maintenance", "sort_order": 22},
    {"slug": "hvac-appliance-maintenance", "name": "HVAC & Appliance Maintenance", "category_type": "service", "parent_slug": "repairs-maintenance", "sort_order": 23},
    {"slug": "deep-cleaning", "name": "Deep Cleaning", "category_type": "service", "parent_slug": "cleaning-services", "sort_order": 31},
    {"slug": "waste-management", "name": "Waste Removal & Haulage", "category_type": "service", "parent_slug": "cleaning-services", "sort_order": 32},
    {"slug": "move-in-move-out-cleaning", "name": "Move-In / Move-Out Cleaning", "category_type": "service", "parent_slug": "cleaning-services", "sort_order": 33},
    {"slug": "wardrobe-styling-auditing", "name": "Personal Styling & Wardrobe Audit", "category_type": "service", "parent_slug": "beauty-personal-care", "sort_order": 41},
    {"slug": "beauty-makeup-services", "name": "Beauty & Makeup Services", "category_type": "service", "parent_slug": "beauty-personal-care", "sort_order": 42},
    {"slug": "barbering-braiding-grooming", "name": "Barbering, Braiding & Grooming", "category_type": "service", "parent_slug": "beauty-personal-care", "sort_order": 43},
    {"slug": "holistic-health-coaching", "name": "Health Coaching", "category_type": "service", "parent_slug": "health-wellness", "sort_order": 51},
    {"slug": "fitness-boot-camp", "name": "Fitness Training & Boot Camps", "category_type": "service", "parent_slug": "health-wellness", "sort_order": 52},
    {"slug": "massage-recovery-services", "name": "Massage & Recovery Services", "category_type": "service", "parent_slug": "health-wellness", "sort_order": 53},
    {"slug": "personalized-financial-planning", "name": "Financial Planning", "category_type": "service", "parent_slug": "professional-services", "sort_order": 61},
    {"slug": "language-translation", "name": "Translation & Interpretation", "category_type": "service", "parent_slug": "professional-services", "sort_order": 62},
    {"slug": "mobile-notary", "name": "Notary & Documentation", "category_type": "service", "parent_slug": "professional-services", "sort_order": 63},
    {"slug": "virtual-administrative-support", "name": "Virtual Assistance", "category_type": "service", "parent_slug": "professional-services", "sort_order": 64},
    {"slug": "digital-transformation-consulting", "name": "Digital Transformation Consulting", "category_type": "service", "parent_slug": "tech-digital-services", "sort_order": 71},
    {"slug": "remote-it-troubleshooting", "name": "IT Support & Troubleshooting", "category_type": "service", "parent_slug": "tech-digital-services", "sort_order": 72},
    {"slug": "custom-software-development", "name": "Custom Software Development", "category_type": "service", "parent_slug": "tech-digital-services", "sort_order": 73},
    {"slug": "seo-services", "name": "SEO & Search Marketing", "category_type": "service", "parent_slug": "tech-digital-services", "sort_order": 74},
    {"slug": "social-media-management", "name": "Social Media Management", "category_type": "service", "parent_slug": "tech-digital-services", "sort_order": 75},
    {"slug": "private-culinary-tutoring", "name": "Culinary Lessons & Coaching", "category_type": "service", "parent_slug": "education-training", "sort_order": 81},
    {"slug": "pet-behavioral-training", "name": "Specialized Behavioural Training", "category_type": "service", "parent_slug": "education-training", "sort_order": 82},
    {"slug": "academic-tutoring", "name": "Academic Tutoring", "category_type": "service", "parent_slug": "education-training", "sort_order": 83},
    {"slug": "event-drone-videography", "name": "Event Videography & Drone Coverage", "category_type": "service", "parent_slug": "events-entertainment", "sort_order": 91},
    {"slug": "sound-engineering-mixing", "name": "Live Sound & Audio Mixing", "category_type": "service", "parent_slug": "events-entertainment", "sort_order": 92},
    {"slug": "dj-hosting-emcee", "name": "DJ, MC & Event Hosting", "category_type": "service", "parent_slug": "events-entertainment", "sort_order": 93},
    {"slug": "mobile-car-detailing", "name": "Mobile Car Detailing", "category_type": "service", "parent_slug": "auto-services", "sort_order": 101},
    {"slug": "oil-change-basic-servicing", "name": "Oil Change & Basic Servicing", "category_type": "service", "parent_slug": "auto-services", "sort_order": 102},
    {"slug": "vehicle-diagnostics-repair", "name": "Vehicle Diagnostics & Repairs", "category_type": "service", "parent_slug": "auto-services", "sort_order": 103},
    {"slug": "ac-appliance-installation", "name": "AC & Appliance Installation", "category_type": "service", "parent_slug": "installation-services", "sort_order": 111},
    {"slug": "furniture-tv-mounting", "name": "Furniture Assembly & TV Mounting", "category_type": "service", "parent_slug": "installation-services", "sort_order": 112},
    {"slug": "solar-cctv-smart-home-setup", "name": "Solar, CCTV & Smart Home Setup", "category_type": "service", "parent_slug": "installation-services", "sort_order": 113},
    {"slug": "brand-identity-logo-design", "name": "Branding & Logo Design", "category_type": "service", "parent_slug": "creative-services", "sort_order": 121},
    {"slug": "architectural-visualization", "name": "Architectural Visualization", "category_type": "service", "parent_slug": "creative-services", "sort_order": 122},
    {"slug": "content-copywriting", "name": "Copywriting & Content Strategy", "category_type": "service", "parent_slug": "creative-services", "sort_order": 123},
]
SERVICE_CATALOG_DEFINITIONS = SERVICE_PARENT_CATALOG_DEFINITIONS + SERVICE_CHILD_CATALOG_DEFINITIONS
DEFAULT_CATALOG_DEFINITIONS = PRODUCT_CATALOG_DEFINITIONS + SERVICE_CATALOG_DEFINITIONS
DEFAULT_CATALOG_LOOKUP = {entry["slug"]: entry for entry in DEFAULT_CATALOG_DEFINITIONS}


def get_catalog_categories(category_type: str | None = None) -> Iterable[dict]:
    if category_type == "product":
        return PRODUCT_CATALOG_DEFINITIONS
    if category_type == "service":
        return SERVICE_CATALOG_DEFINITIONS
    return DEFAULT_CATALOG_DEFINITIONS


def get_catalog_category_definition(slug: str) -> dict | None:
    return DEFAULT_CATALOG_LOOKUP.get(slug)


def ensure_catalog_categories() -> None:
    created: dict[str, CatalogCategory] = {}
    for definition in DEFAULT_CATALOG_DEFINITIONS:
        if definition.get("parent_slug"):
            continue
        category, _ = CatalogCategory.objects.update_or_create(
            slug=definition["slug"],
            defaults={
                "name": definition["name"],
                "description": definition.get("description", ""),
                "category_type": definition["category_type"],
                "sort_order": definition.get("sort_order", 0),
                "parent": None,
            },
        )
        created[definition["slug"]] = category

    for definition in DEFAULT_CATALOG_DEFINITIONS:
        parent_slug = definition.get("parent_slug")
        if not parent_slug:
            continue
        parent = created.get(parent_slug) or CatalogCategory.objects.filter(slug=parent_slug).first()
        category, _ = CatalogCategory.objects.update_or_create(
            slug=definition["slug"],
            defaults={
                "name": definition["name"],
                "description": definition.get("description", ""),
                "category_type": definition["category_type"],
                "sort_order": definition.get("sort_order", 0),
                "parent": parent,
            },
        )
        created[definition["slug"]] = category


def ensure_default_shop_categories(shop_or_id: Shop | str | None) -> None:
    shop = shop_or_id if isinstance(shop_or_id, Shop) else None
    if shop is None and shop_or_id is not None:
        shop = Shop.objects.filter(id=shop_or_id).first()
    if not shop:
        return
    for definition in DEFAULT_CATALOG_DEFINITIONS:
        ShopCategory.objects.update_or_create(
            shop=shop,
            slug=definition["slug"],
            defaults={
                "name": definition["name"],
                "description": definition.get("description", ""),
                "category_type": definition["category_type"],
            },
        )
