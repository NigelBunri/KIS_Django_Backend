# Verification Provider Sandbox Runbook

Use this before enabling live verification providers. Do not paste provider
secrets, raw IDs, passport images, document scans, service account JSON, or live
tokens into this repository.

## Evidence Header

Record this in the release ticket:

- Tester: `TODO_TESTER`
- Date: `TODO_DATE`
- Environment: `TODO_STAGING_OR_PRODUCTION_LIKE_ENV`
- Django release/build: `TODO_DJANGO_RELEASE`
- React Native build: `TODO_RN_BUILD`
- Evidence ticket: `TODO_EVIDENCE_TICKET`

## Local Non-Secret Provider Readiness

Run from the Django backend:

```bash
python3 manage.py verification_provider_readiness
```

Expected result:

- `dojah`, `sumsub`, and `smile_id` appear.
- `configured=false` is acceptable locally.
- In production-like staging, the selected provider should show
  `configured=true` after secrets are installed in the provider secret manager.
- Output must not include API keys, app tokens, partner IDs, or webhook secrets.

## Provider Decision Matrix

| Subject | Primary Provider | Fallback | Manual Review Required | Notes |
| ------- | ---------------- | -------- | ---------------------- | ----- |
| User ID/liveness | Dojah or Sumsub | Smile ID where country coverage fits | Yes for launch | Use provider only for identity/liveness; app stores private evidence refs only. |
| Shop KYB | Sumsub or manual registry review | Dojah where business checks fit | Yes | Keep legacy shop verification endpoint until centralized shop start endpoint replaces it. |
| Partner/company KYB | Sumsub | Manual registry review | Yes | Include representative authorization and beneficial-owner metadata. |
| Health institution | Manual license/accreditation review first | Sumsub/Dojah where applicable | Yes | Verify medical license/accreditation expiry before badge issuance. |
| Education institution | Manual accreditation/certificate review first | Sumsub/Dojah where applicable | Yes | Verify issuer trust and expiry before badge issuance. |

## Dojah Sandbox Checklist

- [ ] Provider sandbox account exists.
- [ ] Sandbox keys are stored in secret manager, not source.
- [ ] `DOJAH_APP_ID`, `DOJAH_SECRET_KEY`, and `DOJAH_BASE_URL` are configured in staging.
- [ ] `python3 manage.py verification_provider_readiness` shows `dojah: configured=true`.
- [ ] User ID case can be started in staging without raw document storage.
- [ ] Failed/invalid sandbox case records provider status safely.
- [ ] Provider callback is sent to the staging webhook URL.
- [ ] Callback signature is verified.
- [ ] Audit event appears in `/api/v1/verification/staff/provider-callbacks/`.

## Sumsub Sandbox Checklist

- [ ] Sandbox app exists.
- [ ] Sandbox token/secret are stored in secret manager, not source.
- [ ] `SUMSUB_APP_TOKEN`, `SUMSUB_SECRET_KEY`, and `SUMSUB_BASE_URL` are configured in staging.
- [ ] `python3 manage.py verification_provider_readiness` shows `sumsub: configured=true`.
- [ ] Applicant/case reference maps to a KIS `VerificationCase`.
- [ ] Rejected sandbox case does not issue badges.
- [ ] Approved sandbox case still waits for staff review before public badge display unless launch policy changes.
- [ ] Callback payload is recorded as an audit event with safe metadata only.

## Smile ID Sandbox Checklist

- [ ] Sandbox partner account exists.
- [ ] Sandbox API key is stored in secret manager, not source.
- [ ] `SMILE_ID_PARTNER_ID`, `SMILE_ID_API_KEY`, and `SMILE_ID_BASE_URL` are configured in staging.
- [ ] `python3 manage.py verification_provider_readiness` shows `smile_id: configured=true`.
- [ ] Country coverage is confirmed for launch countries.
- [ ] Callback replay/signature behavior is documented in the release ticket.
- [ ] Smile ID is configured as fallback only until subject-specific mapping is implemented.

## Webhook Replay And Signature Validation

Use a known safe sandbox payload. Never use real production PII in local command
history.

```bash
python3 manage.py verification_webhook_signature_check \
  --payload '{"event":"sandbox","case":"safe-test"}' \
  --signature 'sha256=TODO_SIGNATURE_FROM_SANDBOX_SECRET'
```

Expected result:

- Valid payload/signature prints `signature_valid=true reason=ok`.
- Invalid payload/signature exits non-zero and does not print secrets.
- Missing `VERIFICATION_WEBHOOK_SECRET` exits non-zero.

## Staff Review Console QA

- [ ] Staff user can open `GET /api/v1/verification/staff/cases/`.
- [ ] Non-staff user receives 403.
- [ ] Staff can filter by `subject_type`, `status`, `provider`, and `q`.
- [ ] Case detail shows evidence shape, not raw file contents.
- [ ] Staff can mark case `in_review`.
- [ ] Staff can request `needs_more_info`.
- [ ] Staff can issue an allowed badge.
- [ ] Staff can revoke a badge with a reason.
- [ ] All actions create audit events.

## Go / No-Go

Do not enable live provider calls until:

- provider sandbox evidence is attached to the release ticket;
- webhook signature validation passes;
- private media access denies unauthenticated and non-owner access;
- staff review queue QA passes;
- badge display QA passes;
- rollback plan is linked;
- provider secret rotation owner is assigned.
