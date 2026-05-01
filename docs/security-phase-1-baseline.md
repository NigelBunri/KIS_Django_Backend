# Security Phase 1 Baseline

This phase adds low-risk guardrails without changing local development behavior.

## Implemented

- Local `.env` files are ignored by the Django backend and React Native app.
- Django `.env.example` now uses placeholders instead of real-looking secrets.
- Nest `.env.example` now uses production-safe placeholders.
- Django production startup now refuses weak `SECRET_KEY`, weak `JWT_SECRET`, weak `DJANGO_INTERNAL_TOKEN`, and `ALLOW_ALL_HOSTS=True`.
- Nest production startup now refuses weak internal/JWT secrets, insecure Django TLS, missing required config, wildcard origins, and HTTP origins.
- Django API schema/docs now remain open in debug mode but require staff login when `DEBUG=False`.

## Required Manual Actions

- Rotate every secret that has ever appeared in `.env` or `.env.example`.
- Rotate database passwords, JWT secrets, internal service tokens, payment keys, Firebase keys, SMS keys, and AI provider keys.
- Remove any committed `.env` files from repository history before sharing or deploying from the repository.
- Use separate secrets for local, staging, and production.

## Validation

- Run `python3 manage.py check` for Django.
- Run `python3 manage.py check --deploy` with production settings after production environment variables are configured.
- Run `pnpm audit --prod` in the Nest backend.
- Run `npm audit --omit=dev` in the React Native app.

## Next Phase

Phase 2 should address dependency vulnerabilities and upload hardening.

## Phase 2 Started

- Django uploads now enforce a maximum size, MIME allowlist, dangerous extension blocklist, and sanitized filenames.
- Nest uploads now require the existing HTTP auth guard, enforce a maximum size, MIME allowlist, and dangerous extension blocklist.
- Dependency constraints were added for audited critical/high JavaScript package families. Refresh lockfiles with the package manager before relying on the new versions.
