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
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User, UserContact
from apps.moderation.models import AuditLog, Flag, UserBlock
from apps.statuses.models import (
    StatusItem,
    StatusItemView,
    StatusMute,
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

    def get_serializer_class(self):
        if self.action == "create":
            return StatusCreateSerializer
        return StatusItemSerializer

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
        base_items = (
            self.filter_queryset(self.get_queryset())
            .exclude(user_id__in=blocked_user_ids)
            .exclude(user_id__in=muted_user_ids)
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
        retention_days = features.get("status_retention_days")
        expires_at = None
        if isinstance(retention_days, int) and retention_days > 0:
            expires_at = timezone.now() + timedelta(days=retention_days)
        if expires_at:
            serializer.save(user=user, expires_at=expires_at)
        else:
            serializer.save(user=user)

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

        items = self.get_queryset().exclude(user_id__in=blocked_user_ids).exclude(user_id__in=muted_user_ids)
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

    @action(detail=True, methods=["post"], url_path="reply")
    def reply(self, request, pk=None):
        try:
            status_item = self._base_status_queryset().get(id=pk)
        except StatusItem.DoesNotExist:
            return Response({"detail": "Status not found."}, status=status.HTTP_404_NOT_FOUND)

        if status_item.reply_permission == "nobody":
            return Response({"detail": "Replies disabled for this status."}, status=status.HTTP_403_FORBIDDEN)

        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "Reply text is required."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.chat.models import Conversation, ConversationMember, Message
        from apps.chat.services import get_or_create_direct_conversation

        try:
            conversation = get_or_create_direct_conversation(
                user_a=request.user,
                user_b_id=status_item.user_id,
            )
        except Exception:
            return Response({"detail": "Could not open conversation."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        msg = Message.objects.create(
            conversation=conversation,
            sender_id=request.user.id,
            kind="text",
            text=text,
            metadata={"status_reply_id": str(status_item.id)},
        )
        AuditLog.objects.create(
            actor_id=request.user.id,
            action="status.reply",
            target_type="STATUS",
            target_id=status_item.id,
            metadata={"message_id": str(msg.id), "text_len": len(text)},
        )
        return Response(
            {"replied": True, "message_id": str(msg.id), "conversation_id": str(conversation.id)},
            status=status.HTTP_201_CREATED,
        )
