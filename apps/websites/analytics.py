"""
Privacy-preserving visitor hashing for on-site analytics. Never stores a
raw IP anywhere. `hash_visitor_session` salts IP+user-agent with
settings.SECRET_KEY (server-only, never exposed) and buckets by
calendar day, so the same visitor hashes the same way for same-day dedup
counting but the hash itself can't be reversed back to an IP, and a
visitor's hash changes every day rather than being a stable long-term
identifier.
"""
import hashlib

from django.conf import settings
from django.utils import timezone


def hash_visitor_session(ip_address: str, user_agent: str) -> str:
    day_bucket = timezone.now().strftime("%Y-%m-%d")
    raw = f"{settings.SECRET_KEY}:{ip_address}:{user_agent}:{day_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def extract_referrer_host(referrer: str) -> str:
    if not referrer:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(referrer).netloc[:255]
    except Exception:
        return ""
