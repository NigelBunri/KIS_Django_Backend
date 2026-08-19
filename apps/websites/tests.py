from __future__ import annotations

import uuid
from unittest.mock import patch

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


class EmbedValidationTests(TestCase):
    def test_valid_youtube_embed_passes(self):
        from apps.websites.embeds import validate_embed

        validate_embed({"provider": "youtube", "url": "https://www.youtube.com/embed/dQw4w9WgXcQ"})

    def test_valid_calendly_embed_passes(self):
        from apps.websites.embeds import validate_embed

        validate_embed({"provider": "calendly", "url": "https://calendly.com/acme/intro-call"})

    def test_unknown_provider_rejected(self):
        from apps.websites.embeds import validate_embed

        with self.assertRaises(ValidationError):
            validate_embed({"provider": "tiktok", "url": "https://tiktok.com/embed/123"})

    def test_url_not_matching_provider_pattern_rejected(self):
        from apps.websites.embeds import validate_embed

        with self.assertRaises(ValidationError):
            validate_embed({"provider": "youtube", "url": "https://evil.example.com/embed/x"})

    def test_youtube_watch_url_rejected_only_embed_url_accepted(self):
        from apps.websites.embeds import validate_embed

        with self.assertRaises(ValidationError):
            validate_embed({"provider": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})


class WebsiteEmbedSectionApiTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110060")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Embed Shop", slug="embed-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]
        self.home_page_id = WebsitePage.objects.get(website_id=self.website_id, is_home=True).id

    def test_valid_embed_section_saves(self):
        response = self.client.patch(
            reverse("websites:page-detail", args=[self.website_id, self.home_page_id]),
            {"sections": [{"id": "e1", "type": "embed", "data": {"provider": "youtube", "url": "https://www.youtube.com/embed/dQw4w9WgXcQ"}}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_invalid_embed_section_rejected(self):
        response = self.client.patch(
            reverse("websites:page-detail", args=[self.website_id, self.home_page_id]),
            {"sections": [{"id": "e1", "type": "embed", "data": {"provider": "not_real", "url": "https://evil.example.com"}}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class WebsiteWebhookApiTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110061")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Webhook Shop", slug="webhook-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]

    def test_owner_can_create_webhook_and_secret_is_returned_once(self):
        response = self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "published", "target_url": "https://example.com/hooks/kis"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["secret"])

        list_response = self.client.get(reverse("websites:webhook-list-create", args=[self.website_id]))
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertNotIn("secret", list_response.data[0])

    def test_non_https_target_url_rejected(self):
        response = self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "published", "target_url": "http://example.com/hooks/kis"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_event_type_rejected(self):
        response = self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "not_a_real_event", "target_url": "https://example.com/hooks"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_delete_webhook(self):
        create_response = self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "published", "target_url": "https://example.com/hooks"}, format="json",
        )
        webhook_id = create_response.data["id"]
        delete_response = self.client.delete(reverse("websites:webhook-detail", args=[self.website_id, webhook_id]))
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

    def test_stranger_cannot_create_webhook(self):
        stranger = _make_user("+2348011110062")
        self.client.force_authenticate(stranger)
        response = self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "published", "target_url": "https://example.com/hooks"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class WebsiteWebhookFiringTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110063")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Firing Shop", slug="firing-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]
        self.website_slug = mine_response.data["slug"]
        self.home_page_id = WebsitePage.objects.get(website_id=self.website_id, is_home=True).id

        self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "published", "target_url": "https://example.com/hooks/published"}, format="json",
        )
        self.client.post(
            reverse("websites:webhook-list-create", args=[self.website_id]),
            {"event_type": "form_submitted", "target_url": "https://example.com/hooks/form"}, format="json",
        )

    @patch("apps.websites.webhooks.requests.post")
    def test_publish_fires_matching_webhook_only(self, mock_post):
        self.client.post(reverse("websites:publish", args=[self.website_id]))
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args.args[0], "https://example.com/hooks/published")
        self.assertIn("X-KIS-Signature", mock_post.call_args.kwargs["headers"])

    @patch("apps.websites.webhooks.requests.post")
    def test_form_submission_fires_form_submitted_webhook(self, mock_post):
        self.client.patch(
            reverse("websites:page-detail", args=[self.website_id, self.home_page_id]),
            {"sections": [{
                "id": "f1", "type": "form",
                "data": {"title": "Contact", "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]},
            }]},
            format="json",
        )
        self.client.post(reverse("websites:publish", args=[self.website_id]))
        self.client.post(reverse("websites:page-publish", args=[self.website_id, self.home_page_id]))
        mock_post.reset_mock()
        self.client.logout()

        self.client.post(
            reverse("websites:public-form-submit", args=[self.website_slug, "home", "f1"]),
            {"name": "Jane"}, format="json",
        )
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args.args[0], "https://example.com/hooks/form")


class WebsiteCollaborationApiTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110070")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Collab Shop", slug="collab-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]

    def _create_invite(self, role="editor", max_uses=None):
        payload = {"role": role}
        if max_uses is not None:
            payload["max_uses"] = max_uses
        response = self.client.post(reverse("websites:invite-list-create", args=[self.website_id]), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data["code"]

    def test_owner_can_create_and_list_invites(self):
        self._create_invite()
        response = self.client.get(reverse("websites:invite-list-create", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_stranger_cannot_create_invite(self):
        stranger = _make_user("+2348011110071")
        self.client.force_authenticate(stranger)
        response = self.client.post(reverse("websites:invite-list-create", args=[self.website_id]), {"role": "editor"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_editor_can_redeem_invite_and_gains_access(self):
        code = self._create_invite(role="editor")
        editor = _make_user("+2348011110072")
        self.client.force_authenticate(editor)

        redeem_response = self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")
        self.assertEqual(redeem_response.status_code, status.HTTP_200_OK, redeem_response.data)
        self.assertEqual(redeem_response.data["role"], "editor")

        patch_response = self.client.patch(
            reverse("websites:detail", args=[self.website_id]), {"name": "Renamed by editor"}, format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)

    def test_editor_cannot_administer_collaborators_or_invites(self):
        code = self._create_invite(role="editor")
        editor = _make_user("+2348011110073")
        self.client.force_authenticate(editor)
        self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")

        list_response = self.client.get(reverse("websites:collaborator-list", args=[self.website_id]))
        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)

        create_invite_response = self.client.post(reverse("websites:invite-list-create", args=[self.website_id]), {"role": "editor"}, format="json")
        self.assertEqual(create_invite_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_code_returns_404(self):
        stranger = _make_user("+2348011110074")
        self.client.force_authenticate(stranger)
        response = self.client.post(reverse("websites:redeem-invite"), {"code": "NOTAREALCODE"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_max_uses_enforced(self):
        code = self._create_invite(role="editor", max_uses=1)
        first_user = _make_user("+2348011110075")
        self.client.force_authenticate(first_user)
        self.assertEqual(self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json").status_code, status.HTTP_200_OK)

        second_user = _make_user("+2348011110076")
        self.client.force_authenticate(second_user)
        response = self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoked_invite_cannot_be_redeemed(self):
        create_response = self.client.post(reverse("websites:invite-list-create", args=[self.website_id]), {"role": "editor"}, format="json")
        invite_id = create_response.data["id"]
        code = create_response.data["code"]
        revoke_response = self.client.post(reverse("websites:invite-revoke", args=[self.website_id, invite_id]))
        self.assertEqual(revoke_response.status_code, status.HTTP_204_NO_CONTENT)

        stranger = _make_user("+2348011110077")
        self.client.force_authenticate(stranger)
        response = self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_remove_collaborator_and_access_is_revoked(self):
        code = self._create_invite(role="editor")
        editor = _make_user("+2348011110078")
        self.client.force_authenticate(editor)
        self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")

        self.client.force_authenticate(self.owner)
        list_response = self.client.get(reverse("websites:collaborator-list", args=[self.website_id]))
        collaborator_id = list_response.data[0]["id"]
        delete_response = self.client.delete(reverse("websites:collaborator-detail", args=[self.website_id, collaborator_id]))
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

        self.client.force_authenticate(editor)
        patch_response = self.client.patch(reverse("websites:detail", args=[self.website_id]), {"name": "Should fail"}, format="json")
        self.assertEqual(patch_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_seat_quota_blocks_redemption_past_business_tier_limit(self):
        # Business tier's team_seats is 3.
        code = self._create_invite(role="editor")
        for i in range(3):
            user = _make_user(f"+234801111008{i}")
            self.client.force_authenticate(user)
            response = self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        fourth_user = _make_user("+2348011110090")
        self.client.force_authenticate(fourth_user)
        response = self.client.post(reverse("websites:redeem-invite"), {"code": code}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class WebsiteAnalyticsTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110091")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Analytics Shop", slug="analytics-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]
        self.website_slug = mine_response.data["slug"]
        self.home_page_id = WebsitePage.objects.get(website_id=self.website_id, is_home=True).id

        self.client.post(reverse("websites:publish", args=[self.website_id]))
        self.client.post(reverse("websites:page-publish", args=[self.website_id, self.home_page_id]))
        self.client.logout()

    def test_beacon_records_a_view_for_a_published_site(self):
        from apps.websites.models import WebsiteAnalyticsEvent

        response = self.client.post(
            reverse("websites:public-analytics-beacon"),
            {"site_slug": self.website_slug, "page_slug": "home", "referrer": "https://google.com/search"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        event = WebsiteAnalyticsEvent.objects.get()
        self.assertEqual(event.website_id, uuid.UUID(self.website_id))
        self.assertEqual(event.referrer_host, "google.com")
        self.assertTrue(event.session_hash)

    def test_beacon_never_stores_a_raw_ip_field(self):
        from apps.websites.models import WebsiteAnalyticsEvent

        field_names = {f.name for f in WebsiteAnalyticsEvent._meta.get_fields()}
        self.assertNotIn("ip_address", field_names)
        self.assertNotIn("ip", field_names)

    def test_beacon_is_silent_no_op_for_unknown_site(self):
        from apps.websites.models import WebsiteAnalyticsEvent

        response = self.client.post(
            reverse("websites:public-analytics-beacon"), {"site_slug": "does-not-exist", "page_slug": "home"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(WebsiteAnalyticsEvent.objects.count(), 0)

    def test_session_hash_same_day_same_visitor_dedupes(self):
        from apps.websites.analytics import hash_visitor_session

        first = hash_visitor_session("203.0.113.5", "TestAgent/1.0")
        second = hash_visitor_session("203.0.113.5", "TestAgent/1.0")
        self.assertEqual(first, second)

    def test_session_hash_different_visitors_differ(self):
        from apps.websites.analytics import hash_visitor_session

        first = hash_visitor_session("203.0.113.5", "TestAgent/1.0")
        second = hash_visitor_session("203.0.113.9", "TestAgent/1.0")
        self.assertNotEqual(first, second)

    def test_owner_can_view_analytics_summary(self):
        for _ in range(3):
            self.client.post(
                reverse("websites:public-analytics-beacon"), {"site_slug": self.website_slug, "page_slug": "home"}, format="json",
            )
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("websites:analytics-summary", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_views"], 3)
        self.assertEqual(len(response.data["top_pages"]), 1)
        self.assertEqual(response.data["top_pages"][0]["count"], 3)

    def test_stranger_cannot_view_analytics_summary(self):
        stranger = _make_user("+2348011110092")
        self.client.force_authenticate(stranger)
        response = self.client.get(reverse("websites:analytics-summary", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CustomDomainValidationTests(TestCase):
    def test_valid_domain_accepted(self):
        from apps.websites.custom_domains import validate_domain_format

        self.assertEqual(validate_domain_format("www.example.com"), "www.example.com")

    def test_invalid_domain_rejected(self):
        from apps.websites.custom_domains import validate_domain_format

        with self.assertRaises(ValidationError):
            validate_domain_format("not a domain")

    def test_kingdomimpactventures_subdomain_rejected(self):
        from apps.websites.custom_domains import validate_domain_format

        with self.assertRaises(ValidationError):
            validate_domain_format("evil.kingdomimpactventures.org")


class WebsiteCustomDomainApiTests(APITestCase):
    def setUp(self):
        self.owner = _make_user("+2348011110093")
        _give_tier(self.owner, "Business")
        self.client.force_authenticate(self.owner)
        from apps.commerce.models import Shop

        self.shop = Shop.objects.create(owner=self.owner, name="Domain Shop", slug="domain-test-shop")
        mine_response = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(self.shop.id)})
        self.website_id = mine_response.data["id"]
        self.website_slug = mine_response.data["slug"]

    def test_get_reports_not_enabled_by_default(self):
        response = self.client.get(reverse("websites:custom-domain", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["enabled"])
        self.assertIsNone(response.data["custom_domain"])

    def test_post_rejected_when_not_configured(self):
        response = self.client.post(reverse("websites:custom-domain", args=[self.website_id]), {"custom_domain": "www.example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stranger_cannot_manage_custom_domain(self):
        stranger = _make_user("+2348011110094")
        self.client.force_authenticate(stranger)
        response = self.client.get(reverse("websites:custom-domain", args=[self.website_id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("apps.websites.views.custom_domains_enabled", return_value=True)
    @patch("apps.websites.views.register_custom_hostname")
    def test_post_registers_domain_when_configured(self, mock_register, _mock_enabled):
        mock_register.return_value = {
            "cloudflare_id": "cf-123",
            "cname_target": "kingdomimpactventures.org",
            "txt_record": {"name": "_cf-custom-hostname.www.example.com", "value": "abc123"},
        }
        response = self.client.post(reverse("websites:custom-domain", args=[self.website_id]), {"custom_domain": "www.example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["cname_target"], "kingdomimpactventures.org")
        self.assertTrue(response.data["txt_record"]["value"])

        from apps.websites.models import Website

        website = Website.objects.get(id=self.website_id)
        self.assertEqual(website.custom_domain, "www.example.com")
        self.assertEqual(website.custom_domain_cloudflare_id, "cf-123")

    @patch("apps.websites.views.custom_domains_enabled", return_value=True)
    @patch("apps.websites.views.register_custom_hostname")
    def test_duplicate_domain_rejected(self, mock_register, _mock_enabled):
        mock_register.return_value = {"cloudflare_id": "cf-1", "cname_target": "x", "txt_record": {"name": "n", "value": "v"}}
        self.client.post(reverse("websites:custom-domain", args=[self.website_id]), {"custom_domain": "www.example.com"}, format="json")

        from apps.commerce.models import Shop

        other_owner = _make_user("+2348011110095")
        _give_tier(other_owner, "Business")
        self.client.force_authenticate(other_owner)
        other_shop = Shop.objects.create(owner=other_owner, name="Other Shop", slug="other-domain-shop")
        other_mine = self.client.get(reverse("websites:mine"), {"owner_type": "shop", "owner_id": str(other_shop.id)})
        response = self.client.post(
            reverse("websites:custom-domain", args=[other_mine.data["id"]]), {"custom_domain": "www.example.com"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_resolve_by_domain_requires_active_status(self):
        from apps.websites.models import Website, WebsiteCustomDomainStatus

        website = Website.objects.get(id=self.website_id)
        website.custom_domain = "www.example.com"
        website.custom_domain_status = WebsiteCustomDomainStatus.PENDING
        website.status = WebsiteStatus.PUBLISHED
        website.save()

        self.client.logout()
        pending_response = self.client.get(reverse("websites:public-site-by-domain", args=["www.example.com"]))
        self.assertEqual(pending_response.status_code, status.HTTP_404_NOT_FOUND)

        website.custom_domain_status = WebsiteCustomDomainStatus.ACTIVE
        website.save()
        active_response = self.client.get(reverse("websites:public-site-by-domain", args=["www.example.com"]))
        self.assertEqual(active_response.status_code, status.HTTP_200_OK)
        self.assertEqual(active_response.data["slug"], self.website_slug)
