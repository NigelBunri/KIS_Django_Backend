"""
Admin-triggered device wipes — GO/admin_control operations distinct from the
user-initiated apps.accounts.views.RevokeAllSecondaryView, which only lets a
parent device revoke its own secondaries. These helpers let an admin reset
ANY account's device history (including the parent), so the account's next
login registers a brand-new parent device with no pairing/secondary-code
prompt, instead of being treated as a new secondary device needing to pair
against a stale parent record.

Deleting the Device rows (rather than only revoking) is deliberate: token_version
bumps invalidate live JWTs, but a still-present Device row would still make the
next login look like "adding a secondary device" to a pre-existing parent.
Removing the rows entirely is what makes the next login a fresh registration.
"""
from __future__ import annotations

from .models import AuditLog, Device


def wipe_devices_for_user(user, *, actor, reason: str) -> dict:
    """Revoke (invalidate JWTs/E2EE keys) then delete every Device row for one user."""
    from .views import revoke_device_session

    devices = list(Device.objects.filter(user=user))
    for device in devices:
        if device.revoked_at is None:
            revoke_device_session(user, device, reason=reason)
    device_ids = [d.device_id for d in devices]
    Device.objects.filter(user=user).delete()

    AuditLog.log(
        actor=actor,
        action="device.admin_wipe_user",
        meta={
            "target_user_id": str(user.id),
            "deleted_count": len(device_ids),
            "deleted_device_ids": device_ids,
            "reason": reason,
        },
    )
    return {"user_id": str(user.id), "deleted_count": len(device_ids)}


def wipe_all_devices(*, actor, reason: str) -> dict:
    """Same as wipe_devices_for_user, applied across every account on the platform."""
    from .models import User
    from .views import revoke_device_session

    users_affected = 0
    devices_deleted = 0
    for user in User.objects.all().iterator():
        devices = list(Device.objects.filter(user=user))
        if not devices:
            continue
        for device in devices:
            if device.revoked_at is None:
                revoke_device_session(user, device, reason=reason)
        Device.objects.filter(user=user).delete()
        users_affected += 1
        devices_deleted += len(devices)

    result = {"users_affected": users_affected, "devices_deleted": devices_deleted}
    AuditLog.log(
        actor=actor,
        action="device.admin_wipe_all_platform",
        meta={**result, "reason": reason},
    )
    return result
