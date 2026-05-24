"""
Partner geolocation / attendance API views.

URL namespace: /api/v1/partners/<partner_id>/location/…

All views require:
  - IsAuthenticated
  - The requesting user must be a member or admin of the partner.

Admin-only endpoints additionally require the user to be an owner/admin/manager.
"""
from __future__ import annotations

import math
import csv
import io

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.partners.models import Partner, PartnerMembership
from apps.partners.services import partner_user_can_access, partner_user_can_manage

from .models import (
    AuditAction,
    EventStatus,
    PartnerLocationAttendance,
    PartnerLocationAuditLog,
    PartnerLocationConsent,
    PartnerLocationEvent,
    PartnerLocationZone,
    TargetType,
)
from .serializers import (
    MemberAttendanceStatusSerializer,
    PartnerLocationAttendanceSerializer,
    PartnerLocationAuditLogSerializer,
    PartnerLocationConsentSerializer,
    PartnerLocationEventSerializer,
    PartnerLocationZoneSerializer,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_CHECKIN_RATE_LIMIT_SECONDS = 60  # minimum gap between check-in attempts
_MAX_DISTANCE_RATIO = 1.5  # allow 150 % of declared radius as tolerance


def _get_partner_or_404(partner_id):
    try:
        return Partner.objects.get(id=partner_id, is_active=True)
    except (Partner.DoesNotExist, Exception):
        return None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return distance in metres between two WGS-84 coordinates."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _round_distance(meters: float) -> int:
    """Round to nearest 10 m for privacy."""
    return max(0, round(meters / 10) * 10)


def _audit(partner, event, actor, action, target_user=None, **meta):
    PartnerLocationAuditLog.objects.create(
        partner=partner,
        event=event,
        actor=actor,
        action=action,
        target_user=target_user,
        metadata=meta,
    )


def _safe_int(val, default, lo=1, hi=500):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


def _user_in_target_scope(event: PartnerLocationEvent, user, partner) -> bool:
    """
    Return True if `user` falls within the event's target scope.
    Called after partner-membership and consent have already been verified.
    """
    t = event.target_type

    if t == TargetType.ALL:
        return True

    if t == TargetType.USERS:
        ids = event.target_user_ids or []
        return str(user.id) in [str(i) for i in ids]

    if t == TargetType.ROLES:
        roles = event.target_roles or []
        if not roles:
            return True  # misconfigured — fail open inside partner
        try:
            membership = PartnerMembership.objects.get(partner=partner, user=user)
            return membership.role in roles
        except PartnerMembership.DoesNotExist:
            return False

    if t == TargetType.COMMUNITY:
        ref = event.target_ref_id
        if not ref:
            return True
        try:
            from apps.communities.models import CommunityMembership
            return CommunityMembership.objects.filter(
                community_id=ref,
                user=user,
                left_at__isnull=True,
                is_banned=False,
            ).exists()
        except Exception:
            return False

    if t == TargetType.GROUP:
        ref = event.target_ref_id
        if not ref:
            return True
        try:
            from django.contrib.contenttypes.models import ContentType
            from apps.core.models import Membership
            from apps.accounts.models import User as UserModel
            ct = ContentType.objects.get_for_model(UserModel)
            return Membership.objects.filter(
                group_id=ref,
                user_content_type=ct,
                user_object_id=str(user.pk),
                status=Membership.STATUS_ACTIVE,
            ).exists()
        except Exception:
            return False

    if t == TargetType.CHANNEL:
        ref = event.target_ref_id
        if not ref:
            return True
        try:
            from apps.broadcasts.models import BroadcastChannelSubscription
            return BroadcastChannelSubscription.objects.filter(
                channel_id=ref,
                user=user,
            ).exists()
        except Exception:
            return False

    # Unknown target type — default deny to be safe
    return False


# ── Permission guards ─────────────────────────────────────────────────────────

class _PartnerViewBase(APIView):
    permission_classes = [IsAuthenticated]

    def _get_partner(self, partner_id):
        return _get_partner_or_404(partner_id)

    def _require_access(self, partner, user):
        return partner_user_can_access(partner, user)

    def _require_manage(self, partner, user):
        return partner_user_can_manage(partner, user)


# ── Event list / create ───────────────────────────────────────────────────────

class LocationEventListView(_PartnerViewBase):
    """
    GET  /api/v1/partners/<partner_id>/location/events/
    POST /api/v1/partners/<partner_id>/location/events/
    """

    def get(self, request, partner_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_access(partner, request.user):
            return Response({"detail": "Not a member of this partner."}, status=status.HTTP_403_FORBIDDEN)

        is_admin = self._require_manage(partner, request.user)
        qs = PartnerLocationEvent.objects.filter(partner=partner, is_active=True)
        if not is_admin:
            qs = qs.filter(status=EventStatus.ACTIVE)

        page = _safe_int(request.query_params.get("page", 1), 1, 1, 1000)
        per_page = _safe_int(request.query_params.get("per_page", 20), 20, 1, 100)
        total = qs.count()
        events = qs[(page - 1) * per_page: page * per_page]
        return Response({
            "events": PartnerLocationEventSerializer(events, many=True).data,
            "pagination": {"page": page, "per_page": per_page, "total": total},
        })

    def post(self, request, partner_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        serializer = PartnerLocationEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        event = serializer.save(partner=partner, created_by=request.user)
        _audit(partner, event, request.user, AuditAction.EVENT_CREATED, title=event.title)
        return Response({"event": PartnerLocationEventSerializer(event).data}, status=status.HTTP_201_CREATED)


# ── Event detail / update / delete ────────────────────────────────────────────

class LocationEventDetailView(_PartnerViewBase):
    """
    GET    /api/v1/partners/<partner_id>/location/events/<event_id>/
    PATCH  /api/v1/partners/<partner_id>/location/events/<event_id>/
    DELETE /api/v1/partners/<partner_id>/location/events/<event_id>/
    """

    def _get_event(self, partner, event_id):
        return PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()

    def get(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_access(partner, request.user):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        event = self._get_event(partner, event_id)
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"event": PartnerLocationEventSerializer(event).data})

    def patch(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        event = self._get_event(partner, event_id)
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PartnerLocationEventSerializer(event, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        _audit(partner, event, request.user, AuditAction.EVENT_UPDATED, fields=list(request.data.keys()))
        return Response({"event": serializer.data})

    def delete(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        event = self._get_event(partner, event_id)
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        _audit(partner, event, request.user, AuditAction.EVENT_DELETED, title=event.title)
        event.is_active = False
        event.status = EventStatus.CANCELLED
        event.save(update_fields=["is_active", "status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Member check-in ───────────────────────────────────────────────────────────

class LocationCheckinView(_PartnerViewBase):
    """
    POST /api/v1/partners/<partner_id>/location/events/<event_id>/checkin/

    Body: {
        "lat": float,
        "lng": float,
        "device_os": "ios" | "android"   # optional
    }

    Privacy contract:
    - lat/lng are used only to compute distance, then discarded.
    - Only a rounded distance (to 10 m) is stored.
    - No continuous tracking, no background location.
    """

    def post(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_access(partner, request.user):
            return Response({"detail": "Not a member of this partner."}, status=status.HTTP_403_FORBIDDEN)

        # Consent check
        consent = PartnerLocationConsent.objects.filter(
            user=request.user, partner=partner, granted=True
        ).first()
        if not consent:
            return Response(
                {"detail": "Location consent is required. Please grant consent before checking in."},
                status=status.HTTP_403_FORBIDDEN,
            )

        event = PartnerLocationEvent.objects.filter(
            partner=partner, id=event_id, is_active=True, status=EventStatus.ACTIVE
        ).first()
        if not event:
            return Response({"detail": "Event not found or not active."}, status=status.HTTP_404_NOT_FOUND)

        # Target scope: verify the user belongs to the event's intended audience
        if not _user_in_target_scope(event, request.user, partner):
            return Response(
                {"detail": "You are not in the target audience for this event."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not event.is_checkin_open:
            return Response(
                {"detail": "Check-in window is not open for this event."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Already checked in? Return existing record (idempotent)
        existing = PartnerLocationAttendance.objects.filter(event=event, user=request.user).first()
        if existing:
            return Response(
                {
                    "attendance": MemberAttendanceStatusSerializer(existing).data,
                    "already_checked_in": True,
                },
                status=status.HTTP_200_OK,
            )

        # Rate limit: prevent rapid fake check-ins
        latest = (
            PartnerLocationAttendance.objects.filter(
                user=request.user, event__partner=partner
            )
            .order_by("-checked_in_at")
            .first()
        )
        if latest:
            gap = (timezone.now() - latest.checked_in_at).total_seconds()
            if gap < _CHECKIN_RATE_LIMIT_SECONDS:
                return Response(
                    {"detail": f"Please wait {int(_CHECKIN_RATE_LIMIT_SECONDS - gap)} seconds before checking in again."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

        # Geofence check
        lat = request.data.get("lat")
        lng = request.data.get("lng")
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Valid lat and lng are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        distance_m = _haversine_m(
            float(event.center_lat), float(event.center_lng), lat, lng
        )
        max_allowed = event.radius_meters * _MAX_DISTANCE_RATIO
        if distance_m > max_allowed:
            return Response(
                {
                    "detail": "You are outside the event area. Move closer and try again.",
                    "distance_m": _round_distance(distance_m),
                    "radius_m": event.radius_meters,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        is_late = (
            event.late_after_minutes > 0
            and (now - event.start_dt).total_seconds() > event.late_after_minutes * 60
        )

        device_os = str(request.data.get("device_os", "")).strip()[:16]

        # Atomic arrival number assignment
        with transaction.atomic():
            # Re-check for race condition
            if PartnerLocationAttendance.objects.filter(event=event, user=request.user).exists():
                att = PartnerLocationAttendance.objects.get(event=event, user=request.user)
                return Response(
                    {"attendance": MemberAttendanceStatusSerializer(att).data, "already_checked_in": True},
                    status=status.HTTP_200_OK,
                )
            # Lock the event row to serialise arrival number generation
            PartnerLocationEvent.objects.select_for_update().get(pk=event.pk)
            next_number = (
                PartnerLocationAttendance.objects.filter(event=event)
                .aggregate(mx=Max("arrival_number"))["mx"]
                or 0
            ) + 1
            attendance = PartnerLocationAttendance.objects.create(
                event=event,
                user=request.user,
                partner=partner,
                checked_in_at=now,
                is_late=is_late,
                arrival_number=next_number,
                distance_from_center_m=_round_distance(distance_m),
                location_verified=True,
                source="app",
                device_os=device_os,
            )

        _audit(
            partner, event, request.user, AuditAction.CHECKIN_RECORDED,
            target_user=request.user,
            arrival_number=next_number,
            is_late=is_late,
        )
        return Response(
            {"attendance": MemberAttendanceStatusSerializer(attendance).data},
            status=status.HTTP_201_CREATED,
        )


# ── Member status ─────────────────────────────────────────────────────────────

class LocationMyStatusView(_PartnerViewBase):
    """
    GET /api/v1/partners/<partner_id>/location/events/<event_id>/my-status/
    """

    def get(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_access(partner, request.user):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        event = PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        attendance = PartnerLocationAttendance.objects.filter(event=event, user=request.user).first()
        total = event.attendances.count() if event.show_checkin_count_to_members else None

        # Show arrival list only if admin-configured
        arrival_list = None
        if event.show_arrival_order_to_members:
            arrival_list = list(
                event.attendances.order_by("arrival_number").values_list("arrival_number", flat=True)
            )

        return Response({
            "event": {
                "id": str(event.id),
                "title": event.title,
                "status": event.status,
                "is_checkin_open": event.is_checkin_open,
                "radius_meters": event.radius_meters,
            },
            "checked_in": attendance is not None,
            "attendance": MemberAttendanceStatusSerializer(attendance).data if attendance else None,
            "checkin_count": total,
            "arrival_list": arrival_list,
        })


# ── Admin attendance list ─────────────────────────────────────────────────────

class LocationAttendanceListView(_PartnerViewBase):
    """
    GET /api/v1/partners/<partner_id>/location/events/<event_id>/attendance/

    Admin-only. Returns full attendance list with member details.
    Query params: status (checked_in | late | manual), page, per_page
    """

    def get(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        event = PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        qs = event.attendances.select_related("user").order_by("arrival_number")
        filter_status = request.query_params.get("status")
        if filter_status == "late":
            qs = qs.filter(is_late=True)
        elif filter_status == "manual":
            qs = qs.filter(is_manual=True)

        page = _safe_int(request.query_params.get("page", 1), 1, 1, 1000)
        per_page = _safe_int(request.query_params.get("per_page", 50), 50, 1, 200)
        total = qs.count()
        items = qs[(page - 1) * per_page: page * per_page]

        # Compute absent members (those targeted but not checked in)
        absent_count = None
        try:
            all_members = partner.memberships.count()
            absent_count = max(0, all_members - total)
        except Exception:
            pass

        return Response({
            "event": {"id": str(event.id), "title": event.title, "status": event.status},
            "attendance": PartnerLocationAttendanceSerializer(items, many=True).data,
            "summary": {
                "total_checked_in": event.attendances.count(),
                "late_count": event.attendances.filter(is_late=True).count(),
                "manual_count": event.attendances.filter(is_manual=True).count(),
                "absent_estimate": absent_count,
            },
            "pagination": {"page": page, "per_page": per_page, "total": total},
        })


# ── Admin manual check-in ─────────────────────────────────────────────────────

class LocationManualCheckinView(_PartnerViewBase):
    """
    POST /api/v1/partners/<partner_id>/location/events/<event_id>/attendance/manual-checkin/
    Body: {"user_id": "<uuid>"}
    """

    def post(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        event = PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.accounts.models import User
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        existing = PartnerLocationAttendance.objects.filter(event=event, user=target_user).first()
        if existing:
            return Response(
                {"attendance": PartnerLocationAttendanceSerializer(existing).data, "already_checked_in": True},
                status=status.HTTP_200_OK,
            )

        with transaction.atomic():
            PartnerLocationEvent.objects.select_for_update().get(pk=event.pk)
            next_number = (
                PartnerLocationAttendance.objects.filter(event=event)
                .aggregate(mx=Max("arrival_number"))["mx"]
                or 0
            ) + 1
            attendance = PartnerLocationAttendance.objects.create(
                event=event,
                user=target_user,
                partner=partner,
                arrival_number=next_number,
                distance_from_center_m=0,
                location_verified=False,
                source="manual_admin",
                is_manual=True,
                manually_adjusted_by=request.user,
                manually_adjusted_at=timezone.now(),
            )

        _audit(
            partner, event, request.user, AuditAction.CHECKIN_MANUAL,
            target_user=target_user,
            arrival_number=next_number,
        )
        return Response(
            {"attendance": PartnerLocationAttendanceSerializer(attendance).data},
            status=status.HTTP_201_CREATED,
        )


# ── Admin export ──────────────────────────────────────────────────────────────

class LocationAttendanceExportView(_PartnerViewBase):
    """
    GET /api/v1/partners/<partner_id>/location/events/<event_id>/attendance/export/

    Returns CSV. Only admins. Excludes precise location data.
    """

    def get(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        event = PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        attendances = event.attendances.select_related("user").order_by("arrival_number")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["arrival_number", "display_name", "checked_in_at", "is_late", "source", "distance_m"])
        for att in attendances:
            display = getattr(att.user, "display_name", None) or getattr(att.user, "username", None) or str(att.user_id)
            writer.writerow([
                att.arrival_number,
                display,
                att.checked_in_at.isoformat(),
                att.is_late,
                att.source,
                att.distance_from_center_m,
            ])

        _audit(partner, event, request.user, AuditAction.REPORT_EXPORTED)
        from django.http import HttpResponse
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="attendance_{event.id}.csv"'
        return response


# ── Consent ───────────────────────────────────────────────────────────────────

class LocationConsentView(_PartnerViewBase):
    """
    GET  /api/v1/partners/<partner_id>/location/consent/   — get my consent record
    POST /api/v1/partners/<partner_id>/location/consent/   — grant or revoke consent
         Body: {"granted": true|false}
    """

    def get(self, request, partner_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)

        consent, _ = PartnerLocationConsent.objects.get_or_create(
            user=request.user, partner=partner
        )
        return Response({"consent": PartnerLocationConsentSerializer(consent).data})

    def post(self, request, partner_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)

        granted = request.data.get("granted")
        if granted is None:
            return Response({"detail": "'granted' (bool) is required."}, status=status.HTTP_400_BAD_REQUEST)
        granted = bool(granted)

        now = timezone.now()
        consent, _ = PartnerLocationConsent.objects.get_or_create(
            user=request.user, partner=partner
        )
        consent.granted = granted
        if granted:
            consent.granted_at = now
            consent.revoked_at = None
        else:
            consent.revoked_at = now
        consent.save(update_fields=["granted", "granted_at", "revoked_at"])

        action = AuditAction.CONSENT_GRANTED if granted else AuditAction.CONSENT_REVOKED
        _audit(partner, None, request.user, action, target_user=request.user)

        return Response({"consent": PartnerLocationConsentSerializer(consent).data})


# ── Admin audit log ───────────────────────────────────────────────────────────

class LocationAuditLogView(_PartnerViewBase):
    """
    GET /api/v1/partners/<partner_id>/location/audit/
    GET /api/v1/partners/<partner_id>/location/events/<event_id>/audit/
    """

    def get(self, request, partner_id, event_id=None):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        qs = PartnerLocationAuditLog.objects.filter(partner=partner)
        if event_id:
            qs = qs.filter(event_id=event_id)

        page = _safe_int(request.query_params.get("page", 1), 1, 1, 1000)
        per_page = _safe_int(request.query_params.get("per_page", 50), 50, 1, 200)
        total = qs.count()
        items = qs[(page - 1) * per_page: page * per_page]
        return Response({
            "logs": PartnerLocationAuditLogSerializer(items, many=True).data,
            "pagination": {"page": page, "per_page": per_page, "total": total},
        })


# ── Zones ─────────────────────────────────────────────────────────────────────

class LocationZoneListView(_PartnerViewBase):
    """
    GET  /api/v1/partners/<partner_id>/location/events/<event_id>/zones/
    POST /api/v1/partners/<partner_id>/location/events/<event_id>/zones/
    """

    def _get_event(self, partner, event_id):
        return PartnerLocationEvent.objects.filter(partner=partner, id=event_id, is_active=True).first()

    def get(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_access(partner, request.user):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        event = self._get_event(partner, event_id)
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)
        zones = event.extra_zones.filter(is_active=True)
        return Response({"zones": PartnerLocationZoneSerializer(zones, many=True).data})

    def post(self, request, partner_id, event_id):
        partner = self._get_partner(partner_id)
        if not partner:
            return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)
        if not self._require_manage(partner, request.user):
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)
        event = self._get_event(partner, event_id)
        if not event:
            return Response({"detail": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PartnerLocationZoneSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        zone = serializer.save(event=event)
        _audit(partner, event, request.user, AuditAction.ZONE_CHANGED, zone_id=str(zone.id))
        return Response({"zone": PartnerLocationZoneSerializer(zone).data}, status=status.HTTP_201_CREATED)
