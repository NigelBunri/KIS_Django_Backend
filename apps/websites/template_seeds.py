"""
Hand-authored starter content for WebsiteTemplate, one per owner type —
a curated, honestly-sized set, not padded to look like a template
gallery. Section types/field names match the RN Website Builder
editor's own vocabulary (KIS/src/components/section-builder/types.ts),
since that's the only place these get edited afterward — an owner
picking a template should land on content they can immediately open and
tweak in the same editor, not something in a shape the editor doesn't
understand.
"""
import uuid

from apps.websites.models import WebsiteOwnerType


def _section(section_type: str, data: dict) -> dict:
    return {"id": str(uuid.uuid4()), "type": section_type, "data": data}


def _bg(color_key: str) -> dict:
    return {"sectionBackgroundImageUrl": "", "sectionBackgroundColorKey": color_key}


SHOP_STOREFRONT = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Quality goods, made with care",
                "subtitle": "Browse our latest collection and find something you'll love.",
                "ctaText": "Shop Now", "ctaLink": "",
            }),
            _section("about", {
                **_bg("mint_soft"), "title": "Our Story", "layout": "image_left", "imageUrl": "",
                "description": "We started this shop to bring thoughtfully made products to people who care about "
                                "quality as much as we do. Every item is chosen or made with real attention to detail.",
            }),
            _section("programs_services", {
                **_bg("slate_air"), "title": "What We Offer",
                "cards": [
                    {"id": "c1", "name": "Featured Products", "description": "Our best-selling items, hand-picked for you."},
                    {"id": "c2", "name": "New Arrivals", "description": "The latest additions to our catalog."},
                    {"id": "c3", "name": "Custom Orders", "description": "Get in touch for a piece made just for you."},
                ],
            }),
            _section("testimonials", {
                **_bg("sandstone"), "title": "What Customers Say",
                "items": [
                    {"id": "t1", "quote": "Fast shipping and even better quality than I expected.", "author": "A happy customer"},
                ],
            }),
            _section("contact_information", {
                **_bg("mint_soft"), "title": "Get In Touch", "phone": "", "email": "", "address": "",
            }),
        ],
    },
]

HEALTH_CLINIC = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Compassionate care, close to home",
                "subtitle": "Book an appointment with our team today.",
                "ctaText": "Book Appointment", "ctaLink": "",
            }),
            _section("about", {
                **_bg("mint_soft"), "title": "About Us", "layout": "image_right", "imageUrl": "",
                "description": "Our team is committed to providing accessible, high-quality care for you and your "
                                "family, in a setting where you're treated like a person, not a number.",
            }),
            _section("programs_services", {
                **_bg("slate_air"), "title": "Our Services",
                "cards": [
                    {"id": "c1", "name": "General Consultations", "description": "Same-week appointments for routine care."},
                    {"id": "c2", "name": "Follow-Up Care", "description": "Ongoing support for existing patients."},
                    {"id": "c3", "name": "Health Screenings", "description": "Preventive checks to catch issues early."},
                ],
            }),
            _section("hours", {
                **_bg("sandstone"), "title": "Hours",
                "days": [
                    {"id": "d1", "day": "Monday - Friday", "hours": "8:00 AM - 5:00 PM"},
                    {"id": "d2", "day": "Saturday", "hours": "9:00 AM - 1:00 PM"},
                    {"id": "d3", "day": "Sunday", "hours": "Closed"},
                ],
            }),
            _section("faqs", {
                **_bg("mint_soft"), "title": "Frequently Asked Questions",
                "items": [
                    {"id": "f1", "question": "Do you accept walk-ins?", "answer": "We recommend booking ahead, but we do our best to accommodate walk-ins when possible."},
                    {"id": "f2", "question": "How do I book an appointment?", "answer": "Use the Book Appointment button above, or contact us directly."},
                ],
            }),
            _section("contact_information", {
                **_bg("slate_air"), "title": "Contact Us", "phone": "", "email": "", "address": "",
            }),
        ],
    },
]

EDUCATION_INSTITUTE = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Learn something new today",
                "subtitle": "Practical courses taught by people who know the subject inside out.",
                "ctaText": "Browse Courses", "ctaLink": "",
            }),
            _section("about", {
                **_bg("mint_soft"), "title": "Why Learn With Us", "layout": "image_left", "imageUrl": "",
                "description": "We built our courses around real, practical outcomes — not just theory. "
                                "Every course is designed to leave you able to actually do the thing you came here to learn.",
            }),
            _section("programs_services", {
                **_bg("slate_air"), "title": "Popular Programs",
                "cards": [
                    {"id": "c1", "name": "Beginner Track", "description": "Start from zero, no prior experience needed."},
                    {"id": "c2", "name": "Advanced Track", "description": "Go deeper once you've got the fundamentals down."},
                    {"id": "c3", "name": "Certification Program", "description": "Finish with a credential that means something."},
                ],
            }),
            _section("testimonials", {
                **_bg("sandstone"), "title": "Student Success Stories",
                "items": [
                    {"id": "t1", "quote": "I went from knowing nothing to landing a job in the field.", "author": "A graduate"},
                ],
            }),
            _section("faqs", {
                **_bg("mint_soft"), "title": "Questions? Start Here",
                "items": [
                    {"id": "f1", "question": "Do I need any prior experience?", "answer": "Not for our Beginner Track — it's designed to start from zero."},
                    {"id": "f2", "question": "How long do courses take?", "answer": "It varies by course — check each course's page for details."},
                ],
            }),
            _section("contact_information", {
                **_bg("slate_air"), "title": "Get In Touch", "phone": "", "email": "", "address": "",
            }),
        ],
    },
]

PARTNER_MINISTRY = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Partnering for lasting impact",
                "subtitle": "See what we're working on and how you can get involved.",
                "ctaText": "Get Involved", "ctaLink": "",
            }),
            _section("about", {
                **_bg("mint_soft"), "title": "Our Mission", "layout": "image_right", "imageUrl": "",
                "description": "We exist to serve our community with real, sustained commitment — not one-off "
                                "gestures. Every project we take on is one we intend to see through.",
            }),
            _section("programs_services", {
                **_bg("slate_air"), "title": "What We're Working On",
                "cards": [
                    {"id": "c1", "name": "Community Programs", "description": "Ongoing initiatives serving the people around us."},
                    {"id": "c2", "name": "Partnerships", "description": "Working alongside other organizations toward shared goals."},
                ],
            }),
            _section("social_links", {
                **_bg("sandstone"), "title": "Follow Our Work", "links": [],
            }),
            _section("contact_information", {
                **_bg("mint_soft"), "title": "Contact Us", "phone": "", "email": "", "address": "",
            }),
        ],
    },
]

BROADCAST_CREATOR = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Welcome to the channel",
                "subtitle": "New content regularly — subscribe so you don't miss it.",
                "ctaText": "Follow", "ctaLink": "",
            }),
            _section("about", {
                **_bg("mint_soft"), "title": "About This Channel", "layout": "image_left", "imageUrl": "",
                "description": "Here's what this channel is about, who it's for, and what you can expect to find here.",
            }),
            _section("social_links", {
                **_bg("slate_air"), "title": "Find Me Elsewhere", "links": [],
            }),
            _section("faqs", {
                **_bg("sandstone"), "title": "Questions I Get a Lot",
                "items": [
                    {"id": "f1", "question": "How often do you post?", "answer": "Update this with your actual posting schedule."},
                ],
            }),
            _section("contact_information", {
                **_bg("mint_soft"), "title": "Get In Touch", "phone": "", "email": "", "address": "",
            }),
        ],
    },
]

TEMPLATE_SEEDS = [
    {
        "owner_type": WebsiteOwnerType.SHOP, "name": "Modern Storefront",
        "description": "A clean, product-first layout for a shop selling physical or digital goods.",
        "seed_pages": SHOP_STOREFRONT,
    },
    {
        "owner_type": WebsiteOwnerType.HEALTH_INSTITUTION, "name": "Clinic Essentials",
        "description": "Everything a health practice's first page needs: services, hours, and how to book.",
        "seed_pages": HEALTH_CLINIC,
    },
    {
        "owner_type": WebsiteOwnerType.EDUCATION_INSTITUTION, "name": "Course Provider",
        "description": "A layout built around programs, student outcomes, and enrollment.",
        "seed_pages": EDUCATION_INSTITUTE,
    },
    {
        "owner_type": WebsiteOwnerType.PARTNER, "name": "Ministry Partner",
        "description": "A mission-forward layout for an organization introducing its work.",
        "seed_pages": PARTNER_MINISTRY,
    },
    {
        "owner_type": WebsiteOwnerType.BROADCAST_CHANNEL, "name": "Creator Channel",
        "description": "A simple landing page for a broadcast channel's audience to find you.",
        "seed_pages": BROADCAST_CREATOR,
    },
]
