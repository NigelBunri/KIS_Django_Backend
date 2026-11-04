# apps/bots/test_memory.py
"""
Phase 4 — bot-specific memory. Covers: off by default (a write attempt
with memory disabled is silently a no-op, never an error, and never
actually persists anything), retention/expiry, deletion (single entry
and "delete everything"), cross-bot isolation, and that enabling
memory never implicitly enables retaining raw message content
(allow_message_content is a separate, also-off-by-default flag).
"""
import datetime

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tiers import ensure_default_account_tiers

from . import memory, services
from .models import BotKind, BotMemoryEntry, BotStatus
from .test_workflows import _make_user, _uuid_suffix


def _active_bot(owner, name="MemoryBot"):
    bot = services.create_bot(owner, name=f"{name}-{_uuid_suffix()}", kind=BotKind.CUSTOM)
    services.set_status(bot, owner, BotStatus.ACTIVE)
    return bot


class MemoryConfigTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("memory-config-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)

    def test_memory_is_off_by_default(self):
        config = memory.get_or_create_memory_config(self.bot)
        self.assertFalse(config.enabled)
        self.assertFalse(config.allow_message_content)

    def test_remember_is_a_noop_when_disabled(self):
        entry = memory.remember(self.bot, content="A fact worth remembering")
        self.assertIsNone(entry)
        self.assertFalse(BotMemoryEntry.objects.filter(bot=self.bot).exists())

    def test_enabling_memory_does_not_implicitly_enable_message_content(self):
        memory.update_memory_config(self.bot, self.owner, enabled=True)
        config = memory.get_or_create_memory_config(self.bot)
        self.assertTrue(config.enabled)
        self.assertFalse(config.allow_message_content)

    def test_retention_days_bounds_enforced(self):
        with self.assertRaises(Exception):
            memory.update_memory_config(self.bot, self.owner, retention_days=0)
        with self.assertRaises(Exception):
            memory.update_memory_config(self.bot, self.owner, retention_days=9999)

    def test_only_owner_can_update_config(self):
        other = _make_user("memory-config-other@example.com")
        with self.assertRaises(Exception):
            memory.update_memory_config(self.bot, other, enabled=True)


class MemoryEntryTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("memory-entry-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)
        memory.update_memory_config(self.bot, self.owner, enabled=True, retention_days=7)

    def test_remember_and_retrieve(self):
        memory.remember(self.bot, content="This member asked about volunteering opportunities.")
        results = memory.retrieve_relevant_memory(self.bot, "volunteering")
        self.assertTrue(results)
        self.assertIn("volunteering", results[0]["content"])

    def test_entry_gets_expiry_from_config_retention(self):
        entry = memory.remember(self.bot, content="Short-lived note")
        self.assertIsNotNone(entry.expires_at)
        delta = entry.expires_at - timezone.now()
        self.assertTrue(6 <= delta.days <= 7)

    def test_expired_entries_excluded_from_retrieval_even_before_purge(self):
        entry = memory.remember(self.bot, content="Already stale note about scheduling")
        BotMemoryEntry.objects.filter(id=entry.id).update(expires_at=timezone.now() - datetime.timedelta(days=1))
        results = memory.retrieve_relevant_memory(self.bot, "scheduling")
        self.assertEqual(results, [])
        # Still exists in the table - only the purge task actually deletes it.
        self.assertTrue(BotMemoryEntry.objects.filter(id=entry.id).exists())

    def test_purge_task_deletes_expired_entries(self):
        entry = memory.remember(self.bot, content="Note to be purged")
        BotMemoryEntry.objects.filter(id=entry.id).update(expires_at=timezone.now() - datetime.timedelta(days=1))
        fresh = memory.remember(self.bot, content="Note to keep")
        purged_count = memory.purge_expired_memory()
        self.assertGreaterEqual(purged_count, 1)
        self.assertFalse(BotMemoryEntry.objects.filter(id=entry.id).exists())
        self.assertTrue(BotMemoryEntry.objects.filter(id=fresh.id).exists())

    def test_subject_scoped_memory_only_returned_for_that_subject(self):
        import uuid

        subject_a = uuid.uuid4()
        subject_b = uuid.uuid4()
        memory.remember(self.bot, content="Subject A likes gardening", subject_user_id=subject_a)
        memory.remember(self.bot, content="Subject B likes gardening too", subject_user_id=subject_b)
        results = memory.retrieve_relevant_memory(self.bot, "gardening", subject_user_id=subject_a)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject_user_id"], str(subject_a))

    def test_delete_single_entry(self):
        entry = memory.remember(self.bot, content="Delete me")
        memory.delete_memory_entry(self.bot, self.owner, entry.id)
        self.assertFalse(BotMemoryEntry.objects.filter(id=entry.id).exists())

    def test_delete_all_memory(self):
        memory.remember(self.bot, content="One")
        memory.remember(self.bot, content="Two")
        deleted = memory.delete_all_memory(self.bot, self.owner)
        self.assertEqual(deleted, 2)
        self.assertFalse(BotMemoryEntry.objects.filter(bot=self.bot).exists())

    def test_max_entries_evicts_oldest(self):
        from . import memory as memory_module

        memory_module.MAX_ENTRIES_PER_BOT = 3
        try:
            ids = []
            for i in range(4):
                entry = memory.remember(self.bot, content=f"Note {i}")
                ids.append(entry.id)
            self.assertEqual(BotMemoryEntry.objects.filter(bot=self.bot).count(), 3)
            self.assertFalse(BotMemoryEntry.objects.filter(id=ids[0]).exists())
        finally:
            memory_module.MAX_ENTRIES_PER_BOT = 2000


class MemoryCrossBotIsolationTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner_a = _make_user("memory-isolation-a@example.com", tier_name="Business")
        self.owner_b = _make_user("memory-isolation-b@example.com", tier_name="Business")
        self.bot_a = _active_bot(self.owner_a, "MemBotA")
        self.bot_b = _active_bot(self.owner_b, "MemBotB")
        memory.update_memory_config(self.bot_a, self.owner_a, enabled=True)
        memory.update_memory_config(self.bot_b, self.owner_b, enabled=True)
        memory.remember(self.bot_a, content="Bot A privately remembers a donor's pledge amount.")

    def test_bot_b_cannot_retrieve_bot_a_memory(self):
        results = memory.retrieve_relevant_memory(self.bot_b, "donor pledge amount")
        self.assertEqual(results, [])

    def test_other_owner_cannot_delete_bot_a_memory(self):
        entry = BotMemoryEntry.objects.get(bot=self.bot_a)
        with self.assertRaises(Exception):
            memory.delete_memory_entry(self.bot_a, self.owner_b, entry.id)
        self.assertTrue(BotMemoryEntry.objects.filter(id=entry.id).exists())

    def test_http_memory_list_never_leaks_across_bots(self):
        client = APIClient()
        client.force_authenticate(user=self.owner_b)
        response = client.get(f"/api/v1/bots/{self.bot_b.id}/memory/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
