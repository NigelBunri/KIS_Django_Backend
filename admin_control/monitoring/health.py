"""Monitoring helpers for the admin control panel."""
from typing import Dict


def health_check() -> Dict[str, str]:
    """Return a simple heartbeat map for Phase 1."""
    return {"status": "ok", "components": "pending"}
