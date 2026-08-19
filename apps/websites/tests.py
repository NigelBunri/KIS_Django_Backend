from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APITestCase

from apps.accounts.models import AccountTier, Subscription
from apps.accounts.tiers import ensure_default_account_tiers
from apps.websites import adapters, owner_resolution
from apps.websites.kis_content_resolvers import resolve_kis_content_section
from apps.websites.models import Website, WebsiteFormSubmission, WebsiteOwnerType, WebsitePage, WebsiteStatus
from apps.websites.permissions import check_websites_quota
from apps.websites.preview_tokens import sign_website_preview_token, verify_website_preview_token
from apps.websites.serializers import WebsitePageSerializer

User = get_user_model()


def _make_user(phone):
    return User.objects.create_user(phone=phone, country="NG", password="pass1234")


def _give_tier(user, tier_name):
    ensure_default_account_tiers()
    tier = AccountTier.objects.filter(name__iexact=tier_name).first()
    if tier:
        Subscription.objects.create(user=user, tier=tier, status="active")
    return tier


class WebsiteModelConstraintTests(TestCase):
    def setUp(self):
        self.owner_id = uuid.uuid4()
        self.website = Website.objects.create(
            owner_type=WebsiteOwnerType.SHOP, owner_id=self.owner_id, slug="acme-shop", name="Acme",
        )

    def test_owner_type_and_owner_id_pair_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Website.objects.create(owner_type=WebsiteOwnerType.SHOP, owner_id=self.owner_id, slug="acme-shop-2")

    def test_only_one_home_page_per_website(self):
        WebsitePage.objects.create(website=self.website, slug="", title="Home", is_home=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WebsitePage.objects.create(website=self.website, slug="home-2", title="Home Again", is_home=True)

    def test_page_slug_unique_per_website(self):
        WebsitePage.objects.create(website=self.website, slug="about", title="About")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WebsitePage.objects.create(website=self.website, slug="about", title="About Again")


class OwnerResolutionTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110001")

    def test_resolves_shop_owner(self):
        from apps.commerce.models import Shop

        shop = Shop.objects.create(owner=self.owner, name="Acme", slug="acme-owner-res")
        resolved = owner_resolution.resolve_owner_object(WebsiteOwnerType.SHOP, shop.id)
        self.assertEqual(resolved.id, shop.id)
        self.assertEqual(owner_resolution.resolve_owner_user(WebsiteOwnerType.SHOP, resolved), self.owner)

    def test_resolves_broadcast_channel_owner_via_owner_user(self):
        from apps.broadcasts.models import BroadcastChannel

        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="owner-res-channel", display_name="Channel",
        )
        resolved = owner_resolution.resolve_owner_object(WebsiteOwnerType.BROADCAST_CHANNEL, channel.id)
        self.assertEqual(owner_resolution.resolve_owner_user(WebsiteOwnerType.BROADCAST_CHANNEL, resolved), self.owner)

    def test_unknown_owner_returns_none(self):
        self.assertIsNone(owner_resolution.resolve_owner_object(WebsiteOwnerType.SHOP, uuid.uuid4()))

    def test_user_can_manage_website_is_owner_only(self):
        from apps.commerce.models import Shop

        stranger = _make_user("+2348011110002")
        shop = Shop.objects.create(owner=self.owner, name="Acme2", slug="acme-owner-res-2")
        self.assertTrue(owner_resolution.user_can_manage_website(self.owner, WebsiteOwnerType.SHOP, shop.id))
        self.assertFalse(owner_resolution.user_can_manage_website(stranger, WebsiteOwnerType.SHOP, shop.id))


class LazyAdapterTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110010")

    def test_shop_adapter_seeds_hero_from_legacy_landing_page_and_leaves_it_untouched(self):
        from apps.commerce.models import Shop, ShopLandingPage

        shop = Shop.objects.create(owner=self.owner, name="Acme Shop", slug="acme-adapter-shop")
        legacy = ShopLandingPage.objects.create(
            shop=shop, headline="Welcome to Acme", subheadline="Quality goods", is_published=True,
        )

        website = adapters.get_or_seed_website(WebsiteOwnerType.SHOP, shop.id, created_by=self.owner)

        self.assertIsNotNone(website)
        self.assertTrue(website.seeded_from_legacy)
        self.assertEqual(website.status, WebsiteStatus.PUBLISHED)
        home = website.pages.get(is_home=True)
        hero_sections = [s for s in home.sections if s["type"] == "hero"]
        self.assertEqual(len(hero_sections), 1)
        self.assertEqual(hero_sections[0]["data"]["headline"], "Welcome to Acme")

        # Legacy row itself must be completely untouched.
        legacy.refresh_from_db()
        self.assertEqual(legacy.headline, "Welcome to Acme")
        self.assertTrue(ShopLandingPage.objects.filter(pk=legacy.pk).exists())

    def test_shop_adapter_with_no_legacy_landing_page_creates_blank_website(self):
        from apps.commerce.models import Shop

        shop = Shop.objects.create(owner=self.owner, name="Bare Shop", slug="bare-adapter-shop")
        website = adapters.get_or_seed_website(WebsiteOwnerType.SHOP, shop.id, created_by=self.owner)
        self.assertIsNotNone(website)
        self.assertEqual(website.status, WebsiteStatus.DRAFT)
        self.assertTrue(website.pages.filter(is_home=True).exists())

    def test_adapter_is_idempotent_second_call_returns_same_website(self):
        from apps.commerce.models import Shop

        shop = Shop.objects.create(owner=self.owner, name="Idem Shop", slug="idem-adapter-shop")
        first = adapters.get_or_seed_website(WebsiteOwnerType.SHOP, shop.id, created_by=self.owner)
        second = adapters.get_or_seed_website(WebsiteOwnerType.SHOP, shop.id, created_by=self.owner)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Website.objects.filter(owner_type=WebsiteOwnerType.SHOP, owner_id=shop.id).count(), 1)

    def _make_broadcast_health_profile(self):
        from apps.accounts.models import Profile
        from apps.broadcasts.models import BroadcastHealthProfile

        profile, _ = Profile.objects.get_or_create(user=self.owner)
        broadcast_profile, _ = BroadcastHealthProfile.objects.get_or_create(profile=profile)
        return broadcast_profile

    def test_health_adapter_skips_on_ambiguous_owner_user_match(self):
        from apps.broadcasts.models import BroadcastHealthInstitution
        from apps.health_dashboard.models import HealthDashboardInstitution
        from apps.health_ops.models import HealthInstitution

        institution = HealthInstitution.objects.create(owner=self.owner, name="Wellness Clinic", slug="wellness-clinic-ambiguous")

        broadcast_profile = self._make_broadcast_health_profile()
        for i in range(2):
            broadcast_institution = BroadcastHealthInstitution.objects.create(
                health_profile=broadcast_profile, institution_uid=f"ambiguous-bhi-{self.owner.id}-{i}",
                name=f"Dashboard Institution {i}",
            )
            HealthDashboardInstitution.objects.create(
                broadcast_institution=broadcast_institution,
                institution_uid=f"ambiguous-{self.owner.id}-{i}",
                owner_user=self.owner,
                name=f"Dashboard Institution {i}",
            )

        website = adapters.get_or_seed_website(WebsiteOwnerType.HEALTH_INSTITUTION, institution.id, created_by=self.owner)

        self.assertIsNotNone(website)
        self.assertFalse(website.pages.get(is_home=True).sections)

    def test_health_adapter_seeds_from_single_unambiguous_match(self):
        from apps.broadcasts.models import BroadcastHealthInstitution
        from apps.health_dashboard.models import HealthDashboardInstitution, HealthDashboardInstitutionLandingPage
        from apps.health_ops.models import HealthInstitution

        institution = HealthInstitution.objects.create(owner=self.owner, name="Solo Clinic", slug="solo-clinic-unambiguous")
        broadcast_profile = self._make_broadcast_health_profile()
        broadcast_institution = BroadcastHealthInstitution.objects.create(
            health_profile=broadcast_profile, institution_uid=f"solo-bhi-{self.owner.id}", name="Solo Dashboard Institution",
        )
        dashboard = HealthDashboardInstitution.objects.create(
            broadcast_institution=broadcast_institution, institution_uid=f"solo-{self.owner.id}", owner_user=self.owner,
            name="Solo Dashboard Institution",
        )
        HealthDashboardInstitutionLandingPage.objects.create(
            dashboard=dashboard, hero_headline="Solo Clinic Welcomes You", is_published=True,
        )

        website = adapters.get_or_seed_website(WebsiteOwnerType.HEALTH_INSTITUTION, institution.id, created_by=self.owner)

        self.assertEqual(website.status, WebsiteStatus.PUBLISHED)
        hero = [s for s in website.pages.get(is_home=True).sections if s["type"] == "hero"]
        self.assertEqual(hero[0]["data"]["headline"], "Solo Clinic Welcomes You")

    def test_broadcast_channel_adapter_has_no_legacy_data_and_creates_blank_website(self):
        from apps.broadcasts.models import BroadcastChannel

        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="adapter-test-channel", display_name="Adapter Test Channel",
        )
        website = adapters.get_or_seed_website(WebsiteOwnerType.BROADCAST_CHANNEL, channel.id, created_by=self.owner)
        self.assertIsNotNone(website)
        self.assertEqual(website.pages.get(is_home=True).sections, [])


class KisContentResolverTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110020")

    def test_resolve_products_excludes_inactive_products(self):
        from apps.commerce.models import Product, Shop

        shop = Shop.objects.create(owner=self.owner, name="Resolver Shop", slug="resolver-shop")
        active = Product.objects.create(shop=shop, sku="SKU-A", name="Active Product", slug="active-product", price=10)
        Product.objects.create(shop=shop, sku="SKU-B", name="Inactive Product", slug="inactive-product", price=10, is_active=False)

        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.SHOP, owner_id=shop.id,
            section_data={"target_type": "product", "presentation": {"limit": 10}},
        )

        ids = {item["id"] for item in items}
        self.assertIn(str(active.id), ids)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["shop_id"], str(shop.id), "checkout needs shop_id on every resolved product item")

    def test_resolve_courses_exposes_checkout_content_id_for_the_primary_priced_broadcast(self):
        from apps.broadcasts.models import EducationInstitution, EducationInstitutionBroadcast, EducationInstitutionCourse

        institution = EducationInstitution.objects.create(owner=self.owner, name="Resolver Institution")
        course = EducationInstitutionCourse.objects.create(
            institution=institution, title="Resolver Course", status="published", visibility="public",
        )
        # A draft broadcast must be ignored — only published ones are checkout-eligible.
        EducationInstitutionBroadcast.objects.create(
            institution=institution, created_by=self.owner, broadcast_kind="lesson", course=course,
            title="Draft Session", status="draft", price_amount=10,
        )
        priced = EducationInstitutionBroadcast.objects.create(
            institution=institution, created_by=self.owner, broadcast_kind="lesson", course=course,
            title="Priced Session", status="published", price_amount=25,
        )

        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.EDUCATION_INSTITUTION, owner_id=institution.id,
            section_data={"target_type": "course", "presentation": {"limit": 10}},
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["checkout_content_id"], str(priced.id))

    def test_resolve_courses_leaves_checkout_content_id_null_with_no_broadcasts(self):
        from apps.broadcasts.models import EducationInstitution, EducationInstitutionCourse

        institution = EducationInstitution.objects.create(owner=self.owner, name="Bare Resolver Institution")
        EducationInstitutionCourse.objects.create(
            institution=institution, title="Bare Course", status="published", visibility="public",
        )

        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.EDUCATION_INSTITUTION, owner_id=institution.id,
            section_data={"target_type": "course", "presentation": {"limit": 10}},
        )

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["checkout_content_id"])

    def test_resolve_products_never_returns_another_shops_products(self):
        from apps.commerce.models import Product, Shop

        shop_a = Shop.objects.create(owner=self.owner, name="Shop A", slug="resolver-shop-a")
        other_owner = _make_user("+2348011110021")
        shop_b = Shop.objects.create(owner=other_owner, name="Shop B", slug="resolver-shop-b")
        Product.objects.create(shop=shop_b, sku="SKU-C", name="Other Shop Product", slug="other-shop-product", price=5)

        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.SHOP, owner_id=shop_a.id,
            section_data={"target_type": "product", "presentation": {"limit": 10}},
        )
        self.assertEqual(items, [])

    def test_resolve_posts_excludes_private_content(self):
        from apps.broadcasts.models import BroadcastChannel, ChannelContent

        channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="resolver-posts-channel", display_name="Resolver Posts Channel", is_public=True,
        )
        public_post = ChannelContent.objects.create(
            channel=channel, content_type="text", title="Public Post",
            visibility=ChannelContent.Visibility.PUBLIC, status=ChannelContent.Status.PUBLISHED,
        )
        private_post = ChannelContent.objects.create(
            channel=channel, content_type="text", title="Private Post",
            visibility=ChannelContent.Visibility.PRIVATE, status=ChannelContent.Status.PUBLISHED,
        )

        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.BROADCAST_CHANNEL, owner_id=channel.id,
            section_data={"target_type": "post", "target_ids": [str(public_post.id), str(private_post.id)]},
        )
        ids = {item["id"] for item in items}
        self.assertIn(str(public_post.id), ids)
        self.assertNotIn(str(private_post.id), ids)

    def test_unknown_target_type_returns_empty_list_not_an_error(self):
        items = resolve_kis_content_section(
            owner_type=WebsiteOwnerType.SHOP, owner_id=uuid.uuid4(), section_data={"target_type": "not_a_real_type"},
        )
        self.assertEqual(items, [])


class TierGateTests(TestCase):
    def setUp(self):
        self.free_user = _make_user("+2348011110030")
        self.business_user = _make_user("+2348011110031")
        _give_tier(self.business_user, "Business")

    def test_free_tier_user_cannot_create_a_website_at_all(self):
        with self.assertRaises(PermissionDenied):
            check_websites_quota(self.free_user)

    def test_business_tier_user_blocked_after_reaching_website_limit(self):
        # Business tier's websites_limit is 1.
        Website.objects.create(
            owner_type=WebsiteOwnerType.SHOP, owner_id=uuid.uuid4(), slug="quota-test-site",
            created_by=self.business_user,
        )
        with self.assertRaises(ValidationError):
            check_websites_quota(self.business_user)


class PreviewTokenTests(TestCase):
    def test_valid_token_round_trips(self):
        website_id = uuid.uuid4()
        token = sign_website_preview_token(website_id, uuid.uuid4())
        self.assertTrue(verify_website_preview_token(token, website_id))

    def test_token_rejected_for_a_different_website(self):
        token = sign_website_preview_token(uuid.uuid4(), uuid.uuid4())
        self.assertFalse(verify_website_preview_token(token, uuid.uuid4()))

    def test_missing_or_garbage_token_rejected(self):
        self.assertFalse(verify_website_preview_token(None, uuid.uuid4()))
        self.assertFalse(verify_website_preview_token("not-a-real-token", uuid.uuid4()))


class WebsiteApiTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110040")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)

        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="API Shop", slug="api-test-shop")

    def test_mine_endpoint_creates_and_is_idempotent(self):
        url = reverse("websites:mine")
        first = self.client.get(url, {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        website_id = first.data["id"]

        second = self.client.get(url, {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["id"], website_id)

    def test_stranger_cannot_open_someone_elses_website_builder(self):
        stranger = _make_user("+2348011110041")
        self.client.force_authenticate(stranger)
        url = reverse("websites:mine")
        response = self.client.get(url, {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_publish_then_public_page_is_reachable_unauthenticated(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]

        publish_response = self.client.post(reverse("websites:publish", args=[website_id]))
        self.assertEqual(publish_response.status_code, status.HTTP_200_OK, publish_response.data)

        home_page = WebsitePage.objects.get(website_id=website_id, is_home=True)
        page_publish_response = self.client.post(reverse("websites:page-publish", args=[website_id, home_page.id]))
        self.assertEqual(page_publish_response.status_code, status.HTTP_200_OK)

        self.client.logout()
        website_slug = mine_response.data["slug"]
        public_response = self.client.get(reverse("websites:public-page", args=[website_slug, "home"]))
        self.assertEqual(public_response.status_code, status.HTTP_200_OK, public_response.data)

    def test_unpublished_page_404s_for_anonymous_visitor(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_slug = mine_response.data["slug"]
        self.client.logout()
        response = self.client.get(reverse("websites:public-page", args=[website_slug, "home"]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_preview_token_bypasses_unpublished_gate(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        website_slug = mine_response.data["slug"]

        token_response = self.client.post(reverse("websites:preview-token", args=[website_id]))
        self.assertEqual(token_response.status_code, status.HTTP_200_OK)
        token = token_response.data["token"]

        self.client.logout()
        site_response = self.client.get(reverse("websites:public-site", args=[website_slug]), {"preview_token": token})
        self.assertEqual(site_response.status_code, status.HTTP_200_OK)

        no_token_response = self.client.get(reverse("websites:public-site", args=[website_slug]))
        self.assertEqual(no_token_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_free_tier_user_blocked_from_creating_a_website(self):
        free_user = _make_user("+2348011110042")
        from apps.commerce.models import Shop

        free_shop = Shop.objects.create(owner=free_user, name="Free Shop", slug="free-tier-shop")
        self.client.force_authenticate(free_user)
        response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(free_shop.id)})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_page_creation_respects_pages_quota(self):
        # Business tier's website_pages_limit is 5; Home already counts as 1.
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        for i in range(4):
            response = self.client.post(reverse("websites:page-list-create", args=[website_id]), {
                "title": f"Page {i}", "slug": f"page-{i}",
            }, format="json")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        over_quota_response = self.client.post(reverse("websites:page-list-create", args=[website_id]), {
            "title": "One Too Many", "slug": "one-too-many",
        }, format="json")
        self.assertEqual(over_quota_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_home_page_cannot_be_deleted(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        home_page = WebsitePage.objects.get(website_id=website_id, is_home=True)
        response = self.client.delete(reverse("websites:page-detail", args=[website_id, home_page.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(WebsitePage.objects.filter(pk=home_page.id).exists())

    def test_sitemap_plan_only_lists_published_content(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        website_slug = mine_response.data["slug"]

        self.client.logout()
        before = self.client.get(reverse("websites:public-sitemap-plan"))
        self.assertNotIn(website_slug, [s["slug"] for s in before.data["sites"]])

        self.client.force_authenticate(self.owner)
        self.client.post(reverse("websites:publish", args=[website_id]))
        self.client.logout()

        after = self.client.get(reverse("websites:public-sitemap-plan"))
        self.assertIn(website_slug, [s["slug"] for s in after.data["sites"]])

    def test_patch_accepts_valid_branding(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        response = self.client.patch(reverse("websites:detail", args=[website_id]), {
            "branding": {
                "palette": {"primary": "#1a1a2e", "secondary": "#fff", "background": "#ffffff", "text": "#000"},
                "typography": {"preset": "serif"},
                "buttons": {"shape": "pill", "fill": "outline"},
            },
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["branding"]["typography"]["preset"], "serif")

    def test_patch_rejects_invalid_hex_color(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        response = self.client.patch(reverse("websites:detail", args=[website_id]), {
            "branding": {"palette": {"primary": "not-a-color"}},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_rejects_invalid_typography_preset(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        response = self.client.patch(reverse("websites:detail", args=[website_id]), {
            "branding": {"typography": {"preset": "comic-sans"}},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_rejects_invalid_button_shape(self):
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        website_id = mine_response.data["id"]
        response = self.client.patch(reverse("websites:detail", args=[website_id]), {
            "branding": {"buttons": {"shape": "hexagon"}},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BrandingValidationTests(TestCase):
    def test_valid_full_payload_passes(self):
        from apps.websites.branding import validate_branding

        validate_branding({
            "palette": {"primary": "#123", "secondary": "#123456", "background": "#fff", "text": "#000"},
            "typography": {"preset": "sans"},
            "buttons": {"shape": "rounded", "fill": "solid"},
        })

    def test_empty_payload_passes(self):
        from apps.websites.branding import validate_branding

        validate_branding({})

    def test_invalid_hex_color_raises(self):
        from apps.websites.branding import validate_branding

        with self.assertRaises(ValidationError):
            validate_branding({"palette": {"primary": "blue"}})

    def test_invalid_typography_preset_raises(self):
        from apps.websites.branding import validate_branding

        with self.assertRaises(ValidationError):
            validate_branding({"typography": {"preset": "comic-sans"}})

    def test_invalid_button_shape_raises(self):
        from apps.websites.branding import validate_branding

        with self.assertRaises(ValidationError):
            validate_branding({"buttons": {"shape": "hexagon"}})

    def test_invalid_button_fill_raises(self):
        from apps.websites.branding import validate_branding

        with self.assertRaises(ValidationError):
            validate_branding({"buttons": {"fill": "gradient"}})


class RnSectionVocabularyTests(TestCase):
    """The RN Website Builder editor (KIS/src/components/section-builder)
    writes section types from its own legacy-landing-page vocabulary
    (hero_banner, about, image_gallery_grid, statistics,
    programs_services, call_to_action, contact_information) rather than
    this app's own (hero, text, gallery, cta, contact_info). Regression
    coverage for the bug where these were silently rejected on every
    save from the actual production editor."""

    def test_every_rn_editor_section_type_is_accepted(self):
        rn_types = [
            "hero_banner", "about", "image_gallery_grid", "statistics",
            "programs_services", "call_to_action", "contact_information",
        ]
        serializer = WebsitePageSerializer()
        for section_type in rn_types:
            serializer.validate_sections([{"id": "x", "type": section_type, "data": {}}])

    def test_unknown_type_is_still_rejected(self):
        serializer = WebsitePageSerializer()
        with self.assertRaises(ValidationError):
            serializer.validate_sections([{"id": "x", "type": "not_a_real_type", "data": {}}])


class WebsiteFormSubmissionApiTests(APITestCase):
    FORM_SECTION = {
        "id": "form-1",
        "type": "form",
        "data": {
            "title": "Contact Us",
            "fields": [
                {"key": "name", "label": "Name", "type": "text", "required": True},
                {"key": "email", "label": "Email", "type": "email", "required": True},
                {"key": "message", "label": "Message", "type": "textarea", "required": False},
            ],
        },
    }

    def setUp(self):
        self.owner = _make_user("+2348011110050")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)

        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Form Shop", slug="form-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]
        self.website_slug = mine_response.data["slug"]
        self.home_page_id = WebsitePage.objects.get(website_id=self.website_id, is_home=True).id

        patch_response = self.client.patch(
            reverse("websites:page-detail", args=[self.website_id, self.home_page_id]),
            {"sections": [self.FORM_SECTION]}, format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)

        self.client.post(reverse("websites:publish", args=[self.website_id]))
        self.client.post(reverse("websites:page-publish", args=[self.website_id, self.home_page_id]))
        self.client.logout()

    def _submit_url(self):
        return reverse("websites:public-form-submit", args=[self.website_slug, "home", "form-1"])

    def test_valid_submission_is_stored(self):
        response = self.client.post(self._submit_url(), {"name": "Jane", "email": "jane@example.com", "message": "Hi"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        submission = WebsiteFormSubmission.objects.get()
        self.assertEqual(submission.data["name"], "Jane")
        self.assertEqual(submission.data["email"], "jane@example.com")
        self.assertLess(submission.spam_score, 0.5)

    def test_missing_required_field_is_rejected(self):
        response = self.client.post(self._submit_url(), {"name": "Jane"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebsiteFormSubmission.objects.count(), 0)

    def test_unknown_fields_are_dropped_not_stored(self):
        response = self.client.post(
            self._submit_url(),
            {"name": "Jane", "email": "jane@example.com", "admin": True, "extra_field": "sneaky"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = WebsiteFormSubmission.objects.get()
        self.assertNotIn("admin", submission.data)
        self.assertNotIn("extra_field", submission.data)

    def test_honeypot_is_silently_accepted_but_never_stored(self):
        response = self.client.post(
            self._submit_url(),
            {"name": "Bot", "email": "bot@example.com", "_hp": "filled"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(WebsiteFormSubmission.objects.count(), 0)

    def test_fast_submission_is_scored_but_still_stored(self):
        response = self.client.post(
            self._submit_url(),
            {"name": "Jane", "email": "jane@example.com", "_elapsed_ms": 200},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = WebsiteFormSubmission.objects.get()
        self.assertGreaterEqual(submission.spam_score, 0.4)

    def test_unpublished_page_rejects_submissions(self):
        self.client.force_authenticate(self.owner)
        self.client.post(reverse("websites:page-unpublish", args=[self.website_id, self.home_page_id]))
        self.client.logout()
        response = self.client.post(self._submit_url(), {"name": "Jane", "email": "jane@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_list_responses(self):
        self.client.post(self._submit_url(), {"name": "Jane", "email": "jane@example.com"}, format="json")
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("websites:form-responses", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["data"]["name"], "Jane")

    def test_stranger_cannot_list_responses(self):
        self.client.post(self._submit_url(), {"name": "Jane", "email": "jane@example.com"}, format="json")
        stranger = _make_user("+2348011110051")
        self.client.force_authenticate(stranger)
        response = self.client.get(reverse("websites:form-responses", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
