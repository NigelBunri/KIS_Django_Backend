from django.test import TestCase
from django.contrib.contenttypes.models import ContentType

from apps.accounts.models import User
from apps.core import models


class CommunityPermissionHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670000001",
            password="StrongPass123",
            country="CM",
            email="core-tests@example.com",
        )
        self.community = models.Community.objects.create(
            slug="core-permission-tests",
            name="Core Permission Tests",
        )
        self.user_ct = ContentType.objects.get_for_model(User)
        self.community_ct = ContentType.objects.get_for_model(models.Community)
        self.permission = "community.manage"

    def _add_ace(self, *, effect: str, permissions: list[str]):
        return models.AccessControlEntry.objects.create(
            principal_content_type=self.user_ct,
            principal_object_id=str(self.user.id),
            target_content_type=self.community_ct,
            target_object_id=str(self.community.id),
            permissions=permissions,
            effect=effect,
        )

    def test_can_user_on_community_without_matching_aces_returns_false(self):
        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)

    def test_can_user_on_community_with_allow_ace_returns_true(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertTrue(allowed)

    def test_can_user_on_community_deny_ace_overrides_allow(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])
        self._add_ace(effect=models.AccessControlEntry.EFFECT_DENY, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)
