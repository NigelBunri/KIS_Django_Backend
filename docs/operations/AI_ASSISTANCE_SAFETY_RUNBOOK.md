# AI Assistance Safety Runbook

Status: Phase 25 foundation.

This runbook defines how KIS can prepare AI assistance without violating Christian principles, child/youth safety, privacy, or regulated-domain boundaries.

## Default State

- AI UI and policy placeholders may be visible.
- Live provider calls are disabled by default.
- No raw prompts or responses are stored by default.
- No provider secret values may be printed, logged, screenshotted, or pasted into tickets.

Required defaults:

- `KIS_AI_ASSISTANCE_ENABLED=True`
- `KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=False`
- `KIS_AI_OUTPUT_MODERATION_REQUIRED=True`
- `KIS_AI_INPUT_REDACTION_REQUIRED=True`
- `KIS_AI_CHILD_SAFE_MODE_REQUIRED=True`
- `KIS_AI_STORE_PROMPTS_ENABLED=False`
- `KIS_AI_STORE_RESPONSES_ENABLED=False`
- `KIS_AI_MEDICAL_DIAGNOSIS_ENABLED=False`
- `KIS_AI_FINANCIAL_ADVICE_ENABLED=False`

## Allowed Future Assistant Surfaces

- Bible study help: scripture-grounded, pastoral support wording, not a replacement for church leadership.
- Learning tutoring: age-safe explanations, no cheating automation.
- Health admin support: summaries, reminders, and admin wording only; no diagnosis or treatment decision.
- Commerce/product help: no manipulative sales, unsafe claims, or hidden sponsorship.
- Moderation triage: staff decision support only.
- Creator/channel drafting: media safety and Christian content policy enforced before publishing.
- Messaging suggestions: opt-in only, no private data leakage, no manipulation.
- Admin insights: aggregate and redacted only.

## Blocked AI Outputs

AI must not produce:

- Pornographic, sexually explicit, predatory, abusive, or degrading content.
- Manipulative, coercive, deceptive, or exploitative messaging.
- Medical diagnosis, treatment decisions, or emergency-care replacement.
- Financial, investment, legal, tax, credit, or cash-equivalent advice.
- Instructions for self-harm, exploitation, abuse, evasion, fraud, or cyber harm.
- Content that violates KIS Christian principles or child/youth-safe modes.

## Provider Enablement Gate

Before enabling `KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=True` in staging:

1. Select one provider in `KIS_AI_PROVIDER`.
2. Confirm provider key is mounted only as an environment secret.
3. Confirm input redaction strips private health, payment, verification, credential, child, and exact-location data unless explicitly approved.
4. Confirm output moderation blocks explicit, manipulative, medical-diagnosis, financial-advice, and unsafe child/youth content.
5. Confirm raw prompt/response storage remains disabled.
6. Confirm staff audit metadata is redacted.
7. Run `/api/v1/core/ai/safety-policy/` and verify no critical failures.
8. Attach staging QA evidence to the release ticket.

Production enablement requires explicit approval after staging evidence.

## Validation Commands

```bash
python3 manage.py check
python3 manage.py verify_deployment_security --target-production
python3 manage.py test apps.core.tests.AIAssistanceSafetyPolicyTests --noinput --keepdb
```

React Native:

```bash
npx eslint src/network/routes/miscRoutes.ts src/services/aiAssistanceSafetyService.ts src/components/dashboard/AIAssistanceSafetyCard.tsx src/screens/tabs/ProfileScreen.tsx --quiet
npm run typecheck -- --pretty false
```

## Incident Rollback

If unsafe AI behavior is detected:

1. Set `KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=False`.
2. Remove or hide the affected assistant entry point.
3. Preserve redacted audit metadata for staff review.
4. Add the unsafe prompt/output class to blocked policy.
5. Re-run staging tests before re-enabling any provider calls.
