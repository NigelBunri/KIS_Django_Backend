"""
Website Builder app (websites)

Unifies the previously-scattered "landing page" concepts across Shop
(commerce.ShopLandingPage), Health (health_dashboard.
HealthDashboardInstitutionLandingPage), Education (broadcasts.
EducationInstitution.branding), and Partner (partners.PartnerSetting
key="landing_page_builder") into one polymorphically-owned, multi-page
website model, extended to also cover Broadcast channels.

None of those legacy models/tables are touched, migrated, or deleted by
this app — see apps.websites.adapters for the lazy, read-through seeding
approach that brings an owner's existing landing data forward into a
Website only the first time they open the new builder.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class BaseEntity(models.Model):
    """UUID-keyed, timestamped abstract base — every app in this codebase
    defines its own copy of this rather than importing a shared one (see
    apps.commerce.models.BaseEntity, apps.core.models.BaseEntity,
    apps.health_ops.models.TimeStampedUUIDModel); this follows that
    established convention."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class WebsiteOwnerType(models.TextChoices):
    """Mirrors apps.broadcasts.models.BroadcastChannel.OwnerType's shape,
    but unlike that enum (whose SHOP/HEALTH/EDUCATION/PARTNER values are
    declared and never actually constructed anywhere), every value here is
    wired to a real owner-resolution path — see owner_resolution.py."""

    SHOP = "shop", "Shop"
    HEALTH_INSTITUTION = "health_institution", "Health Institution"
    EDUCATION_INSTITUTION = "education_institution", "Education Institution"
    PARTNER = "partner", "Partner"
    BROADCAST_CHANNEL = "broadcast_channel", "Broadcast Channel"


class WebsiteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    UNPUBLISHED = "unpublished", "Unpublished"


class Website(BaseEntity):
    """One per owner entity (Shop/HealthInstitution/EducationInstitution/
    Partner/BroadcastChannel) — enforced by the unique_owner constraint
    below. `slug` is globally unique and is the public URL segment:
    kingdomimpactventures.org/page/<slug>."""

    owner_type = models.CharField(max_length=32, choices=WebsiteOwnerType.choices, db_index=True)
    owner_id = models.UUIDField(db_index=True)

    slug = models.SlugField(max_length=140, unique=True)
    name = models.CharField(max_length=255, blank=True, default="")

    # {colors: {primary, secondary, background, text}, typography:
    # {heading_font, body_font}, spacing: {section_gap, density},
    # logo_url, favicon_url}
    branding = models.JSONField(default=dict, blank=True)

    # {title, description, share_image_url} — per-page seo overrides this.
    default_seo = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=WebsiteStatus.choices, default=WebsiteStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    unpublished_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="websites_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="websites_updated",
    )

    # Set once the lazy adapter (apps.websites.adapters) has run for this
    # owner — never re-run after the first time, even if it skipped (e.g.
    # Health's ambiguous-match case), so an owner's own subsequent edits
    # are never silently overwritten by a second adaptation attempt.
    seeded_from_legacy = models.BooleanField(default=False)
    seeded_from_legacy_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "website"
        constraints = [
            models.UniqueConstraint(fields=["owner_type", "owner_id"], name="website_unique_owner"),
        ]
        indexes = [
            models.Index(fields=["owner_type", "owner_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.name or self.slug


class WebsitePage(BaseEntity):
    """A page under a Website. `slug=""` is reserved for the Home page
    (public URL kingdomimpactventures.org/page/<website.slug>); any other
    slug nests under it (.../page/<website.slug>/<page.slug>)."""

    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="pages")

    slug = models.SlugField(max_length=140, blank=True, default="")
    title = models.CharField(max_length=255)
    is_home = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    # list[{id: <uuid4 str>, type: <str>, data: {...}}] — freeform, no
    # per-type DB schema, generalizing ShopLandingPage.builder_data's
    # existing shape (apps.commerce.serializers._build_landing_builder_payload)
    # across all 5 owner types. See SECTION_TYPES below for the closed
    # vocabulary of `type` values this app understands when rendering.
    sections = models.JSONField(default=list, blank=True)

    seo = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=WebsiteStatus.choices, default=WebsiteStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="website_pages_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="website_pages_updated",
    )

    class Meta:
        db_table = "website_page"
        constraints = [
            models.UniqueConstraint(fields=["website", "slug"], name="website_page_unique_slug"),
            models.UniqueConstraint(fields=["website"], condition=models.Q(is_home=True), name="website_one_home_page"),
        ]
        indexes = [
            models.Index(fields=["website", "status"]),
        ]
        ordering = ["sort_order", "created_at"]

    def __str__(self):
        return f"{self.title} ({self.website.slug}/{self.slug or ''})"


# Closed vocabulary for WebsitePage.sections[].type — enforced at the
# serializer layer (apps.websites.serializers), not a DB constraint, since
# the section body itself is intentionally freeform JSON.
#
# hero_banner/about/image_gallery_grid/statistics/programs_services/
# call_to_action/contact_information are the RN Website Builder editor's
# own section vocabulary (apps/section-builder toolkit, originally built
# for the older per-owner-type legacy landing pages and reused as-is for
# this app's editor screens — see KIS/src/components/section-builder/
# types.ts). They're accepted here verbatim, field names and all, rather
# than translated to this app's own hero/text/gallery/cta/contact_info
# names, since the RN editor already writes this exact shape and a
# translation layer would just be one more thing to keep in sync. See
# website repo's components/website-builder/SectionRenderer.tsx for the
# matching render cases.
SECTION_TYPES = (
    "hero",
    "hero_banner",
    "text",
    "about",
    "image",
    "gallery",
    "image_gallery_grid",
    "video",
    "testimonials",
    "statistics",
    "programs_services",
    "faqs",
    "social_links",
    "contact_info",
    "contact_information",
    "hours",
    "cta",
    "call_to_action",
    "map",
    "form",
    "kis_content",
)

# Closed vocabulary for a `kis_content` section's data.target_type — the
# same target_type/target_id idiom already used by
# apps.billing.models.DirectPaymentIntent, resolved live (never persisted)
# by apps.websites.kis_content_resolvers at read time.
KIS_CONTENT_TARGET_TYPES = (
    "course",
    "product",
    "shop_service",
    "health_service",
    "broadcast_channel",
    "post",
    "event",
    "testimonial",
)

# Closed vocabulary for a `form` section's data.fields[].type — deliberately
# small (three plain HTML input shapes), not an open-ended schema.
FORM_FIELD_TYPES = ("text", "email", "textarea")


class WebsiteFormSubmission(BaseEntity):
    """A visitor's submission of a `form` section on a published page.
    `section_id` is the section's own `id` within WebsitePage.sections —
    not a FK, since sections aren't a separate table (see WebsitePage.
    sections docstring) — so a submission stays attributable even if the
    owner later edits that section's fields. `spam_score` is a 0-1
    heuristic (apps.websites.forms.score_submission) computed at
    submit-time and never re-evaluated; nothing here is ever auto-deleted
    purely from an authorial signal alone, since removing a page/website
    should still cascade correctly for privacy/retention purposes."""

    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="form_submissions")
    page = models.ForeignKey(WebsitePage, on_delete=models.CASCADE, related_name="form_submissions")
    section_id = models.CharField(max_length=64)
    data = models.JSONField(default=dict, blank=True)
    spam_score = models.FloatField(default=0.0)

    class Meta:
        db_table = "website_form_submission"
        indexes = [
            models.Index(fields=["website", "created_at"]),
            models.Index(fields=["page", "section_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Submission on {self.page_id}/{self.section_id} at {self.created_at:%Y-%m-%d %H:%M}"
