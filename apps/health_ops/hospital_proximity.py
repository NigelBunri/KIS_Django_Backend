"""Pluggable nearest-hospital lookup used by the emergency SOS flow.

There is no geospatial/Places vendor wired into this codebase yet, and this
module must never fabricate hospital data to fill that gap. The abstract
provider interface below is the single integration point: implement
`HospitalProximityProvider.find_nearby` against a real vendor (e.g. Google
Places, HERE, a national hospital registry) and point
`settings.HEALTH_HOSPITAL_PROXIMITY_PROVIDER` at it. Until then,
`NullHospitalProximityProvider` is used and honestly reports that no results
are available rather than returning placeholder entries that look real.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module

from django.conf import settings


@dataclass(frozen=True)
class HospitalProximityResult:
    name: str
    distance_km: float | None
    address: str
    phone: str | None
    latitude: float | None = None
    longitude: float | None = None


class HospitalProximityProvider(ABC):
    @abstractmethod
    def find_nearby(self, latitude: float | None, longitude: float | None, limit: int = 5) -> list[HospitalProximityResult]:
        """Return real nearby hospitals for the given coordinates, nearest first."""
        raise NotImplementedError


class NullHospitalProximityProvider(HospitalProximityProvider):
    """Default provider. No vendor is configured, so it returns no results.

    Deliberately does not fabricate a hospital entry — a caller that
    receives an empty list with `provider_configured: False` can render an
    honest "nearby hospital lookup is unavailable" state instead of showing
    fake data during an emergency.
    """

    def find_nearby(self, latitude, longitude, limit: int = 5) -> list[HospitalProximityResult]:
        return []


def get_hospital_proximity_provider() -> HospitalProximityProvider:
    dotted_path = getattr(
        settings,
        "HEALTH_HOSPITAL_PROXIMITY_PROVIDER",
        "apps.health_ops.hospital_proximity.NullHospitalProximityProvider",
    )
    module_path, _, class_name = dotted_path.rpartition(".")
    provider_cls = getattr(import_module(module_path), class_name)
    return provider_cls()


def is_hospital_proximity_configured() -> bool:
    return not isinstance(get_hospital_proximity_provider(), NullHospitalProximityProvider)
