"""
Idempotent launch seed for KIS Books:
  - the current Christian Publishing Policy version (required before any
    book can be submitted for review — see services.submit_for_review).
  - four AdminRole presets scoped to app_label="books", assignable to any
    GO/staff user via the existing admin_control roles UI:
      Book Reviewer       -> books.review
      Book Moderator      -> books.review + books.restrict
      Book Administrator  -> books.review + books.restrict + books.ban_user
      Book Finance Officer -> books.refund
Safe to run repeatedly (get_or_create throughout).
"""
from django.core.management.base import BaseCommand

POLICY_BODY = """# KIS Books — Christian Publishing Policy (v1.0)

KIS Books is dedicated to Christian literature and resources that align with
our Christian publishing policy. All submitted books are subject to review
before publication. Uploading a book does not guarantee approval or
publication. Books that do not meet our policy may be rejected, removed, or
restricted. Serious or repeated violations may result in account
restrictions or suspension.

## Eligible categories

Bible studies & biblical commentary; Christian theology & doctrine;
discipleship & spiritual growth; prayer, devotionals & meditation; Christian
biography, testimony & ministry history; Christian fiction & storytelling;
Christian education & children's literature; marriage, family &
relationships; Christian leadership & ministry; apologetics & faith-related
scholarship; Christian & church history; Christian counseling & pastoral
care; Christian living & practical faith; worship, hymns & related
education; and other literature that legitimately falls within KIS's
Christian publishing mission.

A book is not approved merely because it contains the word "God," quotes a
Bible verse, or uses Christian imagery — nor is it automatically rejected
for discussing difficult subjects, differing theological interpretations,
historical controversies, criticism, abuse, or grief. Reviewers evaluate
content against this policy, its context, and KIS's publishing standards.

## Rights & copyright

You must own the content or hold sufficient rights to publish it. KIS
cannot and does not guarantee that review or automated checks prove legal
ownership or theological compliance — human judgment governs every
difficult case.
"""


class Command(BaseCommand):
    help = "Seed the current Christian Publishing Policy version and Books admin roles."

    def handle(self, *args, **options):
        from apps.books.models import ChristianPublishingPolicyVersion

        policy, created = ChristianPublishingPolicyVersion.objects.get_or_create(
            version="1.0",
            defaults={"title": "KIS Books Christian Publishing Policy", "body_markdown": POLICY_BODY, "is_current": True},
        )
        if not created and not policy.is_current:
            ChristianPublishingPolicyVersion.objects.exclude(id=policy.id).update(is_current=False)
            policy.is_current = True
            policy.save(update_fields=["is_current"])
        self.stdout.write(self.style.SUCCESS(f"Policy version {policy.version} ready (current={policy.is_current})."))

        from admin_control.roles import AdminRole, AdminRolePermission

        presets = [
            ("Book Reviewer", ["books.review"]),
            ("Book Moderator", ["books.review", "books.restrict"]),
            ("Book Administrator", ["books.review", "books.restrict", "books.ban_user"]),
            # Deliberately its own role, not folded into Book Administrator —
            # content moderation and financial refund authority are
            # different responsibilities (see permissions.py's docstring).
            ("Book Finance Officer", ["books.refund"]),
        ]
        for name, perms in presets:
            role, _ = AdminRole.objects.get_or_create(name=name, defaults={"description": f"Books moderation — {name}"})
            perm_row, _ = AdminRolePermission.objects.get_or_create(role=role, app_label="books", defaults={"permissions": perms})
            if set(perm_row.permissions or []) != set(perms):
                perm_row.permissions = perms
                perm_row.save(update_fields=["permissions"])
            self.stdout.write(self.style.SUCCESS(f"Role '{name}' -> {perms}"))
