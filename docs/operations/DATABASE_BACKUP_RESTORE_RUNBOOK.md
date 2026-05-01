# Database Backup And Restore Runbook

Use this runbook for PostgreSQL backup verification, restore drills, and
database recovery. Do not paste database URLs, passwords, or dumps into docs,
chat, or tickets.

## Ownership

- Primary owner: `TODO_DATABASE_OWNER`
- Backup operator: `TODO_BACKUP_OPERATOR`
- Hosting provider: `TODO_PROVIDER_NAME`
- Database service: `TODO_DATABASE_SERVICE`
- Restore test environment: `TODO_RESTORE_TEST_ENVIRONMENT`

## Backup Policy

Minimum launch policy:

- Automated full backup: daily.
- Point-in-time recovery or WAL/archive equivalent: enabled if provider supports it.
- Retention: at least 14 days for daily backups.
- Encryption at rest: enabled.
- Access control: limited to production operators.
- Backup alerts: failed backup alerts route to `TODO_ESCALATION_CHANNEL`.

Recommended growth policy:

- Daily backups retained 30 days.
- Weekly backups retained 12 weeks.
- Monthly backups retained 12 months.
- Restore test at least monthly and before major releases.

## Pre-Deploy Backup Checklist

Before any migration or risky deploy:

- Confirm latest automated backup completed successfully.
- Record backup identifier in the release ticket: `TODO_BACKUP_ID_FIELD`.
- Confirm point-in-time recovery window if available.
- Confirm no active incident is already affecting database health.
- Confirm rollback target application version.

## Restore Test Procedure

Run this in staging or an isolated restore environment:

1. Create a new temporary database.
2. Restore the selected backup into the temporary database.
3. Point a disposable app environment to the restored database.
4. Run safe checks:

```bash
python3 manage.py check
python3 manage.py showmigrations --plan
python3 manage.py migrate --plan
python3 manage.py verify_deployment_security --target-production
```

5. Verify critical records exist:

- one staff/admin account;
- one normal user account;
- one chat conversation;
- one notification row;
- one media asset row;
- one billing or commerce row if payments are enabled.

6. Confirm no secrets were printed into logs during restore.
7. Destroy the temporary restored database when complete.

## Emergency Restore Procedure

Use only during a confirmed production data-loss or destructive migration event.

1. Declare incident and freeze deploys.
2. Identify recovery target time.
3. Identify backup or point-in-time target.
4. Snapshot current broken database before overwrite, if safe.
5. Restore into a new database first when possible.
6. Run integrity smoke checks.
7. Swap application database connection to restored database.
8. Restart Django, Celery, and Nest services.
9. Run post-restore smoke checks:

```bash
python3 manage.py check
python3 manage.py verify_deployment_security --target-production
```

10. Check login, refresh, chat, upload, notifications, and admin.
11. Record timeline, backup ID, restored time, and residual data loss.

## Rollback From Bad Migration

Preferred path:

- restore from backup into a new database;
- deploy previous application version;
- point app to restored database after validation.

Only use reverse migrations when:

- the migration is explicitly reversible;
- data-loss risk is understood;
- a fresh backup exists;
- a restore test has been completed.

## Evidence To Record

- Backup ID.
- Restore target time.
- Restore environment.
- Restore operator.
- Validation commands and results.
- Known data gap.
- Customer/admin communication status.
