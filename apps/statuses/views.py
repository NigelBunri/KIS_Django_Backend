from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User, UserContact
from apps.accounts.visibility import get_visible_work_status, get_visible_hiring_job_listing_id
from apps.moderation.models import AuditLog, Flag, UserBlock
from apps.statuses.models import (
    StatusComment,
    StatusHide,
    StatusItem,
    StatusItemView,
    StatusMute,
    StatusReaction,
    StatusVisibility,
)
from apps.statuses.serializers import StatusItemSerializer, StatusCreateSerializer


class StatusViewSet(viewsets.ModelViewSet):
    """
    /api/v1/statuses/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = None
    # PUT/PATCH were reachable but unintended: ModelViewSet wires them to
    # UpdateModelMixin by default, which used StatusItemSerializer (the
    # *read* serializer - the only one with no read_only_fields at all)
    # for the write. That let an owner silently rewrite expires_at/
    # created_at/type/text/style/visibility on an already-posted status
    # with zero re-validation (no content-safety re-scan, no text-required
    # check, no audience-target sync for a visibility change) - a real
    # authorization/data-integrity gap Phase 4's security audit caught,
    # not a hypothetical. WhatsApp itself has no "edit a posted status"
    # feature either - the correct product behavior is delete-and-repost,
    # which destroy() below already supports. No frontend route ever
    # called PATCH/PUT here (confirmed: no update route exists in
    # ROUTES.statuses), so this closes a real gap, not a used feature.
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return StatusCreateSerializer
        return StatusItemSerializer

    def destroy(self, request, *args, **kwargs):
        """Soft-deletes (is_deleted=True, matching the is_deleted filter
        every read path in this file already applies) AND actually removes
        the underlying media file from storage now, rather than leaving it
        orphaned forever - the previous default ModelViewSet.destroy()
        hard-deleted the row (cascading away StatusItemView/
        StatusAudienceTarget with it) but never touched status_item.file
        at all, since Django's Model.delete() never deletes FieldFile
        content by default. A hard-deleted row also meant is_deleted, a
        field every single read-path query filters on, was dead - written
        nowhere in the codebase.
        """
        status_item = self.get_object()
        if status_item.file:
            try:
                status_item.file.delete(save=False)
            except Exception:
                # Best-effort - a storage-backend hiccup must not block the
                # user from deleting their own status; the row still flips
                # to is_deleted=True below, which is what every read path
                # actually enforces.
                pass
        status_item.is_deleted = True
        status_item.save(update_fields=["is_deleted"])
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.delete",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _parse_user_ids(self) -> set[str] | None:
        request = self.request
        ids: set[str] = set()
        has_filter = False

        raw_ids = request.query_params.get("userIds") or request.query_params.get("user_ids")
        if raw_ids:
            has_filter = True
            for token in raw_ids.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    ids.add(str(uuid.UUID(token)))
                except ValueError:
                    continue

        raw_phones = request.query_params.get("phones")
        if raw_phones:
            has_filter = True
            phones = [p.strip() for p in raw_phones.split(",") if p.strip()]
            if phones:
                found = User.objects.filter(phone__in=phones).values_list("id", flat=True)
                ids.update({str(uid) for uid in found})

        if not has_filter:
            return None
        ids.add(str(request.user.id))
        return ids

    def _get_status_contacts(self) -> tuple[set[str], set[str], set[str]]:
        user = self.request.user
        outgoing_contact_ids = set(
            str(value)
            for value in UserContact.objects.filter(
                user=user,
                contact_user__isnull=False,
            ).values_list("contact_user_id", flat=True)
        )
        incoming_contact_ids = set(
            str(value)
            for value in UserContact.objects.filter(contact_user=user).values_list("user_id", flat=True)
        )
        mutual_contact_ids = outgoing_contact_ids & incoming_contact_ids
        return outgoing_contact_ids, incoming_contact_ids, mutual_contact_ids

    def _get_blocked_user_ids(self) -> set[str]:
        from .services import get_blocked_user_ids

        return get_blocked_user_ids(self.request.user)

    def _get_muted_user_ids(self) -> set[str]:
        return {
            str(value)
            for value in StatusMute.objects.filter(user=self.request.user).values_list("muted_user_id", flat=True)
        }

    def _get_hidden_status_ids(self) -> set[str]:
        return {
            str(value)
            for value in StatusHide.objects.filter(user=self.request.user).values_list("status_id", flat=True)
        }

    def _get_viewable_status_or_404(self, pk) -> StatusItem:
        """Shared re-validation for every discussion action (react/unreact/
        reactions/comments/hide) - every one of them must independently
        re-check that the requesting user can actually see this status
        (not blocked, in-audience, not pending/blocked moderation) rather
        than trusting that a prior list/view call already proved it, since
        a status's audience/block state can change between requests. 404,
        not 403, for the same hide-not-reveal reason reply()/react() above
        already use for a status outside the caller's audience."""
        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            raise NotFound("Status not found.")

        blocked_user_ids = self._get_blocked_user_ids()
        _, _, mutual_contact_ids = self._get_status_contacts()
        if not self._can_view_status(
            status_item,
            viewer_id=str(self.request.user.id),
            blocked_user_ids=blocked_user_ids,
            author_contact_ids=self._get_author_contact_ids({str(status_item.user_id)}),
            mutual_contact_ids=mutual_contact_ids,
        ):
            raise NotFound("Status not found.")
        return status_item

    def _get_author_contact_ids(self, author_ids: set[str]) -> dict[str, set[str]]:
        if not author_ids:
            return {}
        rows = UserContact.objects.filter(
            user_id__in=author_ids,
            contact_user__isnull=False,
        ).values_list("user_id", "contact_user_id")
        mapping: dict[str, set[str]] = defaultdict(set)
        for author_id, contact_user_id in rows:
            mapping[str(author_id)].add(str(contact_user_id))
        return mapping

    def _can_view_status(
        self,
        status_item: StatusItem,
        *,
        viewer_id: str,
        blocked_user_ids: set[str],
        author_contact_ids: dict[str, set[str]] | None = None,
        mutual_contact_ids: set[str] | None = None,
    ) -> bool:
        # Extracted to apps/statuses/services.py in Phase 2 so
        # apps/statuses/media_hooks.py's access_authorizer (used by the
        # generic media signed-URL endpoint) can share the exact same logic
        # instead of a second copy — this method is now a thin wrapper, zero
        # behavior change, still covered by StatusPrivacyContractTests.
        from .services import can_view_status

        return can_view_status(
            status_item,
            viewer_id=viewer_id,
            blocked_user_ids=blocked_user_ids,
            author_contact_ids=author_contact_ids,
            mutual_contact_ids=mutual_contact_ids,
        )

    def _base_status_queryset(self):
        now = timezone.now()
        return (
            StatusItem.objects.select_related("user", "user__profile")
            .prefetch_related("audience_targets")
            .annotate(view_count=Count("views", distinct=True))
            .filter(is_deleted=False, expires_at__gt=now)
        )

    def _build_viewed_by_preview(self, items: list[StatusItem]) -> dict[str, list[dict]]:
        owner_items = [item for item in items if item.user_id == self.request.user.id]
        if not owner_items:
            return {}
        rows = (
            StatusItemView.objects.filter(status__in=owner_items)
            .select_related("user")
            .order_by("-viewed_at")
        )
        preview: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            bucket = preview[str(row.status_id)]
            if len(bucket) >= 5:
                continue
            bucket.append(
                {
                    "id": str(row.user_id),
                    "display_name": row.user.display_name or row.user.phone or f"User {row.user_id}",
                    "viewed_at": row.viewed_at,
                }
            )
        return preview

    def _serialize_status_items(self, request, items: list[StatusItem], viewed_ids: set[str], mutual_contact_ids: set[str]):
        return StatusItemSerializer(
            items,
            many=True,
            context={
                "request": request,
                "viewed_ids": viewed_ids,
                "mutual_contact_ids": mutual_contact_ids,
                "viewed_by_preview": self._build_viewed_by_preview(items),
            },
        ).data

    def get_queryset(self):
        base = self._base_status_queryset()
        if self.action in ("retrieve", "update", "partial_update", "destroy"):
            return base.filter(user=self.request.user)
        return base

    def list(self, request, *args, **kwargs):
        requested_user_ids = self._parse_user_ids()
        viewer_id = str(request.user.id)
        _, _, mutual_contact_ids = self._get_status_contacts()
        blocked_user_ids = self._get_blocked_user_ids()
        muted_user_ids = self._get_muted_user_ids()
        hidden_status_ids = self._get_hidden_status_ids()
        base_items = (
            self.filter_queryset(self.get_queryset())
            .exclude(user_id__in=blocked_user_ids)
            .exclude(user_id__in=muted_user_ids)
            .exclude(id__in=hidden_status_ids)
            .order_by("-created_at")
        )
        base_items = list(base_items)
        author_contact_ids = self._get_author_contact_ids({str(item.user_id) for item in base_items})
        items = [
            item
            for item in base_items
            if (requested_user_ids is None or str(item.user_id) in requested_user_ids)
            and self._can_view_status(
                item,
                viewer_id=viewer_id,
                blocked_user_ids=blocked_user_ids,
                author_contact_ids=author_contact_ids,
                mutual_contact_ids=mutual_contact_ids,
            )
        ]
        viewed_ids = set(
            str(sid)
            for sid in StatusItemView.objects.filter(
                user=request.user,
                status_id__in=[item.id for item in items],
            ).values_list("status_id", flat=True)
        )

        grouped: dict[str, dict] = {}
        items_by_user: dict[str, list] = defaultdict(list)
        for item in items:
            items_by_user[str(item.user_id)].append(item)

        for user_id, user_items in items_by_user.items():
            user = user_items[0].user
            grouped[user_id] = {
                "user": {
                    "id": str(user.id),
                    "display_name": user.display_name or user.phone or f"User {user.id}",
                    "avatar_url": getattr(user.profile, "avatar_url", None),
                    "work_status": get_visible_work_status(user, request.user),
                    "hiring_job_listing_id": get_visible_hiring_job_listing_id(user, request.user),
                },
                "items": self._serialize_status_items(
                    request,
                    user_items,
                    viewed_ids,
                    mutual_contact_ids,
                ),
                "latest_at": user_items[0].created_at,
                "is_muted": user_id in muted_user_ids,
            }
            grouped[user_id]["has_unseen"] = any(
                not item.get("viewed") for item in grouped[user_id]["items"]
            )

        ordered = sorted(
            grouped.values(),
            key=lambda entry: entry["latest_at"],
            reverse=True,
        )

        # Move current user to the front if present.
        current_id = str(request.user.id)
        ordered = [item for item in ordered if item["user"]["id"] == current_id] + [
            item for item in ordered if item["user"]["id"] != current_id
        ]

        for entry in ordered:
            entry.pop("latest_at", None)

        return Response({"results": ordered}, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value

        user = self.request.user
        features = get_user_tier_features(user)
        limit_mb = normalize_limit_value(features.get("media_storage_mb"), default=None)
        if limit_mb is not None:
            limit_bytes = int(limit_mb) * 1024 * 1024
            file_obj = self.request.FILES.get("file")
            resolved_intent = getattr(serializer, "_resolved_media_intent", None)
            incoming_bytes = file_obj.size if file_obj else (resolved_intent.size_bytes if resolved_intent else None)
            if incoming_bytes is not None and incoming_bytes > limit_bytes:
                raise ValidationError({"detail": "Status file exceeds your storage limit."})
        # Hard product cap: a status lives for at most
        # StatusItem.MAX_LIFETIME_HOURS (72h) no matter what a tier's
        # status_retention_days preset says (some tiers configure up to 14
        # days) - StatusItem.save()'s own fallback already uses the same
        # ceiling when expires_at is omitted, so clamping here just makes
        # every tier actually honor it instead of only the ones with no
        # explicit retention preset. A client can never extend this by
        # sending its own expires_at - StatusCreateSerializer declares it
        # read_only, and it's computed here server-side from
        # timezone.now() regardless of anything in the request.
        retention_days = features.get("status_retention_days")
        if isinstance(retention_days, int) and retention_days > 0:
            retention_hours = min(retention_days * 24, StatusItem.MAX_LIFETIME_HOURS)
        else:
            retention_hours = StatusItem.MAX_LIFETIME_HOURS
        expires_at = timezone.now() + timedelta(hours=retention_hours)
        serializer.save(user=user, expires_at=expires_at)

    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        items = (
            self.get_queryset()
            .filter(user=request.user)
            .order_by("-created_at")
        )
        _, _, mutual_contact_ids = self._get_status_contacts()
        viewed_ids = set(
            str(sid)
            for sid in StatusItemView.objects.filter(
                user=request.user,
                status_id__in=[item.id for item in items],
            ).values_list("status_id", flat=True)
        )
        data = self._serialize_status_items(request, list(items), viewed_ids, mutual_contact_ids)
        return Response({"results": data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="view")
    def mark_view(self, request, pk=None):
        try:
            status_item = self._base_status_queryset().get(
                id=pk,
                is_deleted=False,
                expires_at__gt=timezone.now(),
            )
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        _, _, mutual_contact_ids = self._get_status_contacts()
        blocked_user_ids = self._get_blocked_user_ids()
        if not self._can_view_status(
            status_item,
            viewer_id=str(request.user.id),
            blocked_user_ids=blocked_user_ids,
            author_contact_ids=self._get_author_contact_ids({str(status_item.user_id)}),
            mutual_contact_ids=mutual_contact_ids,
        ):
            return Response({"detail": "You cannot view this status."}, status=status.HTTP_403_FORBIDDEN)

        StatusItemView.objects.get_or_create(status=status_item, user=request.user)
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.view",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={"author_id": str(status_item.user_id)},
        )
        return Response({"viewed": True, "id": str(status_item.id)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="media-url")
    def media_url(self, request, pk=None):
        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        if not status_item.file:
            return Response({"detail": "This status has no media."}, status=status.HTTP_404_NOT_FOUND)

        _, _, mutual_contact_ids = self._get_status_contacts()
        blocked_user_ids = self._get_blocked_user_ids()
        if not self._can_view_status(
            status_item,
            viewer_id=str(request.user.id),
            blocked_user_ids=blocked_user_ids,
            author_contact_ids=self._get_author_contact_ids({str(status_item.user_id)}),
            mutual_contact_ids=mutual_contact_ids,
        ):
            return Response({"detail": "You cannot view this status."}, status=status.HTTP_403_FORBIDDEN)

        from django.core.files.storage import default_storage

        return Response(
            {
                "mediaUrl": status_item.file.url,
                "expiresInSeconds": getattr(default_storage, "presigned_expiry", 3600),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        query = (request.query_params.get("q") or "").strip()
        type_filter = (request.query_params.get("type") or "").strip().lower()
        viewer_id = str(request.user.id)
        _, _, mutual_contact_ids = self._get_status_contacts()
        blocked_user_ids = self._get_blocked_user_ids()
        muted_user_ids = self._get_muted_user_ids()
        hidden_status_ids = self._get_hidden_status_ids()

        items = (
            self.get_queryset()
            .exclude(user_id__in=blocked_user_ids)
            .exclude(user_id__in=muted_user_ids)
            .exclude(id__in=hidden_status_ids)
        )
        if type_filter:
            items = items.filter(type=type_filter)
        if query:
            items = items.filter(
                Q(text__icontains=query)
                | Q(user__display_name__icontains=query)
                | Q(user__phone__icontains=query)
            )
        items = [
            item
            for item in items.order_by("-created_at")[:100]
            if self._can_view_status(
                item,
                viewer_id=viewer_id,
                blocked_user_ids=blocked_user_ids,
                author_contact_ids=self._get_author_contact_ids({str(item.user_id)}),
                mutual_contact_ids=mutual_contact_ids,
            )
        ]
        viewed_ids = set(
            str(sid)
            for sid in StatusItemView.objects.filter(
                user=request.user,
                status_id__in=[item.id for item in items],
            ).values_list("status_id", flat=True)
        )
        data = self._serialize_status_items(request, items, viewed_ids, mutual_contact_ids)
        return Response({"results": data, "count": len(data)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="viewers")
    def viewers(self, request, pk=None):
        try:
            status_item = self.get_queryset().get(id=pk, user=request.user)
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        rows = (
            StatusItemView.objects.filter(status=status_item)
            .select_related("user")
            .order_by("-viewed_at")
        )
        results = [
            {
                "id": str(row.user_id),
                "display_name": row.user.display_name or row.user.phone or f"User {row.user_id}",
                "viewed_at": row.viewed_at,
            }
            for row in rows
        ]
        return Response(
            {
                "status_id": str(status_item.id),
                "view_count": len(results),
                "results": results,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mute")
    def mute(self, request):
        target_user_id = str(request.data.get("user_id") or "").strip()
        if not target_user_id:
            raise ValidationError({"user_id": "This field is required."})
        if target_user_id == str(request.user.id):
            raise ValidationError({"user_id": "You cannot mute yourself."})
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            raise ValidationError({"user_id": "User not found."})

        mute, created = StatusMute.objects.get_or_create(user=request.user, muted_user=target_user)
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.mute",
            target_type="USER",
            target_id=target_user.id,
            metadata={"created": created},
        )
        return Response(
            {
                "muted": True,
                "created": created,
                "user_id": str(target_user.id),
                "mute_id": str(mute.id),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="unmute")
    def unmute(self, request):
        target_user_id = str(request.data.get("user_id") or "").strip()
        if not target_user_id:
            raise ValidationError({"user_id": "This field is required."})
        deleted, _ = StatusMute.objects.filter(
            user=request.user,
            muted_user_id=target_user_id,
        ).delete()
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.unmute",
            target_type="USER",
            target_id=target_user_id,
            metadata={"removed": deleted > 0},
        )
        return Response(
            {
                "muted": False,
                "removed": deleted > 0,
                "user_id": target_user_id,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="report")
    def report(self, request, pk=None):
        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        reason = (request.data.get("reason") or "").strip() or "status_report"
        flag = Flag.objects.create(
            source="USER",
            target_type="STATUS",
            target_id=status_item.id,
            reporter_id=request.user.id,
            reason=reason,
            severity="MEDIUM",
            tags=["status"],
        )
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.report",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={"flag_id": str(flag.id), "reason": reason},
        )
        return Response(
            {
                "reported": True,
                "flag_id": str(flag.id),
                "status_id": str(status_item.id),
            },
            status=status.HTTP_201_CREATED,
        )

    def _user_preview(self, user) -> dict:
        return {
            "id": str(user.id),
            "display_name": user.display_name or user.phone or f"User {user.id}",
            "avatar_url": getattr(getattr(user, "profile", None), "avatar_url", None),
            "work_status": get_visible_work_status(user, self.request.user),
            "hiring_job_listing_id": get_visible_hiring_job_listing_id(user, self.request.user),
        }

    @action(detail=True, methods=["post"], url_path="reply")
    def reply(self, request, pk=None):
        """Back-compat alias for the original quick-reply box: creates a
        top-level StatusComment (no parent) - see comments() below for the
        real, full-featured endpoint (supports parent_id for threaded
        replies, pagination for reads). Kept so older app builds that still
        call /reply/ keep working unchanged.

        Previously delivered the reply as a real chat message via
        deliver_status_reply_message, into a direct conversation it would
        get_or_create between the viewer and the status's author - this
        both polluted the two participants' ordinary chat history with
        every single status reply, and meant a reply's success depended on
        a synchronous HTTP round-trip to Nest (wrong internal token name
        and a wrong call signature meant this path had never actually
        worked in production - see git history). Replies/comments now live
        entirely in StatusComment, scoped to the status, never touching
        apps.chat at all.
        """
        return self._create_comment(request, pk, parent_id=None)

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        """GET: list comments for this status - top-level only by default,
        or a single comment's replies when ?parent_id= is given (so the
        Replies tab can lazily load a thread's replies instead of fetching
        every reply for every comment up front). Supports ?before= (an
        ISO timestamp or comment id) for incremental loading of older
        comments/replies.

        POST: create a comment (parent_id absent) or a reply to an
        existing comment on the SAME status (parent_id present) - see
        _create_comment for the full validation (text required, 2000-char
        cap, reply_permission, visibility, client_id idempotency, and that
        a reply's parent really does belong to this exact status, not a
        different one supplied via a tampered parent_id).
        """
        if request.method == "POST":
            parent_id = (request.data.get("parent_id") or "").strip() or None
            return self._create_comment(request, pk, parent_id=parent_id)

        status_item = self._get_viewable_status_or_404(pk)
        parent_id = (request.query_params.get("parent_id") or "").strip() or None
        try:
            limit = max(1, min(int(request.query_params.get("limit", 30)), 100))
        except (TypeError, ValueError):
            limit = 30
        before_raw = (request.query_params.get("before") or "").strip()

        qs = StatusComment.objects.filter(status=status_item, is_deleted=False)
        qs = qs.filter(parent_id=parent_id) if parent_id else qs.filter(parent__isnull=True)
        if before_raw:
            try:
                before_comment = StatusComment.objects.get(id=before_raw)
                qs = qs.filter(created_at__lt=before_comment.created_at)
            except (StatusComment.DoesNotExist, ValueError):
                pass
        qs = qs.select_related("author", "author__profile").order_by("-created_at")[: limit + 1]

        rows = list(qs)
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()  # chronological for display, newest-fetched-first for pagination above

        reply_counts: dict[str, int] = {}
        if not parent_id and rows:
            counted = (
                StatusComment.objects.filter(parent_id__in=[r.id for r in rows], is_deleted=False)
                .values("parent_id")
                .annotate(n=Count("id"))
            )
            reply_counts = {str(row["parent_id"]): row["n"] for row in counted}

        results = [
            {
                "id": str(row.id),
                "parent_id": str(row.parent_id) if row.parent_id else None,
                "author": self._user_preview(row.author),
                "text": row.text,
                "created_at": row.created_at,
                "reply_count": reply_counts.get(str(row.id), 0),
                "is_mine": row.author_id == request.user.id,
            }
            for row in rows
        ]
        return Response(
            {"status_id": str(status_item.id), "results": results, "has_more": has_more},
            status=status.HTTP_200_OK,
        )

    def _create_comment(self, request, pk, *, parent_id: str | None):
        from apps.statuses.services import notify_status_discussion_event

        status_item = self._get_viewable_status_or_404(pk)

        if status_item.reply_permission == "nobody":
            return Response({"detail": "Replies disabled for this status."}, status=status.HTTP_403_FORBIDDEN)

        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "Comment text is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return Response({"detail": "Comment is too long (2000 characters max)."}, status=status.HTTP_400_BAD_REQUEST)

        parent = None
        if parent_id:
            try:
                parent = StatusComment.objects.get(id=parent_id, status=status_item, is_deleted=False)
            except (StatusComment.DoesNotExist, ValueError):
                # Deliberately the same "not found" shape as a bad status id -
                # never reveal whether parent_id exists on a DIFFERENT status,
                # which is exactly the "attach a reply to a different status by
                # manipulating an id" case this guards against.
                return Response({"detail": "Comment not found."}, status=status.HTTP_404_NOT_FOUND)
            if parent.parent_id:
                return Response({"detail": "Cannot reply to a reply."}, status=status.HTTP_400_BAD_REQUEST)

        client_id = (request.data.get("client_id") or "").strip()
        if client_id:
            existing = StatusComment.objects.filter(
                status=status_item, author=request.user, client_id=client_id,
            ).first()
            if existing:
                return Response(
                    self._serialize_comment(existing, is_mine=True),
                    status=status.HTTP_200_OK,
                )

        comment = StatusComment.objects.create(
            status=status_item,
            author=request.user,
            parent=parent,
            text=text,
            client_id=client_id,
        )
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.comment" if parent is None else "status.comment_reply",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={"comment_id": str(comment.id), "parent_id": parent_id, "text_len": len(text)},
        )
        notify_status_discussion_event(
            status_id=str(status_item.id),
            event="status.comment_added",
            payload=self._serialize_comment(comment, is_mine=False),
        )
        return Response(self._serialize_comment(comment, is_mine=True), status=status.HTTP_201_CREATED)

    def _serialize_comment(self, comment: StatusComment, *, is_mine: bool) -> dict:
        return {
            "id": str(comment.id),
            "parent_id": str(comment.parent_id) if comment.parent_id else None,
            "author": self._user_preview(comment.author),
            "text": comment.text,
            "created_at": comment.created_at,
            "reply_count": 0,
            "is_mine": is_mine,
        }

    @action(detail=True, methods=["post"], url_path="react")
    def react(self, request, pk=None):
        """Heart/emoji quick-reaction - persists directly to StatusReaction
        only. Previously also delivered the reaction as a real chat message
        via deliver_status_reply_message (same chat-polluting, cross-
        service-dependent path reply() used - see its docstring above for
        why that was removed)."""
        from apps.statuses.services import notify_status_discussion_event

        status_item = self._get_viewable_status_or_404(pk)

        if status_item.reply_permission == "nobody":
            return Response({"detail": "Reactions disabled for this status."}, status=status.HTTP_403_FORBIDDEN)

        emoji = (request.data.get("emoji") or "").strip()
        if not emoji:
            return Response({"detail": "An emoji is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(emoji) > 16:
            return Response({"detail": "Invalid emoji."}, status=status.HTTP_400_BAD_REQUEST)

        # update_or_create on the (status, user) unique constraint is what
        # makes "react again with a different emoji" REPLACE the existing
        # reaction rather than stack a second row for the same user - the
        # product's single-reaction-per-user-per-status rule is enforced by
        # the DB constraint itself (status_item_unique_reaction), not just
        # this upsert's good behavior.
        reaction, created = StatusReaction.objects.update_or_create(
            status=status_item, user=request.user, defaults={"emoji": emoji, "created_at": timezone.now()},
        )
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.react",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={"emoji": emoji, "created": created},
        )
        notify_status_discussion_event(
            status_id=str(status_item.id),
            event="status.reaction_changed",
            payload={"status_id": str(status_item.id), "user_id": str(request.user.id), "emoji": emoji},
        )
        return Response(
            {"reacted": True, "emoji": emoji, "status_id": str(status_item.id)},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="unreact")
    def unreact(self, request, pk=None):
        from apps.statuses.services import notify_status_discussion_event

        status_item = self._get_viewable_status_or_404(pk)
        deleted, _ = StatusReaction.objects.filter(status=status_item, user=request.user).delete()
        if deleted:
            AuditLog.objects.create(
                actor_id=request.user.id,
                action="status.unreact",
                target_type="STATUS",
                target_id=status_item.id,
                metadata={},
            )
            notify_status_discussion_event(
                status_id=str(status_item.id),
                event="status.reaction_changed",
                payload={"status_id": str(status_item.id), "user_id": str(request.user.id), "emoji": None},
            )
        return Response(
            {"reacted": False, "removed": bool(deleted), "status_id": str(status_item.id)},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="reactions")
    def reactions(self, request, pk=None):
        """Reactions grouped by emoji, with per-group participant previews -
        available to any viewer who can see the status (previously
        owner-only), since the Reactions tab is something every authorized
        viewer, not just the author, can open."""
        status_item = self._get_viewable_status_or_404(pk)

        rows = (
            StatusReaction.objects.filter(status=status_item)
            .select_related("user", "user__profile")
            .order_by("-created_at")
        )
        groups: dict[str, dict] = {}
        my_reaction = None
        for row in rows:
            if row.user_id == request.user.id:
                my_reaction = row.emoji
            bucket = groups.setdefault(row.emoji, {"emoji": row.emoji, "count": 0, "users": []})
            bucket["count"] += 1
            bucket["users"].append({**self._user_preview(row.user), "created_at": row.created_at})

        ordered_groups = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
        return Response(
            {
                "status_id": str(status_item.id),
                "reaction_count": sum(g["count"] for g in ordered_groups),
                "groups": ordered_groups,
                "my_reaction": my_reaction,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="hide")
    def hide_status(self, request, pk=None):
        """Per-viewer "not interested in this one" - affects only the
        caller's own feed/list (StatusHide, filtered in list()/search()
        above), never the author or any other viewer, and distinct from
        StatusMute (hides every status from a given author, not just one)."""
        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        StatusHide.objects.get_or_create(user=request.user, status=status_item)
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.hide",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={},
        )
        return Response({"hidden": True, "status_id": str(status_item.id)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unhide")
    def unhide_status(self, request, pk=None):
        try:
            status_uuid = uuid.UUID(str(pk))
        except (TypeError, ValueError):
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        deleted, _ = StatusHide.objects.filter(user=request.user, status_id=status_uuid).delete()
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.unhide",
            target_type="STATUS",
            target_id=status_uuid,
            metadata={"removed": deleted > 0},
        )
        return Response({"hidden": False, "removed": bool(deleted), "status_id": str(status_uuid)}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get"],
        url_path="can-view",
        permission_classes=[],
        authentication_classes=[],
    )
    def can_view(self, request, pk=None):
        """Internal-only: lets Nest's status.join socket handler re-verify
        (DjangoConversationClient.canViewStatus) that a user may join the
        status:{id} realtime room before letting them subscribe to its
        reaction/comment events - the same can_view_status rule every REST
        read/write path in this file already enforces, so a user excluded
        from a status's audience can't eavesdrop on its discussion via a
        socket subscription just because the REST endpoints are locked
        down. No request.user here - the query param is the user Nest is
        checking on behalf of, not the (service) caller."""
        from apps.chat.internal_auth import require_internal_auth

        require_internal_auth(request)

        user_id = str(request.query_params.get("user_id") or "").strip()
        if not user_id:
            return Response({"can_view": False}, status=400)

        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            return Response({"can_view": False}, status=404)

        blocked_by_me = set(UserBlock.objects.filter(blocker_id=user_id).values_list("blocked_id", flat=True))
        blocked_me = set(UserBlock.objects.filter(blocked_id=user_id).values_list("blocker_id", flat=True))
        blocked_user_ids = {str(v) for v in blocked_by_me | blocked_me}

        outgoing = set(
            UserContact.objects.filter(user_id=user_id, contact_user__isnull=False)
            .values_list("contact_user_id", flat=True)
        )
        author_contact_ids = {str(status_item.user_id): {str(v) for v in outgoing}}

        from .services import can_view_status

        can_view = can_view_status(
            status_item,
            viewer_id=user_id,
            blocked_user_ids=blocked_user_ids,
            author_contact_ids=author_contact_ids,
        )
        return Response({"can_view": can_view}, status=200)
