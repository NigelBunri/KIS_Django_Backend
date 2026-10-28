"""
Server-side authorization for Books. Never rely on hiding a button in the
client — every one of these is re-checked on every request.

Reuses the platform's existing RBAC (admin_control.roles.AdminAccessService)
rather than inventing a parallel permission system. New permission strings
introduced for Books, assignable to any AdminRole via the existing
roles admin UI (AdminRoleView/AdminRoleAssignmentView):

  - "books.review"       — open the moderation queue, read full unpublished
                            manuscripts, approve/reject/request corrections.
  - "books.restrict"      — block/suspend/restore a book.
  - "books.ban_user"      — impose account-level publishing/account
                            restrictions on an author (the highest bar —
                            deliberately separate from books.review so a
                            reviewer who can read and approve books does not
                            automatically get ban authority).
  - "books.refund"        — issue a refund for a paid book purchase (moves
                            real money back via the payment provider, revokes
                            the buyer's entitlement, and reverses the
                            author's royalty record for that sale) —
                            deliberately separate from books.review, since
                            content moderation and financial refund
                            authority are different responsibilities.

The immutable GO identity (apps.partners.permissions.is_platform_go) and
any is_superuser always pass every check, matching how IsKCANAdmin already
behaves platform-wide.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission

from admin_control.roles import AdminAccessService
from apps.partners.permissions import is_platform_go


def _is_platform_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or is_platform_go(user))


def has_books_permission(user, permission: str) -> bool:
    if _is_platform_admin(user):
        return True
    return AdminAccessService.has_permission(user, app_label="books", permission=permission)


def can_review_books(user) -> bool:
    return has_books_permission(user, "books.review")


def can_restrict_books(user) -> bool:
    return has_books_permission(user, "books.restrict")


def can_ban_book_authors(user) -> bool:
    return has_books_permission(user, "books.ban_user")


def can_refund_book_purchases(user) -> bool:
    return has_books_permission(user, "books.refund")


class CanReviewBooks(BasePermission):
    message = "Book moderation access required."

    def has_permission(self, request, view):
        return can_review_books(request.user)


class CanRestrictBooks(BasePermission):
    message = "Book restriction authority required."

    def has_permission(self, request, view):
        return can_restrict_books(request.user)


class CanBanBookAuthors(BasePermission):
    message = "Account-restriction authority required."

    def has_permission(self, request, view):
        return can_ban_book_authors(request.user)


class CanRefundBookPurchases(BasePermission):
    message = "Refund authority required."

    def has_permission(self, request, view):
        return can_refund_book_purchases(request.user)


class IsBookOwner(BasePermission):
    message = "You do not own this book."

    def has_object_permission(self, request, view, obj):
        if _is_platform_admin(request.user):
            return True
        return obj.owner_id == request.user.id
