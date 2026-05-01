# KIS Production Operations Overview

This is the operational handoff index for KIS production readiness. Keep real
provider names, credentials, hostnames, backup IDs, and emergency contacts in
the hosting provider or incident system, not in source control.

## Systems Covered

- Django backend: API, admin, Celery tasks, database migrations.
- Nest backend: chat/realtime HTTP and Socket.IO service.
- React Native app: deployed mobile clients and release channels.
- PostgreSQL database.
- Redis/cache/broker.
- Media/upload storage.
- Firebase push notification credentials.
- Payment, SMS, AI, and other provider secrets.

## Provider Placeholders

Fill these before production launch:

- Hosting provider: `TODO_PROVIDER_NAME`
- Django service name: `TODO_DJANGO_SERVICE`
- Nest service name: `TODO_NEST_SERVICE`
- Database service name: `TODO_DATABASE_SERVICE`
- Redis service name: `TODO_REDIS_SERVICE`
- Media/object storage bucket: `TODO_MEDIA_BUCKET`
- Secret manager path/project: `TODO_SECRET_MANAGER_PATH`
- Log/monitoring provider: `TODO_LOG_PROVIDER`
- On-call owner: `TODO_ON_CALL_OWNER`
- Escalation channel: `TODO_ESCALATION_CHANNEL`

## Recovery Targets

Set realistic targets with the business owner before launch:

- RPO, database: `TODO_RPO_DATABASE`, recommended starting target: 15 minutes.
- RTO, API/chat: `TODO_RTO_APP`, recommended starting target: 60 minutes.
- RTO, media restore: `TODO_RTO_MEDIA`, recommended starting target: 4 hours.
- Incident acknowledgement: `TODO_INCIDENT_ACK`, recommended starting target: 15 minutes.

## Required Runbooks

- [Database Backup And Restore](DATABASE_BACKUP_RESTORE_RUNBOOK.md)
- [Application And Environment Rollback](APPLICATION_ROLLBACK_RUNBOOK.md)
- [Media And Upload Storage Recovery](MEDIA_STORAGE_RECOVERY_RUNBOOK.md)
- [Secret Rotation](SECRET_ROTATION_RUNBOOK.md)
- [Security Incident Response](SECURITY_INCIDENT_RESPONSE_RUNBOOK.md)

## Release Readiness Rule

Do not deploy to production unless:

- database backup schedule is active;
- restore test has been completed in a non-production environment;
- application rollback command/path is known;
- current release artifact/version can be identified;
- production env values are stored in secret manager or protected provider settings;
- `INTERNAL_SIGNATURE_REQUIRED` is enabled for Django and Nest;
- private media exposure decisions are documented;
- on-call owner and escalation channel are active.

## Verification

From the Django backend root:

```bash
python3 scripts/security/verify_ops_readiness.py
```

This checks the presence and structure of the operational runbooks. It does not
connect to production and does not need real secrets.
