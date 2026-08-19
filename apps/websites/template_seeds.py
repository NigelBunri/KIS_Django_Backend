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

SHOP_BOLD_COLLECTION = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("slate_air"), "backgroundImageUrl": "",
                "title": "Made for people who notice the details",
                "subtitle": "Limited runs. Real materials. No filler.",
                "ctaText": "Shop the Collection", "ctaLink": "",
            }),
            _section("statistics", {
                **_bg("mint_soft"), "title": "By the Numbers",
                "metrics": [
                    {"id": "m1", "label": "Happy Customers", "value": "1,000+"},
                    {"id": "m2", "label": "5-Star Reviews", "value": "98%"},
                    {"id": "m3", "label": "Ships In", "value": "48hrs"},
                ],
            }),
            _section("about", {
                **_bg("ocean_mist"), "title": "Why We're Different", "layout": "image_right", "imageUrl": "",
                "description": "We keep runs small on purpose — every piece gets real attention instead of being "
                                "one of a thousand identical units. When it's gone, it's gone.",
            }),
            _section("faqs", {
                **_bg("sandstone"), "title": "Before You Buy",
                "items": [
                    {"id": "f1", "question": "What's your return policy?", "answer": "Update this with your actual return window and process."},
                    {"id": "f2", "question": "Do you ship internationally?", "answer": "Update this with your actual shipping coverage."},
                ],
            }),
            _section("social_links", {**_bg("mint_soft"), "title": "Follow the Drops", "links": []}),
            _section("contact_information", {**_bg("slate_air"), "title": "Reach Us", "phone": "", "email": "", "address": ""}),
        ],
    },
]

HEALTH_TELEHEALTH_BOOKING = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("mint_soft"), "backgroundImageUrl": "",
                "title": "See a provider today, from wherever you are",
                "subtitle": "In-person and virtual visits — book in under two minutes.",
                "ctaText": "Book a Visit", "ctaLink": "",
            }),
            _section("programs_services", {
                **_bg("ocean_mist"), "title": "Ways to Be Seen",
                "cards": [
                    {"id": "c1", "name": "Virtual Visit", "description": "Talk to a provider from home for non-urgent care."},
                    {"id": "c2", "name": "In-Person Visit", "description": "Come to us for exams and hands-on care."},
                    {"id": "c3", "name": "Follow-Up", "description": "Ongoing check-ins for existing patients."},
                ],
            }),
            _section("faqs", {
                **_bg("slate_air"), "title": "Telehealth, Explained",
                "items": [
                    {"id": "f1", "question": "Is a virtual visit right for me?", "answer": "Good for non-urgent concerns, follow-ups, and prescription renewals — update with your own guidance."},
                    {"id": "f2", "question": "What do I need for a virtual visit?", "answer": "A phone or computer with a camera and a stable connection."},
                    {"id": "f3", "question": "Is it covered by insurance?", "answer": "Update this with your actual coverage/pricing info."},
                ],
            }),
            _section("hours", {
                **_bg("sandstone"), "title": "When We're Available",
                "days": [
                    {"id": "d1", "day": "Monday - Friday", "hours": "8:00 AM - 6:00 PM"},
                    {"id": "d2", "day": "Saturday", "hours": "10:00 AM - 2:00 PM"},
                ],
            }),
            _section("contact_information", {**_bg("mint_soft"), "title": "Questions First?", "phone": "", "email": "", "address": ""}),
        ],
    },
]

EDUCATION_COHORT_ENROLLMENT = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("slate_air"), "backgroundImageUrl": "",
                "title": "The next cohort starts soon",
                "subtitle": "A small group, real feedback, and an outcome you can point to.",
                "ctaText": "Reserve Your Spot", "ctaLink": "",
            }),
            _section("statistics", {
                **_bg("ocean_mist"), "title": "Track Record",
                "metrics": [
                    {"id": "m1", "label": "Graduates", "value": "500+"},
                    {"id": "m2", "label": "Completion Rate", "value": "92%"},
                    {"id": "m3", "label": "Avg. Rating", "value": "4.8/5"},
                ],
            }),
            _section("testimonials", {
                **_bg("mint_soft"), "title": "From Past Cohorts",
                "items": [
                    {"id": "t1", "quote": "Small enough that the instructor actually knew my name and where I was stuck.", "author": "A recent graduate"},
                ],
            }),
            _section("faqs", {
                **_bg("sandstone"), "title": "Before You Enroll",
                "items": [
                    {"id": "f1", "question": "When does the next cohort start?", "answer": "Update this with your actual upcoming dates."},
                    {"id": "f2", "question": "Is there a payment plan?", "answer": "Update this with your actual payment options."},
                ],
            }),
            _section("contact_information", {**_bg("slate_air"), "title": "Have Questions?", "phone": "", "email": "", "address": ""}),
        ],
    },
]

PARTNER_IMPACT_REPORT = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("ocean_mist"), "backgroundImageUrl": "",
                "title": "Here's what your support made possible",
                "subtitle": "A transparent look at the work, in numbers and in people.",
                "ctaText": "Support This Work", "ctaLink": "",
            }),
            _section("statistics", {
                **_bg("slate_air"), "title": "This Year's Impact",
                "metrics": [
                    {"id": "m1", "label": "People Reached", "value": "10,000+"},
                    {"id": "m2", "label": "Communities Served", "value": "24"},
                    {"id": "m3", "label": "Volunteer Hours", "value": "3,200"},
                ],
            }),
            _section("programs_services", {
                **_bg("mint_soft"), "title": "Where the Work Happens",
                "cards": [
                    {"id": "c1", "name": "Direct Programs", "description": "Ongoing, hands-on work in the communities we serve."},
                    {"id": "c2", "name": "Partnerships", "description": "Collaborations that multiply what we can do alone."},
                ],
            }),
            _section("testimonials", {
                **_bg("sandstone"), "title": "In Their Words",
                "items": [
                    {"id": "t1", "quote": "What they built here changed how our whole community thinks about this work.", "author": "A community partner"},
                ],
            }),
            _section("social_links", {**_bg("ocean_mist"), "title": "Follow the Work", "links": []}),
            _section("contact_information", {**_bg("slate_air"), "title": "Get In Touch", "phone": "", "email": "", "address": ""}),
        ],
    },
]

BROADCAST_HIGHLIGHT_REEL = [
    {
        "slug": "", "title": "Home", "is_home": True, "sort_order": 0,
        "sections": [
            _section("hero_banner", {
                **_bg("slate_air"), "backgroundImageUrl": "",
                "title": "New here? Start with the highlights",
                "subtitle": "A quick look at what this channel is all about.",
                "ctaText": "Subscribe", "ctaLink": "",
            }),
            _section("programs_services", {
                **_bg("mint_soft"), "title": "What You'll Find Here",
                "cards": [
                    {"id": "c1", "name": "Weekly Uploads", "description": "New content on a regular schedule — update with your own cadence."},
                    {"id": "c2", "name": "Behind the Scenes", "description": "The stuff that doesn't make the main feed."},
                ],
            }),
            _section("testimonials", {
                **_bg("ocean_mist"), "title": "What Viewers Say",
                "items": [
                    {"id": "t1", "quote": "Found this channel and immediately watched everything.", "author": "A subscriber"},
                ],
            }),
            _section("social_links", {**_bg("sandstone"), "title": "Find Me Everywhere", "links": []}),
            _section("contact_information", {**_bg("mint_soft"), "title": "Business Inquiries", "phone": "", "email": "", "address": ""}),
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
    {
        "owner_type": WebsiteOwnerType.SHOP, "name": "Bold Collection",
        "description": "A punchier, trust-numbers-forward layout for a shop selling limited or curated runs.",
        "seed_pages": SHOP_BOLD_COLLECTION,
    },
    {
        "owner_type": WebsiteOwnerType.HEALTH_INSTITUTION, "name": "Telehealth & Booking",
        "description": "Built around getting a visitor booked — virtual and in-person options up front.",
        "seed_pages": HEALTH_TELEHEALTH_BOOKING,
    },
    {
        "owner_type": WebsiteOwnerType.EDUCATION_INSTITUTION, "name": "Cohort & Enrollment",
        "description": "A layout built around a specific upcoming cohort and enrollment deadline.",
        "seed_pages": EDUCATION_COHORT_ENROLLMENT,
    },
    {
        "owner_type": WebsiteOwnerType.PARTNER, "name": "Impact Report",
        "description": "A numbers-and-testimonials layout for reporting back to supporters.",
        "seed_pages": PARTNER_IMPACT_REPORT,
    },
    {
        "owner_type": WebsiteOwnerType.BROADCAST_CHANNEL, "name": "Highlight Reel",
        "description": "A first-impression layout for a channel's most-asked-about content and links.",
        "seed_pages": BROADCAST_HIGHLIGHT_REEL,
    },
]
