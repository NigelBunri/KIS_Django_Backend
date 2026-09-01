"""
Staff visibility into self-reported under-13 accounts with no guardian link.

Context: apps.accounts.models.User.is_under_13 is computed from the
optional, self-reported date_of_birth field, but nothing in the codebase
consumed it before this - it was pure dead scaffolding. This view wires it
into a real, actionable control: giving staff (not the public, not the
user) a queue of accounts that self-report as under 13 and have no
FamilyMember link to an active FamilyAccount, i.e. no guardian relationship
on file at all.

This is an ENGINEERING RECOMMENDATION that operationalizes a legally
relevant risk category (COPPA in the US requires verifiable parental
consent before collecting personal data from under-13 users; NDPR/GDPR
carry their own, higher, minor-protection duties). It is explicitly NOT a
claim of COPPA/NDPR/GDPR compliance by itself - date_of_birth is optional
and unenforced at signup (the published mobile app doesn't collect it
yet), so this queue only ever sees the subset of under-13 users who
happened to have a DOB on file, not every actual under-13 user. Genuine
compliance requires a verified-parental-consent mechanism at signup, which
is a separate, larger product decision this view does not make or imply.
"""
from datetime import date

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_control.permissions import IsAdminControlUser
from apps.accounts.models import User
from apps.family.models import FamilyMember


def _thirteen_years_ago(today: date) -> date:
    try:
        return today.replace(year=today.year - 13)
    except ValueError:
        # today is Feb 29 and (today.year - 13) isn't a leap year.
        return today.replace(year=today.year - 13, day=28)


def _unsupervised_under_13_queryset():
    today = timezone.now().date()
    cutoff = _thirteen_years_ago(today)
    candidates = User.objects.filter(date_of_birth__isnull=False, date_of_birth__gt=cutoff)
    supervised_ids = FamilyMember.objects.filter(
        user_id__in=candidates.values_list("id", flat=True),
        family__is_active=True,
    ).values_list("user_id", flat=True)
    return candidates.exclude(id__in=supervised_ids).order_by("-created_at")


class AdminUnsupervisedMinorsListView(APIView):
    """
    GET /control/admin/child-safety/unsupervised-minors/

    Lists self-reported under-13 accounts with no active guardian
    (FamilyMember) link, newest signup first. Read-only - deciding what to
    do about a listed account (contact, restrict, link to a family, escalate)
    is a staff judgment call this endpoint doesn't make for them.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "child_safety.review"

    def get(self, request):
        qs = _unsupervised_under_13_queryset()[:250]
        today = timezone.now().date()
        results = []
        for user in qs:
            age = today.year - user.date_of_birth.year - (
                (today.month, today.day) < (user.date_of_birth.month, user.date_of_birth.day)
            )
            results.append({
                "id": str(user.id),
                "display_name": user.display_name,
                "phone": user.phone,
                "date_of_birth": user.date_of_birth.isoformat(),
                "age": age,
                "country": user.country,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            })
        return Response({"results": results, "count": len(results)})
