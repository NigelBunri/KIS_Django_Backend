"""
Profile.work_status - the LinkedIn-style "Open to work" / "Hiring" photo-ring
status. A single tri-state field (none / open_for_work / hiring), distinct
from the pre-existing Profile.open_to_work boolean (which backs the
unrelated partners/recruitment talent-search filter).

Covers:
  - model default
  - exposure through ProfileSerializer / UserSerializer / PublicUserSerializer
  - the existing ProfileViewSet PATCH endpoint actually persisting a change
  - ownership (can't set it via someone else's profile id)

Run:
  python3 manage.py test apps.accounts.test_work_status --keepdb -v 2
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Device, Profile, ProfileFieldVisibility, User, UserContact, WorkStatus
from .serializers import ProfileSerializer, PublicUserSerializer, UserSerializer
from .views import issue_tokens_for_user
from .visibility import get_visible_work_status, get_visible_hiring_job_listing_id

DEVICE_A = "work-status-device-a"
DEVICE_B = "work-status-device-b"


def _make_active_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.verification = {"phone": {"verified": True}}
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


def _auth_client(user: User, device_id: str) -> APIClient:
    tokens = issue_tokens_for_user(user, device_id=device_id)
    Device.objects.get_or_create(
        user=user, device_id=device_id,
        defaults={"platform": "android", "is_parent": True, "token_version": 1, "last_seen_at": timezone.now()},
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=device_id)
    return client


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkStatusModelTests(TestCase):
    def test_defaults_to_none(self):
        user = _make_active_user("+237699820001")
        profile, _ = Profile.objects.get_or_create(user=user)
        self.assertEqual(profile.work_status, WorkStatus.NONE)
        self.assertEqual(profile.work_status, "none")

    def test_can_be_set_to_open_for_work_or_hiring(self):
        user = _make_active_user("+237699820002")
        profile, _ = Profile.objects.get_or_create(user=user)

        profile.work_status = WorkStatus.OPEN_FOR_WORK
        profile.save(update_fields=["work_status"])
        profile.refresh_from_db()
        self.assertEqual(profile.work_status, "open_for_work")

        profile.work_status = WorkStatus.HIRING
        profile.save(update_fields=["work_status"])
        profile.refresh_from_db()
        self.assertEqual(profile.work_status, "hiring")


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkStatusSerializerExposureTests(TestCase):
    def setUp(self):
        self.user = _make_active_user("+237699820003")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.work_status = WorkStatus.HIRING
        self.profile.save(update_fields=["work_status"])

    def test_profile_serializer_exposes_work_status(self):
        data = ProfileSerializer(self.profile).data
        self.assertEqual(data["work_status"], "hiring")

    def test_user_serializer_exposes_nested_work_status(self):
        # Re-fetch rather than reuse self.user: the User.post_save signal
        # that auto-creates a Profile already populated self.user's reverse
        # one-to-one cache (Django caches that automatically on create) with
        # the pre-update Profile instance, which would make `.profile` on
        # the in-memory self.user stale even though the DB row (asserted via
        # ProfileSerializer(self.profile) above) is correctly updated - a
        # fresh query sidesteps that in-memory artifact, matching how a real
        # request looks the user up per-request anyway.
        fresh_user = User.objects.get(pk=self.user.pk)
        data = UserSerializer(fresh_user).data
        self.assertEqual(data["profile"]["work_status"], "hiring")

    def test_public_user_serializer_exposes_nested_work_status(self):
        fresh_user = User.objects.get(pk=self.user.pk)
        data = PublicUserSerializer(fresh_user, context={}).data
        self.assertEqual(data["profile"]["work_status"], "hiring")


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkStatusUpdateEndpointTests(TestCase):
    def setUp(self):
        self.user = _make_active_user("+237699820004")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.client_self = _auth_client(self.user, DEVICE_A)

        self.other_user = _make_active_user("+237699820005")
        self.other_profile, _ = Profile.objects.get_or_create(user=self.other_user)
        self.client_other = _auth_client(self.other_user, DEVICE_B)

    def test_patch_profile_persists_open_for_work(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "open_for_work"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["work_status"], "open_for_work")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_status, "open_for_work")

    def test_patch_profile_persists_hiring(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "hiring"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_status, "hiring")

    def test_patch_profile_back_to_none_clears_it(self):
        self.profile.work_status = WorkStatus.HIRING
        self.profile.save(update_fields=["work_status"])

        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "none"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_status, "none")

    def test_rejects_invalid_choice(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "not_a_real_status"}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_status, "none")

    def test_cannot_set_work_status_on_another_users_profile(self):
        res = self.client_other.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "hiring"}, format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_status, "none")


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkStatusVisibilityEnforcementTests(TestCase):
    """work_status used to be handed to any viewer who could load the
    profile at all ("public by definition" per the old inline comment),
    completely ignoring the owner's own ProfileFieldVisibility rule -
    the exact per-field privacy mechanism avatar/headline/bio already use.
    These lock down the fix: a private/contacts-only status must actually
    be hidden from an unauthorized viewer, not just from the frontend."""

    def setUp(self):
        owner = _make_active_user("+237699820010")
        profile, _ = Profile.objects.get_or_create(user=owner)
        profile.work_status = WorkStatus.OPEN_FOR_WORK
        profile.save(update_fields=["work_status"])
        # User.post_save already auto-creates a Profile via signal, which
        # caches THAT (pre-update) instance on the in-memory `owner` - same
        # artifact WorkStatusSerializerExposureTests.test_user_serializer_
        # exposes_nested_work_status works around above. Re-fetching here
        # sidesteps it, matching how a real request looks users up fresh.
        self.owner = User.objects.get(pk=owner.pk)
        self.profile = profile

        self.stranger = _make_active_user("+237699820011")
        contact = _make_active_user("+237699820012")
        UserContact.objects.create(user=self.owner, contact_user=contact)
        UserContact.objects.create(user=contact, contact_user=self.owner)
        self.contact = User.objects.get(pk=contact.pk)

    def test_private_status_is_hidden_from_a_stranger(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="private")
        self.assertIsNone(get_visible_work_status(self.owner, self.stranger))

    def test_private_status_is_still_visible_to_the_owner(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="private")
        self.assertEqual(get_visible_work_status(self.owner, self.owner), "open_for_work")

    def test_contacts_only_status_hidden_from_a_stranger_visible_to_a_contact(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="contacts")
        self.assertIsNone(get_visible_work_status(self.owner, self.stranger))
        self.assertEqual(get_visible_work_status(self.owner, self.contact), "open_for_work")

    def test_public_status_is_visible_to_anyone(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="public")
        self.assertEqual(get_visible_work_status(self.owner, self.stranger), "open_for_work")
        self.assertEqual(get_visible_work_status(self.owner, None), "open_for_work")

    def test_no_rule_defaults_to_visible(self):
        self.assertEqual(get_visible_work_status(self.owner, self.stranger), "open_for_work")

    def test_none_status_never_renders_regardless_of_visibility(self):
        self.profile.work_status = WorkStatus.NONE
        self.profile.save(update_fields=["work_status"])
        self.assertIsNone(get_visible_work_status(self.owner, self.stranger))
        self.assertIsNone(get_visible_work_status(self.owner, self.owner))

    def test_private_status_is_hidden_via_the_real_profile_serializer(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="private")
        data = ProfileSerializer(self.profile, context={"request": _FakeRequest(self.stranger)}).data
        self.assertIsNone(data["work_status"])

    def test_private_status_is_hidden_via_public_user_serializer(self):
        ProfileFieldVisibility.objects.create(user=self.owner, field_key="work_status", visibility="private")
        fresh_owner = User.objects.get(pk=self.owner.pk)
        data = PublicUserSerializer(fresh_owner, context={"request": _FakeRequest(self.stranger)}).data
        self.assertIsNone(data["profile"]["work_status"])


class _FakeRequest:
    def __init__(self, user):
        self.user = user


@override_settings(SECURE_SSL_REDIRECT=False)
class WorkPreferencesTests(TestCase):
    """Section A of the Open-to-Work spec: job titles/roles, employment
    type, workplace arrangement, preferred location, availability. Stored
    as a free-form JSON blob (Profile.work_preferences), only meaningful -
    and only ever persisted - while work_status == open_for_work."""

    def setUp(self):
        self.user = _make_active_user("+237699820020")
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.client_self = _auth_client(self.user, "work-prefs-device-a")

    def test_patch_sets_work_preferences_alongside_open_for_work(self):
        payload = {
            "work_status": "open_for_work",
            "work_preferences": {
                "roles": ["Backend Engineer"],
                "employment_types": ["full_time", "contract"],
                "workplace": "remote",
                "location": "Douala, Cameroon",
                "availability": "Immediately",
            },
        }
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", payload, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["work_preferences"]["workplace"], "remote")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_preferences["location"], "Douala, Cameroon")

    def test_work_preferences_dropped_when_status_is_hiring(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/",
            {"work_status": "hiring", "work_preferences": {"roles": ["Should not persist"]}},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_preferences, {})

    def test_work_preferences_dropped_when_status_is_none(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/",
            {"work_preferences": {"roles": ["Should not persist either"]}},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_preferences, {})

    def test_existing_preferences_survive_an_unrelated_patch(self):
        self.profile.work_status = WorkStatus.OPEN_FOR_WORK
        self.profile.work_preferences = {"roles": ["Existing"]}
        self.profile.save(update_fields=["work_status", "work_preferences"])

        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"headline": "New headline"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.work_preferences, {"roles": ["Existing"]})

    def test_work_preferences_hidden_from_viewer_who_cannot_see_work_status(self):
        self.profile.work_status = WorkStatus.OPEN_FOR_WORK
        self.profile.work_preferences = {"roles": ["Secret role"]}
        self.profile.save(update_fields=["work_status", "work_preferences"])
        ProfileFieldVisibility.objects.create(user=self.user, field_key="work_status", visibility="private")

        stranger = _make_active_user("+237699820021")
        data = ProfileSerializer(self.profile, context={"request": _FakeRequest(stranger)}).data
        self.assertIsNone(data["work_status"])
        self.assertEqual(data["work_preferences"], {})


@override_settings(SECURE_SSL_REDIRECT=False)
class HiringJobListingTests(TestCase):
    """Section 3/7 of the spec: a Hiring status may optionally link to a
    real apps.commerce.JobListing, and tapping the ring should navigate
    there only when that listing is still an active opportunity - a
    deleted or deactivated listing must read back as "stale" (no
    tap-through), without hiding the Hiring ring itself."""

    def setUp(self):
        from apps.commerce.business_models import JobListing

        self.JobListing = JobListing
        user = _make_active_user("+237699820030")
        self.profile, _ = Profile.objects.get_or_create(user=user)
        self.profile.work_status = WorkStatus.HIRING
        self.profile.save(update_fields=["work_status"])
        # Re-fetch - see the matching comment on WorkStatusVisibilityEnforcementTests.setUp:
        # the post_save signal's auto-created Profile is cached on the
        # in-memory `user` object from _make_active_user, which would make
        # user.profile.work_status read back stale ('none') even though the
        # DB row (self.profile) is correctly updated.
        self.user = User.objects.get(pk=user.pk)
        self.client_self = _auth_client(self.user, "hiring-device-a")

        self.other_user = _make_active_user("+237699820031")

        self.listing = JobListing.objects.create(
            poster=self.user,
            title="Senior Engineer",
            description="Build things.",
            is_active=True,
        )
        self.other_listing = JobListing.objects.create(
            poster=self.other_user,
            title="Not yours",
            description="Someone else's posting.",
            is_active=True,
        )

    def test_can_link_own_active_listing(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/",
            {"hiring_job_listing": str(self.listing.id)},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["hiring_job_listing_id"], str(self.listing.id))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hiring_job_listing_id, self.listing.id)

    def test_cannot_link_someone_elses_listing(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/",
            {"hiring_job_listing": str(self.other_listing.id)},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.hiring_job_listing_id)

    def test_can_activate_hiring_without_linking_a_listing(self):
        res = self.client_self.patch(
            f"/api/v1/profiles/{self.profile.id}/", {"work_status": "hiring"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["work_status"], "hiring")
        self.assertIsNone(res.data["hiring_job_listing_id"])

    def test_inactive_listing_reads_as_stale_but_ring_still_shows(self):
        self.profile.hiring_job_listing = self.listing
        self.profile.save(update_fields=["hiring_job_listing"])
        self.listing.is_active = False
        self.listing.save(update_fields=["is_active"])

        # Fresh fetch - see setUp's own comment: self.user.profile is cached
        # from before this test's mutations and won't reflect them otherwise.
        owner = User.objects.get(pk=self.user.pk)
        self.assertEqual(get_visible_work_status(owner, owner), "hiring")
        self.assertIsNone(get_visible_hiring_job_listing_id(owner, owner))

    def test_deleted_listing_reads_as_stale_but_ring_still_shows(self):
        self.profile.hiring_job_listing = self.listing
        self.profile.save(update_fields=["hiring_job_listing"])
        self.listing.delete()

        self.profile.refresh_from_db()
        owner = User.objects.get(pk=self.user.pk)
        self.assertEqual(get_visible_work_status(owner, owner), "hiring")
        self.assertIsNone(get_visible_hiring_job_listing_id(owner, owner))

    def test_hiring_job_listing_id_hidden_from_viewer_who_cannot_see_work_status(self):
        self.profile.hiring_job_listing = self.listing
        self.profile.save(update_fields=["hiring_job_listing"])
        ProfileFieldVisibility.objects.create(user=self.user, field_key="work_status", visibility="private")

        from .visibility import get_visible_hiring_job_listing_id as _get

        owner = User.objects.get(pk=self.user.pk)
        self.assertIsNone(_get(owner, self.other_user))
        self.assertEqual(_get(owner, owner), str(self.listing.id))

    def test_non_hiring_status_never_exposes_a_linked_listing(self):
        self.profile.hiring_job_listing = self.listing
        self.profile.work_status = WorkStatus.OPEN_FOR_WORK
        self.profile.save(update_fields=["hiring_job_listing", "work_status"])

        owner = User.objects.get(pk=self.user.pk)
        self.assertEqual(owner.profile.hiring_job_listing_id, self.listing.id)  # sanity: link really saved
        self.assertIsNone(get_visible_hiring_job_listing_id(owner, owner))
