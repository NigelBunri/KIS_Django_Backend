# Verification Launch QA Checklist

This checklist covers verification launch evidence across backend, React Native,
provider sandbox, private media, staff review, and badge display. Store evidence
in a release ticket; do not paste secrets or raw documents here.

## Required Command Gates

Django backend:

```bash
python3 -m py_compile apps/verification/providers.py apps/verification/management/commands/verification_provider_readiness.py apps/verification/management/commands/verification_webhook_signature_check.py
python3 manage.py check
python3 manage.py makemigrations verification --check --dry-run
python3 manage.py verification_provider_readiness
```

React Native:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck -- --pretty false
npx eslint . --quiet
npm run ci:launch
```

## Private Media Evidence

Before launch, verify each evidence metadata reference points to private media
only:

- [ ] Unauthenticated request is denied.
- [ ] Non-owner/non-staff request is denied.
- [ ] Owner or authorized staff request succeeds through signed/proxy path.
- [ ] Public CDN URL does not expose identity/business/license documents.
- [ ] Signed URL TTL is short and documented.
- [ ] Malware/quarantine status is visible before staff approval.
- [ ] Verification case payload stores only references and metadata shape.

## Badge Display QA

User profile:

- [ ] Unverified user shows unverified/ready status.
- [ ] Verified user shows verified badge.
- [ ] Revoked badge disappears from public UI after refresh.

Shop:

- [ ] Shop card shows verified/trusted badges when active.
- [ ] Shop verification sheet opens from management UI.
- [ ] Revoked shop badge is not shown after refresh.

Partner:

- [ ] Partner workspace shows verification status card.
- [ ] Partner badge row appears only for active public badges.
- [ ] Staff-issued official partner badge appears after refresh.

Health institution:

- [ ] Health management area shows verification card.
- [ ] Licensed provider badge appears after approval.
- [ ] Expired/revoked license badge does not display publicly.

Education institution:

- [ ] Education workspace overview shows verification status.
- [ ] Accredited education badge appears after approval.
- [ ] Expired/revoked accreditation badge does not display publicly.

## Staff Review Console QA

- [ ] Staff queue loads cases across all subject types.
- [ ] Search/filter works for status, provider, subject type, and UUID.
- [ ] Case detail does not expose raw evidence blobs.
- [ ] Provider callback inspection shows webhook audit events.
- [ ] Suspicious signals endpoint returns aggregate signals without PII-heavy dumps.
- [ ] Badge issue creates audit event.
- [ ] Badge revoke creates audit event and removes public badge.
- [ ] Expiry reminders list upcoming cases/badges.
- [ ] Expire overdue badges dry-run shows counts without changing records.
- [ ] Expire overdue badges with `dry_run=false` changes only overdue active badges.

## Expiry Reminder Notification Planning

Phase 9 does not dispatch reminders. Before production reminder dispatch:

- [ ] Decide reminder windows: recommended 30, 14, 7, and 1 day before expiry.
- [ ] Decide channels: in-app notification first, push notification optional.
- [ ] Ensure notification text contains no document numbers or private identifiers.
- [ ] Add idempotency key per subject/badge/window.
- [ ] Add owner/staff recipient rules per subject type.
- [ ] Add opt-out rules where legally required.
- [ ] Add SIEM/alert hook for high-risk expired health/education licenses.

## Production Rollout Rules

Launch order:

1. Deploy backend staff/admin APIs with live provider calls disabled.
2. Deploy frontend badge display and verification center.
3. Run staff review and badge display QA in staging.
4. Configure provider sandbox secrets in staging.
5. Run webhook replay/signature checks.
6. Enable one provider and one low-risk subject type in staging.
7. Review audit events and rollback readiness.
8. Enable production provider calls only after explicit sign-off.

Rollback:

- Disable provider feature flag / unset provider secrets.
- Keep manual staff review available.
- Revoke incorrectly issued badges through staff API.
- Mark affected cases `needs_more_info` or `cancelled`.
- Attach audit event export to incident ticket.

## Evidence To Attach To Release Ticket

- Command gate outputs.
- Provider readiness output with no secrets.
- Webhook signature replay result.
- Private media deny/allow proof.
- Staff queue screenshots.
- Badge display screenshots for all subject types.
- Expiry reminder output.
- Rollback drill notes.
