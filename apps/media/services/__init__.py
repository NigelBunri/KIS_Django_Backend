# apps/media/services/
"""
Phase 2 service layer for the KIS Universal Media Platform.

Views (apps/media/views.py) and any future feature app call into these
modules rather than re-implementing ownership/confirmation/lifecycle
checks. Each module owns one concern:

  lifecycle.py    — attachable/downloadable/deletable predicates, the
                     confirmed-intent <-> canonical-asset field sync, and
                     the cancel/delete state transitions.
  access.py       — the one access chokepoint (can_user_access_media) and
                     signed-URL issuance.
  attachments.py  — the generic attach endpoint's full dispatch flow.

None of these modules import apps.accounts/apps.commerce/apps.statuses —
feature-specific behavior only ever reaches this layer through hooks
registered on apps.media.purposes (register_target_authorizer,
register_attach_handler, register_access_authorizer,
register_detach_handler), each feature app registering its own from its
own AppConfig.ready(). This keeps the dependency direction one-way
(feature apps depend on apps.media, never the reverse) and is what makes
the hook registration safe against circular imports.
"""
