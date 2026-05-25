"""
Management command: setup_kcan_superadmin

Creates the KCAN super-admin user (nigel / nigelbunribah@gmail.com) and
ensures he is the owner of the KCAN partner organisation.  Also assigns
him a super-admin role inside admin_control so that the full admin
control dashboard is accessible.

Usage:
    KCAN_SUPERADMIN_PASSWORD=<secret> python manage.py setup_kcan_superadmin
    python manage.py setup_kcan_superadmin --password <secret>

The password must be supplied either via the KCAN_SUPERADMIN_PASSWORD
environment variable or the --password flag.  No default is provided.
Running without a password in production will abort with an error.
In DEBUG mode a warning is printed and the command proceeds only when
the user account already exists (password is not changed for existing accounts).
"""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)

SUPERADMIN_EMAIL = "nigelbunribah@gmail.com"
SUPERADMIN_PHONE = "+237654008266"
SUPERADMIN_USERNAME = "nigel"
SUPERADMIN_DISPLAY_NAME = "Nigel — GO"
SUPERADMIN_COUNTRY = "CM"
SUPERADMIN_TIER = "Partner Pro"
KCAN_PARTNER_SLUG = "kcan"
SUPER_ROLE_NAME = "super_admin"


class Command(BaseCommand):
    help = "Create / update the KCAN super-admin user (nigel) and wire up KCAN ownership."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=None,
            help="Initial password for the super-admin user. Overrides KCAN_SUPERADMIN_PASSWORD env var.",
        )

    def handle(self, *args, **options):
        password = options.get("password") or os.environ.get("KCAN_SUPERADMIN_PASSWORD")

        if not password:
            from apps.accounts.models import User
            user_exists = (
                User.objects.filter(email__iexact=SUPERADMIN_EMAIL).exists()
                or User.objects.filter(username__iexact=SUPERADMIN_USERNAME).exists()
            )
            if not user_exists:
                if not getattr(settings, "DEBUG", False):
                    raise CommandError(
                        "No password provided. Set KCAN_SUPERADMIN_PASSWORD or pass --password. "
                        "Refusing to create an account without a password in production."
                    )
                raise CommandError(
                    "No password provided. Set KCAN_SUPERADMIN_PASSWORD or pass --password."
                )
            # Existing account: password is not changed — safe to continue without one.
            self.stdout.write(self.style.WARNING(
                "[setup_kcan_superadmin] No password provided. "
                "Existing account will be updated without changing the password."
            ))

        with transaction.atomic():
            user = self._ensure_user(password)
            partner = self._ensure_kcan_partner(user)
            self._ensure_admin_role(user)
            self._ensure_kcan_verified(partner, user)
        self.stdout.write(self.style.SUCCESS(
            f"[setup_kcan_superadmin] Done — user: {user.email} | partner: {partner.slug} | role: {SUPER_ROLE_NAME}"
        ))

    # ------------------------------------------------------------------
    def _ensure_user(self, password: str):
        from apps.accounts.models import User

        user = (
            User.objects.filter(email__iexact=SUPERADMIN_EMAIL).first()
            or User.objects.filter(phone=SUPERADMIN_PHONE).first()
            or User.objects.filter(username__iexact=SUPERADMIN_USERNAME).first()
        )

        if user:
            changed = []
            if not user.is_superuser:
                user.is_superuser = True
                changed.append("is_superuser")
            if not user.is_staff:
                user.is_staff = True
                changed.append("is_staff")
            if user.tier != SUPERADMIN_TIER:
                user.tier = SUPERADMIN_TIER
                changed.append("tier")
            if not user.email:
                user.email = SUPERADMIN_EMAIL
                changed.append("email")
            if not user.phone:
                user.phone = SUPERADMIN_PHONE
                changed.append("phone")
            if not user.username:
                user.username = SUPERADMIN_USERNAME
                changed.append("username")
            if not user.display_name:
                user.display_name = SUPERADMIN_DISPLAY_NAME
                changed.append("display_name")
            if user.country != SUPERADMIN_COUNTRY:
                user.country = SUPERADMIN_COUNTRY
                changed.append("country")
            if changed:
                user.save(update_fields=changed)
            self.stdout.write(f"  [user] existing — {user.email} (updated: {changed or 'none'})")
        else:
            if not password:
                raise CommandError("Cannot create a new account without a password.")
            user = User.objects.create_superuser(
                email=SUPERADMIN_EMAIL,
                password=password,
                phone=SUPERADMIN_PHONE,
                country=SUPERADMIN_COUNTRY,
                username=SUPERADMIN_USERNAME,
                display_name=SUPERADMIN_DISPLAY_NAME,
                tier=SUPERADMIN_TIER,
            )
            # Re-apply tier after post_save signals that may override it.
            if user.tier != SUPERADMIN_TIER:
                User.objects.filter(id=user.id).update(tier=SUPERADMIN_TIER)
                user.tier = SUPERADMIN_TIER
            self.stdout.write(f"  [user] created — {user.email}")

        return user

    def _ensure_kcan_partner(self, owner):
        from apps.partners.models import Partner, PartnerMembership

        partner = Partner.objects.filter(slug=KCAN_PARTNER_SLUG).first()
        if not partner:
            partner = Partner.objects.create(
                slug=KCAN_PARTNER_SLUG,
                name="KCAN, Kingdom Citizens & Ambassadors Network",
                owner=owner,
                is_active=True,
            )
            self.stdout.write(f"  [partner] created — {partner.slug}")
        else:
            if partner.owner_id != owner.id:
                partner.owner = owner
                partner.save(update_fields=["owner"])
                self.stdout.write(f"  [partner] owner updated → {owner.email}")
            else:
                self.stdout.write(f"  [partner] already owned by {owner.email}")

        PartnerMembership.objects.update_or_create(
            partner=partner,
            user=owner,
            defaults={"status": "member", "role": "admin"},
        )
        return partner

    def _ensure_kcan_verified(self, partner, owner):
        from apps.partners.seed import ensure_kcan_verified
        ensure_kcan_verified()
        self.stdout.write(f"  [verification] KCAN badges ensured")

    def _ensure_admin_role(self, user):
        from admin_control.roles import AdminRole, AdminRolePermission, AdminRoleAssignment

        role, created = AdminRole.objects.get_or_create(
            name=SUPER_ROLE_NAME,
            defaults={"description": "Full-access super admin — GO of KCAN.", "is_super_role": True},
        )
        if not role.is_super_role:
            role.is_super_role = True
            role.save(update_fields=["is_super_role"])

        AdminRolePermission.objects.get_or_create(
            role=role,
            app_label=AdminRolePermission.ALLOWED_ALL,
            defaults={"permissions": [AdminRolePermission.ALLOWED_ALL]},
        )

        assignment, created = AdminRoleAssignment.objects.get_or_create(
            user=user,
            role=role,
            defaults={"is_active": True},
        )
        if not assignment.is_active:
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])

        self.stdout.write(f"  [role] {'created' if created else 'confirmed'} — {role.name} → {user.email}")
