"""
QUARANTINED — superseded by apps.accounts (AccountTier/Subscription/
UsageQuota) + apps.billing (upgrade/downgrade/cancel/renewal services).

This app's models (a fully separate shadow User/Organization/Subscription/
BillingPlan system, plus speculative features like HolographicRoom and
QuantumEncryptionSetting) are not consumed anywhere in the real tier/
feature-gating system. Its URLs are deliberately NOT included in
config/urls.py — see the comment there for why (10 of its 15 routes were
live and publicly reachable before that was removed).

Kept in INSTALLED_APPS only so its migrations and any existing database
rows remain accessible via the ORM for a manual data audit — do not extend
this app or route new URLs to it. New tier/billing/entitlement work belongs
in apps.accounts / apps.billing.
"""
from django.apps import AppConfig


class TiersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tiers'

    def ready(self):
        # Signal wiring intentionally removed: apps/tiers/signals.py queued
        # a Celery reconciliation task on every Subscription save. With the
        # URL exposure removed there is no remaining reachable path that
        # creates/updates a apps.tiers.Subscription row, so this had no
        # trigger left — left disconnected rather than deleted so the
        # historical intent stays visible in signals.py.
        pass
