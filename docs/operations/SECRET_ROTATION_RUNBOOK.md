# Secret Rotation Runbook

Use this for planned or emergency rotation of credentials. Do not paste secret
values into this document, chat, tickets, logs, screenshots, or code.

## Ownership

- Secret owner: `TODO_SECRET_OWNER`
- Secret manager path/project: `TODO_SECRET_MANAGER_PATH`
- Approver: `TODO_SECRET_ROTATION_APPROVER`
- Incident channel: `TODO_ESCALATION_CHANNEL`

## Secrets Covered

- Django `SECRET_KEY`.
- Django `JWT_SECRET`.
- Django/Nest internal tokens.
- Nest `DJANGO_JWT_SECRET`.
- Database URL/password.
- Redis URL/password.
- Firebase service account.
- Firebase/mobile config.
- Payment provider keys/webhook secrets.
- SMS/OTP provider keys.
- AI provider keys.
- Object storage credentials.

## Planned Rotation Steps

1. Identify secret and dependent services.
2. Create new secret value in secret manager.
3. If provider supports dual keys, enable old and new keys temporarily.
4. Update staging first.
5. Run staging smoke tests.
6. Update production secret manager/provider env.
7. Restart affected services.
8. Run production verifiers:

```bash
python3 manage.py verify_deployment_security --target-production --strict
npm run security:env-check
```

9. Smoke test affected flows.
10. Revoke old secret.
11. Confirm old secret no longer works.
12. Record rotation evidence without secret values.

## Emergency Rotation Steps

Use when a secret is suspected leaked.

1. Declare security incident.
2. Identify all systems using the secret.
3. Disable or restrict the compromised credential if provider supports immediate revocation.
4. Generate replacement secret.
5. Update production secret manager/provider env.
6. Restart services.
7. Revoke old credential fully.
8. Search logs for abuse indicators.
9. Rotate adjacent secrets if blast radius is unclear.
10. Notify affected users/providers if required.

## Service-Specific Notes

### Django `SECRET_KEY`

Risk:

- Rotating may invalidate signed cookies/tokens using Django signing.

Plan:

- Prefer a maintenance window.
- Confirm JWT/session behavior.
- Force logout if necessary.

### JWT Secrets

Risk:

- Existing access/refresh tokens become invalid.

Plan:

- Rotate during low traffic.
- Revoke refresh tokens if compromise is suspected.
- Monitor login failures.

### Internal Tokens

Risk:

- Django/Nest calls fail if not rotated together.

Plan:

- Update Django and Nest env values in one change window.
- Ensure `INTERNAL_SIGNATURE_REQUIRED` remains enabled.
- Restart both services.

### Firebase Service Account

Risk:

- Push delivery may fail.

Plan:

- Create new service account key.
- Update secret manager/file mount.
- Restart notification workers.
- Revoke old service account key.

### Payment Webhook Secret

Risk:

- Webhooks may fail validation.

Plan:

- Use provider dual-secret period if available.
- Update app secret.
- Send test webhook.
- Remove old provider secret.

## Evidence To Record

- Secret name only, never value.
- Reason for rotation.
- Operator and approver.
- Services restarted.
- Verification commands and results.
- Old credential revoked confirmation.
- Follow-up monitoring window.
