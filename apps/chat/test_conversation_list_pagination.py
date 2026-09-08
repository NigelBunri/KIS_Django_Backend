"""
P0 fix: ConversationViewSet.get_queryset() had no explicit ORDER BY, so
Postgres could return conversations in a different row order across
successive LIMIT/OFFSET page requests against the same query (no ordering
guarantee is made for an unordered query once a table sees concurrent reads/
writes). For a user with more than one page of conversations this could
silently omit a conversation from every page, or return the same one twice.
Fixed by giving get_queryset() a deterministic pinned -> most-recent-activity
(NULLS LAST, so a never-messaged conversation doesn't jump to the top) -> id
ordering. See apps/chat/views.py ConversationViewSet.get_queryset().

Run:
  python3 manage.py test apps.chat.test_conversation_list_pagination --keepdb -v 2
"""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import BaseConversationRole, Conversation, ConversationMember, ConversationType


class ConversationListPaginationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348100000101", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def _make_conversations(self, count, pinned_indices=(), base_time=None):
        """Create `count` DIRECT conversations, each with a distinct peer and
        a strictly descending last_message_at, so conversation 0 is the most
        recently active and conversation N-1 is the oldest — activity order
        is unambiguous."""
        base_time = base_time or timezone.now()
        conversations = []
        for i in range(count):
            peer = User.objects.create_user(
                phone=f"+234820{i:06d}", password="pw123456", country="NG",
            )
            convo = Conversation.objects.create(
                type=ConversationType.DIRECT,
                created_by=self.user,
                last_message_at=base_time - datetime.timedelta(seconds=i),
            )
            ConversationMember.objects.create(
                conversation=convo, user=self.user, base_role=BaseConversationRole.OWNER,
                is_pinned=i in pinned_indices,
            )
            ConversationMember.objects.create(
                conversation=convo, user=peer, base_role=BaseConversationRole.MEMBER,
            )
            conversations.append(convo)
        return conversations

    def _fetch_all_pages(self, page_size=None):
        """Walk every page the API reports for the current user and return
        (ids_in_returned_order, meta_of_first_page)."""
        def _get(page):
            params = {"page": page}
            if page_size is not None:
                params["page_size"] = page_size
            res = self.client.get("/api/v1/conversations/", params)
            self.assertEqual(res.status_code, 200)
            return res.json()

        first = _get(1)
        ids = [row["id"] for row in first["results"]]
        total_pages = first["meta"]["total_pages"]
        for page in range(2, total_pages + 1):
            body = _get(page)
            ids.extend(row["id"] for row in body["results"])
        return ids, first["meta"]

    def _assert_full_coverage_no_dupes(self, ids, expected_conversations):
        self.assertEqual(
            len(ids), len(set(ids)),
            "pagination returned the same conversation on more than one page",
        )
        self.assertEqual(
            set(ids), {str(c.id) for c in expected_conversations},
            "pagination silently omitted or invented a conversation",
        )

    def test_25_conversations_fit_on_a_single_page(self):
        convos = self._make_conversations(25)
        ids, meta = self._fetch_all_pages()
        self.assertEqual(meta["total_pages"], 1)
        self.assertEqual(meta["count"], 25)
        self._assert_full_coverage_no_dupes(ids, convos)

    def test_26_conversations_require_a_second_page_with_no_loss_or_duplication(self):
        convos = self._make_conversations(26)
        ids, meta = self._fetch_all_pages()
        self.assertEqual(meta["total_pages"], 2)
        self.assertEqual(meta["count"], 26)
        self._assert_full_coverage_no_dupes(ids, convos)
        self.assertEqual(ids[0], str(convos[0].id))

    def test_50_conversations_paginate_completely(self):
        convos = self._make_conversations(50)
        ids, meta = self._fetch_all_pages()
        self.assertEqual(meta["total_pages"], 2)
        self.assertEqual(meta["count"], 50)
        self._assert_full_coverage_no_dupes(ids, convos)

    def test_100_conversations_paginate_completely(self):
        convos = self._make_conversations(100)
        ids, meta = self._fetch_all_pages()
        self.assertEqual(meta["total_pages"], 4)
        self.assertEqual(meta["count"], 100)
        self._assert_full_coverage_no_dupes(ids, convos)

    def test_ordering_is_stable_across_repeated_identical_requests(self):
        # The actual regression: an unordered queryset offers no guarantee
        # that two identical LIMIT/OFFSET calls return rows in the same
        # order. Fetch page 1 three times and require identical id order.
        self._make_conversations(60)
        first = self.client.get("/api/v1/conversations/", {"page": 1}).json()["results"]
        second = self.client.get("/api/v1/conversations/", {"page": 1}).json()["results"]
        third = self.client.get("/api/v1/conversations/", {"page": 1}).json()["results"]
        first_ids = [r["id"] for r in first]
        self.assertEqual(first_ids, [r["id"] for r in second])
        self.assertEqual(first_ids, [r["id"] for r in third])

    def test_pinned_conversations_sort_before_unpinned_regardless_of_activity(self):
        # Index 10 is far less recently active than indices 0-9, but pinned.
        convos = self._make_conversations(15, pinned_indices={10})
        res = self.client.get("/api/v1/conversations/", {"page": 1, "page_size": 100})
        ids = [row["id"] for row in res.json()["results"]]
        self.assertEqual(ids[0], str(convos[10].id))

    def test_conversation_with_no_messages_yet_does_not_jump_ahead_of_active_conversations(self):
        # Regression for a naive `-last_message_at` ordering: Postgres sorts
        # NULL first under a plain DESC ORDER BY, which would put a brand
        # new, never-messaged conversation at the very top of the list,
        # ahead of conversations the user is actually talking in. Give the
        # never-messaged conversation an old created_at (its only fallback
        # sort key) so this isolates the NULL-handling bug rather than
        # accidentally passing because it happens to be created last.
        active = self._make_conversations(5)
        never_messaged = Conversation.objects.create(
            type=ConversationType.DIRECT,
            created_by=self.user,
            last_message_at=None,
            created_at=timezone.now() - datetime.timedelta(days=1),
        )
        peer = User.objects.create_user(phone="+2348100009999", password="pw123456", country="NG")
        ConversationMember.objects.create(
            conversation=never_messaged, user=self.user, base_role=BaseConversationRole.OWNER,
        )
        ConversationMember.objects.create(
            conversation=never_messaged, user=peer, base_role=BaseConversationRole.MEMBER,
        )

        res = self.client.get("/api/v1/conversations/", {"page": 1, "page_size": 100})
        ids = [row["id"] for row in res.json()["results"]]

        self.assertEqual(ids[0], str(active[0].id))
        self.assertNotEqual(ids[0], str(never_messaged.id))

    def test_every_conversation_remains_discoverable_via_search_action(self):
        # The dedicated /search action reuses get_queryset(); confirm it
        # also benefits from the same deterministic ordering fix and does
        # not lose conversations relative to the default list action.
        convos = self._make_conversations(30)
        res = self.client.get("/api/v1/conversations/search/", {})
        self.assertEqual(res.status_code, 200)
        ids = {row["id"] for row in res.json()["results"]}
        self.assertTrue({str(c.id) for c in convos}.issubset(ids))
