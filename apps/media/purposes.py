# apps/media/purposes.py
"""
Declarative media-purpose registry — Phase 1 & 2 of the KIS Universal Media
Platform (see the platform architecture document for the full design).

Phase 1 registered, without changing, the behavior of every currently
active presigned-upload context (apps.media.upload_intent.UPLOAD_CONTEXTS).
MIME allowlists and size limits are still resolved lazily through
upload_intent.py's existing per-context functions — the single source of
truth those functions already were, including their settings-override-
ability in tests — never re-declared here as frozen values that could
drift out of sync with the functions that actually gate initiate()/confirm().

Phase 2 adds the hook surface a generic attach/access/detach dispatch needs:
`target_types` / `allow_attach` / `allow_reuse` / `allow_delete` /
`visibility_class` are static, declared once at register_purpose() time.
`authorize_target` / `access_authorizer` / `attach_handler` / `detach_handler`
are callables, registered SEPARATELY via register_target_authorizer() /
register_access_authorizer() / register_attach_handler() /
register_detach_handler() — never as raw import-path strings executed
dynamically. This split exists because purposes.py is imported by
apps/media (which loads early in INSTALLED_APPS) while the hook
implementations live in the owning feature apps (accounts/commerce/statuses),
which register them from their own AppConfig.ready() — see each app's
apps.py. Hook registration is append-only and raises on a duplicate, exactly
like register_purpose() itself.

Adding a new purpose in a future phase means one UploadContextConfig entry
in upload_intent.py (as today), one register_purpose() call here, and
(if it supports generic attach) hook registrations in the owning app's
ready() — nothing else changes, per the platform's "config over code"
principle.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable

from . import upload_intent


@dataclass(frozen=True)
class MediaPurpose:
    name: str
    context: str
    allowed_mime_types: tuple[str, ...]
    max_bytes: int
    key_prefix: str
    moderation_context: str
    retention_days: int | None
    requires_processing: tuple[str, ...] = ()

    # Phase 2: static attachment/visibility declarations.
    # target_types: the targetType strings the generic attach endpoint will
    # accept for this purpose (e.g. ("commerce.Product",)). Empty means the
    # generic attach endpoint has nothing to validate against because...
    target_types: tuple[str, ...] = ()
    # ...allow_attach is False: this purpose's media is bound to its owning
    # resource at CREATE time (status posts, marketplace complaints) or at
    # CONFIRM time (profile avatar/cover auto-attach), not via a later,
    # separate attach call to an already-existing target. The generic
    # attach endpoint still handles a *repeat* call for these purposes
    # (idempotent success/rejection based on the existing attachment), it
    # just never performs a *fresh* attach for them — see
    # apps/media/services/attachments.py.
    allow_attach: bool = False
    # allow_reuse: whether one confirmed asset may be attached to more than
    # one target. None of the 11 Phase 1 purposes need this — every one is
    # single-use, matching resolve_confirmed_intent's existing
    # attached_at-must-be-NULL enforcement.
    allow_reuse: bool = False
    allow_delete: bool = True
    # visibility_class is documentation + a dispatch aid (e.g. for tests
    # asserting a purpose's intended audience) — actual enforcement always
    # happens through access_authorizer, never inferred from this alone.
    visibility_class: str = "private"  # "public_catalog" | "restricted" | "private"

    attach_target: str | None = None  # Phase 1 field, kept for docs/back-compat

    authorize_target: Callable | None = None
    access_authorizer: Callable | None = None
    attach_handler: Callable | None = None
    detach_handler: Callable | None = None


@dataclass(frozen=True)
class _PurposeSpec:
    """Static, settings-independent metadata for one purpose. Kept separate
    from MediaPurpose itself so MIME/size values are never captured at
    import time — they're merged in from upload_intent.UPLOAD_CONTEXTS'
    live callables on every get_purpose() call, so a test's
    override_settings(...) still affects what the registry reports, exactly
    like it already affects validate_initiate_payload()."""

    context: str
    moderation_context: str
    retention_days: int | None
    requires_processing: tuple[str, ...] = ()
    target_types: tuple[str, ...] = ()
    allow_attach: bool = False
    allow_reuse: bool = False
    allow_delete: bool = True
    visibility_class: str = "private"
    attach_target: str | None = None
    authorize_target: Callable | None = None
    access_authorizer: Callable | None = None
    attach_handler: Callable | None = None
    detach_handler: Callable | None = None


_REGISTRY: dict[str, _PurposeSpec] = {}

_HOOK_FIELDS = ("authorize_target", "access_authorizer", "attach_handler", "detach_handler")


def register_purpose(name: str, spec: _PurposeSpec) -> None:
    """Append-only: raises if `name` has no matching UPLOAD_CONTEXTS entry
    (the registry describes existing contexts, it doesn't invent new ones),
    or if `name` is already registered. There is no unregister/overwrite —
    the registry is built once, by the module-level calls at the bottom of
    this file, not mutated by callers at runtime."""
    if name not in upload_intent.UPLOAD_CONTEXTS:
        raise ValueError(
            f"Cannot register purpose {name!r}: no matching entry in "
            f"apps.media.upload_intent.UPLOAD_CONTEXTS. Add the UploadContextConfig "
            f"there first — the registry describes existing contexts, it doesn't invent them."
        )
    if name in _REGISTRY:
        raise ValueError(f"Purpose {name!r} is already registered.")
    _REGISTRY[name] = spec


def _set_hook(name: str, field: str, fn: Callable) -> None:
    if name not in _REGISTRY:
        raise ValueError(
            f"Cannot register a {field} for unknown purpose {name!r} — call "
            f"register_purpose() for it first."
        )
    if not callable(fn):
        raise TypeError(f"{field} for purpose {name!r} must be callable, got {fn!r}.")
    spec = _REGISTRY[name]
    if getattr(spec, field) is not None:
        raise ValueError(
            f"Purpose {name!r} already has a {field} registered — hook "
            f"registration is append-only, there is no overwrite."
        )
    _REGISTRY[name] = dataclasses.replace(spec, **{field: fn})


def register_target_authorizer(name: str, authorizer: Callable) -> None:
    """authorizer(*, user, target_type: str, target_id: str) -> None,
    raising PermissionDenied/NotFound/ValidationError on rejection.
    Verifies the caller may attach media onto this specific target instance
    (e.g. "does this user own this shop") — separate from
    access_authorizer, which governs *viewing* already-attached media."""
    _set_hook(name, "authorize_target", authorizer)


def register_attach_handler(name: str, handler: Callable) -> None:
    """handler(*, user, asset, target_type: str, target_id: str,
    slot: str | None, position: int | None) -> None. Performs the actual
    feature-model binding (mirrors the purpose's existing attach_* function
    where one exists — this is a thin adapter calling it, never a second
    implementation). Only meaningful when allow_attach=True."""
    _set_hook(name, "attach_handler", handler)


def register_access_authorizer(name: str, authorizer: Callable) -> None:
    """authorizer(user, asset: MediaAsset) -> AccessDecision (see
    apps.media.services.access). Governs whether `user` may view/download
    this already-attached (or being-attached) asset."""
    _set_hook(name, "access_authorizer", authorizer)


def register_detach_handler(name: str, handler: Callable) -> None:
    """handler(*, user, asset) -> None. Performs feature-side cleanup when
    media is deleted/detached (e.g. clearing a FK, removing a gallery row).
    Optional — purposes with allow_delete=True but no detach_handler are
    detached generically (asset marked deleted, feature model untouched)."""
    _set_hook(name, "detach_handler", handler)


def get_purpose(name: str) -> MediaPurpose:
    spec = _REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"Unknown media purpose: {name!r}")
    config = upload_intent.UPLOAD_CONTEXTS[name]
    return MediaPurpose(
        name=name,
        context=spec.context,
        allowed_mime_types=tuple(sorted(config.allowed_content_types())),
        max_bytes=config.max_bytes(),
        key_prefix=config.key_prefix,
        moderation_context=spec.moderation_context,
        retention_days=spec.retention_days,
        requires_processing=spec.requires_processing,
        target_types=spec.target_types,
        allow_attach=spec.allow_attach,
        allow_reuse=spec.allow_reuse,
        allow_delete=spec.allow_delete,
        visibility_class=spec.visibility_class,
        attach_target=spec.attach_target,
        authorize_target=spec.authorize_target,
        access_authorizer=spec.access_authorizer,
        attach_handler=spec.attach_handler,
        detach_handler=spec.detach_handler,
    )


def list_purposes() -> list[MediaPurpose]:
    return [get_purpose(name) for name in _REGISTRY]


def purpose_names() -> frozenset[str]:
    return frozenset(_REGISTRY)


# --------------------------------------------------------------------------
# Registrations — one entry per context in apps.media.upload_intent.UPLOAD_CONTEXTS.
# A test (apps/media/test_purposes.py) asserts purpose_names() exactly
# equals UPLOAD_CONTEXTS.keys(), so a future new context can never be added
# to UPLOAD_CONTEXTS without also being registered here.
#
# Hooks (authorize_target/access_authorizer/attach_handler/detach_handler)
# are NOT set here — they're registered by the owning feature app's
# AppConfig.ready(): apps/accounts/apps.py, apps/commerce/apps.py,
# apps/statuses/apps.py. This keeps apps/media free of imports into
# apps/accounts|commerce|statuses (avoiding circular imports) while still
# giving every purpose exactly one place its hooks are declared.
# --------------------------------------------------------------------------

register_purpose("profile_avatar", _PurposeSpec(
    context="profile",
    moderation_context="profile",
    retention_days=None,  # permanent until replaced — no TTL today
    attach_target="accounts.Profile.avatar_file",
    target_types=("accounts.Profile",),
    allow_attach=False,  # auto-attaches at confirm time — see _attach_profile_avatar
    visibility_class="restricted",  # visible to any authenticated-or-anonymous viewer of the profile (IsAuthenticatedOrReadOnly), not fully public-catalog
))
register_purpose("profile_cover", _PurposeSpec(
    context="profile",
    moderation_context="profile",
    retention_days=None,
    attach_target="accounts.Profile.cover_file",
    target_types=("accounts.Profile",),
    allow_attach=False,
    visibility_class="restricted",
))

# Commerce attach targets are dynamic (which shop/product/service instance
# is chosen at attach time, per apps.commerce.media_uploads) rather than a
# single fixed model field — attach_target is left None to reflect that
# honestly rather than naming just one of several possible targets.
register_purpose("commerce_shop_image", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.Shop",), allow_attach=True, visibility_class="public_catalog",
))
register_purpose("commerce_product_main_image", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.Product",), allow_attach=True, visibility_class="public_catalog",
))
register_purpose("commerce_product_gallery_image", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.Product",), allow_attach=True, allow_reuse=False,
    visibility_class="public_catalog",
))
register_purpose("commerce_service_image", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.ShopService",), allow_attach=True, visibility_class="public_catalog",
))
register_purpose("commerce_service_gallery_image", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.ShopService",), allow_attach=True, visibility_class="public_catalog",
))
# Complaint attachments are created ALONGSIDE a new MarketplaceComplaint
# (apps.commerce.media_uploads.attach_complaint_media creates the complaint
# row itself) — there is no pre-existing complaint to attach to, so this is
# a create-with-media purpose like status, not an attach-to-existing one.
# Evidence, so deletion stays disabled (retention over convenience).
register_purpose("commerce_complaint_attachment", _PurposeSpec(
    context="commerce", moderation_context="commerce", retention_days=None,
    target_types=("commerce.MarketplaceComplaint",), allow_attach=False,
    allow_delete=False, visibility_class="restricted",
))

# Status media retention is governed by StatusItem.expires_at (tier-based
# retention_days, see apps.statuses.views.perform_create), not by anything
# at the media-asset layer — no cleanup task deletes an expired status's
# underlying file today (a known, documented, pre-existing gap; not
# invented or fixed here). retention_days=None reflects that honestly
# rather than asserting an enforcement that doesn't exist. Status media is
# also create-with-media (StatusCreateSerializer.create binds the file when
# the StatusItem itself is created) — allow_attach=False for the same
# reason as complaint attachments.
register_purpose("status_image", _PurposeSpec(
    context="status", moderation_context="status", retention_days=None,
    target_types=("statuses.StatusItem",), allow_attach=False, visibility_class="restricted",
))
register_purpose("status_video", _PurposeSpec(
    context="status", moderation_context="status", retention_days=None,
    target_types=("statuses.StatusItem",), allow_attach=False, visibility_class="restricted",
))
register_purpose("status_audio", _PurposeSpec(
    context="status", moderation_context="status", retention_days=None,
    target_types=("statuses.StatusItem",), allow_attach=False, visibility_class="restricted",
))

# Education (Phase 3 of the Education System cleanup project). Like status
# and complaint attachments, these bind at CREATE/UPDATE time on the owning
# record (see apps.broadcasts.education_media), not via a later generic
# attach call — allow_attach=False. target_types lists every model these
# purposes can end up bound to (institution branding logo; program/course/
# lesson/class-session/material/assessment/event cover images; material
# resource attachments) purely for documentation — actual authorization is
# institution-membership-based (owner/manager/administrator), not a single
# fixed FK, mirroring commerce's Shop-owner-or-staff pattern.
register_purpose("education_institution_logo", _PurposeSpec(
    context="education", moderation_context="education", retention_days=None,
    target_types=("broadcasts.EducationInstitution",), allow_attach=False,
    visibility_class="public_catalog",
))
register_purpose("education_module_cover_image", _PurposeSpec(
    context="education", moderation_context="education", retention_days=None,
    target_types=(
        "broadcasts.EducationInstitutionProgram",
        "broadcasts.EducationInstitutionCourse",
        "broadcasts.EducationInstitutionLesson",
        "broadcasts.EducationInstitutionClassSession",
        "broadcasts.EducationInstitutionMaterial",
        "broadcasts.EducationInstitutionAssessment",
        "broadcasts.EducationInstitutionEvent",
    ),
    allow_attach=False, visibility_class="public_catalog",
))
register_purpose("education_material", _PurposeSpec(
    context="education", moderation_context="education", retention_days=None,
    target_types=("broadcasts.EducationInstitutionMaterial",), allow_attach=False,
    visibility_class="restricted",
))
register_purpose("testimony_media", _PurposeSpec(
    context="testimony", moderation_context="testimony", retention_days=None,
    target_types=("testimony.UserTestimony",), allow_attach=False,
    visibility_class="restricted",
))

# Partner task reports (apps.tasks) — an assignee attaches evidence of
# completed work when submitting a task via TaskSubmitView, which binds the
# already-confirmed asset directly (same create-with-media shape as status/
# complaint attachments above), not through the generic attach endpoint.
register_purpose("task_report", _PurposeSpec(
    context="tasks", moderation_context="tasks", retention_days=None,
    target_types=("tasks.Task",), allow_attach=False,
    visibility_class="restricted",
))
register_purpose("partner_resource", _PurposeSpec(
    context="partners", moderation_context="partners", retention_days=None,
    target_types=("partners.PartnerResource",), allow_attach=False,
    visibility_class="restricted",
))
