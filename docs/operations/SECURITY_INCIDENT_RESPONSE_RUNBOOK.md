# Security Incident Response Runbook

Use this for suspected account compromise, credential leak, private media
exposure, payment abuse, internal service auth failure, or data access anomaly.

## Ownership

- Incident commander: `TODO_INCIDENT_COMMANDER`
- Engineering lead: `TODO_ENGINEERING_LEAD`
- Communications owner: `TODO_COMMUNICATIONS_OWNER`
- Legal/privacy owner: `TODO_LEGAL_PRIVACY_OWNER`
- Escalation channel: `TODO_ESCALATION_CHANNEL`
- Log provider: `TODO_LOG_PROVIDER`

## Severity Levels

### SEV-1

- Confirmed data breach.
- Private media or health/payment data exposed.
- Production admin compromise.
- Active attacker or destructive action.

### SEV-2

- Credential leak without confirmed abuse.
- Significant auth bypass risk.
- High-volume abuse, spam, or fraud.
- Broken production internal service trust.

### SEV-3

- Suspicious behavior requiring investigation.
- Failed security gate before deployment.
- Limited exposure in staging or development.

## First 15 Minutes

1. Open incident channel.
2. Assign incident commander.
3. Freeze deploys unless rollback is needed.
4. Preserve logs and evidence.
5. Identify affected systems:

- Django API;
- Nest chat/realtime;
- React Native app;
- database;
- Redis;
- media storage;
- Firebase;
- payment/SMS/AI providers.

6. Decide immediate containment:

- disable public media path;
- rotate secret;
- block account/session/device;
- disable provider key;
- rollback deployment;
- increase throttles;
- block origin/IP at provider/WAF.

## Investigation Checklist

- Review Django security audit events.
- Review failed auth metrics.
- Review internal auth failure logs.
- Review Nest HTTP/Socket.IO logs.
- Review admin login and staff action logs.
- Review media access logs/CDN logs.
- Review payment webhook failures.
- Review Firebase credential usage if applicable.
- Review database changes around incident window.
- Check for new or suspicious environment changes.

## Containment Playbooks

### Credential Leak

Use [Secret Rotation](SECRET_ROTATION_RUNBOOK.md).

Immediate actions:

- revoke exposed credential;
- rotate dependent credentials;
- redeploy/restart services;
- search logs for use of old credential.

### Private Media Exposure

Use [Media And Upload Storage Recovery](MEDIA_STORAGE_RECOVERY_RUNBOOK.md).

Immediate actions:

- disable public path;
- purge CDN;
- identify object keys;
- review access logs.

### Bad Deployment

Use [Application And Environment Rollback](APPLICATION_ROLLBACK_RUNBOOK.md).

Immediate actions:

- rollback app or env;
- verify database migration status;
- smoke test critical flows.

### Database Damage

Use [Database Backup And Restore](DATABASE_BACKUP_RESTORE_RUNBOOK.md).

Immediate actions:

- freeze writes if needed;
- snapshot current state;
- restore to new database first when possible.

## Communication

Internal update template:

```text
Incident: TODO_ID
Severity: TODO_SEVERITY
Started: TODO_TIME
Affected systems: TODO_SYSTEMS
Current impact: TODO_IMPACT
Containment: TODO_ACTIONS
Next update: TODO_TIME
Owner: TODO_OWNER
```

External communication must be approved by `TODO_LEGAL_PRIVACY_OWNER`.

## Recovery And Closure

Do not close the incident until:

- containment is complete;
- affected secrets are rotated or confirmed safe;
- affected data/media is identified;
- production verifiers pass or known exceptions are approved;
- monitoring is stable;
- user/provider notifications are complete if required;
- post-incident review owner is assigned.

## Post-Incident Review

Record:

- timeline;
- root cause;
- blast radius;
- what detected it;
- what delayed detection or response;
- customer/user impact;
- data categories involved;
- actions completed;
- follow-up tasks with owners and dates.
