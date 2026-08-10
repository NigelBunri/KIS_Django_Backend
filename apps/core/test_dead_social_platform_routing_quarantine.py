"""
Phase 8: apps.core's generic Community/Group/Channel/Membership/Invite/
ModerationAction/*Settings viewsets (an early, superseded generic
social-platform scaffold) were still registered in apps/core/urls.py,
included BEFORE apps.communities.urls in config/urls.py — so
apps.core.views.CommunityViewSet was silently shadowing ALL of
/api/v1/communities/, making the real apps.communities app's REST API
completely unreachable. Confirmed via django.urls.resolve() before this
fix. /api/v1/groups/ and /api/v1/channels/ had no live collision (the real
apps.groups/apps.channels apps were already rerouted to /api/v1/chat-groups/
and /api/v1/partner-channels/ before this phase) — removing their dead
apps.core registrations is pure attack-surface cleanup, not a functional
restoration.

Run:
  python3 manage.py test apps.core.test_dead_social_platform_routing_quarantine --keepdb -v 2
"""
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class DeadSocialPlatformRoutingQuarantineTests(SimpleTestCase):
    def test_communities_now_resolves_to_the_real_communities_app(self):
        match = resolve("/api/v1/communities/")
        self.assertEqual(match.func.cls.__module__, "apps.communities.views")
        self.assertEqual(match.func.cls.__name__, "CommunityViewSet")

    def test_community_detail_resolves_to_the_real_communities_app(self):
        match = resolve("/api/v1/communities/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(match.func.cls.__module__, "apps.communities.views")

    def test_community_posts_no_longer_shadowed(self):
        match = resolve("/api/v1/communities/posts/")
        self.assertEqual(match.func.cls.__name__, "CommunityPostViewSet")

    def test_bare_groups_path_is_gone(self):
        with self.assertRaises(Resolver404):
            resolve("/api/v1/groups/")

    def test_bare_channels_path_is_gone(self):
        with self.assertRaises(Resolver404):
            resolve("/api/v1/channels/")

    def test_real_chat_communities_route_still_works(self):
        match = resolve("/api/v1/chat-communities/")
        self.assertEqual(match.func.cls.__module__, "apps.communities.views")

    def test_real_chat_groups_route_still_works(self):
        match = resolve("/api/v1/chat-groups/")
        self.assertEqual(match.func.cls.__module__, "apps.groups.views")

    def test_real_partner_channels_route_still_works(self):
        # apps/channels/urls.py registers at r"channels" under this prefix
        # (not the bare prefix itself, which is just the router's root view).
        match = resolve("/api/v1/partner-channels/channels/")
        self.assertEqual(match.func.cls.__module__, "apps.channels.views")

    def test_rbac_admin_endpoints_remain_registered(self):
        # Deliberately left in place — staff-only, not colliding with
        # anything, a legitimate (if currently unused) future extension
        # point. Only the Community/Group/Channel/Membership/etc.
        # registrations were removed.
        for path in ("/api/v1/permissions/", "/api/v1/roles/", "/api/v1/role-assignments/", "/api/v1/aces/"):
            match = resolve(path)
            self.assertEqual(match.func.cls.__module__, "apps.core.views")
