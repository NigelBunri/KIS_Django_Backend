#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NEST_DIR="${NEST_DIR:-$(cd "$ROOT_DIR/../Nestjs/CC_Node_Backend" 2>/dev/null && pwd || true)}"
FRONTEND_DIR="${FRONTEND_DIR:-/Users/nigel/dev/KIS}"
RUN_DEPENDENCY_AUDIT="${RUN_DEPENDENCY_AUDIT:-0}"
RUN_FULL_TESTS="${RUN_FULL_TESTS:-0}"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
RESULTS=()

record() {
  local status="$1"
  local name="$2"
  local detail="$3"
  RESULTS+=("$status|$name|$detail")
  case "$status" in
    PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
    SKIP) SKIP_COUNT=$((SKIP_COUNT + 1)) ;;
  esac
}

run_check() {
  local name="$1"
  local dir="$2"
  shift 2
  echo ""
  echo "==> $name"
  echo "    $*"
  if (cd "$dir" && "$@"); then
    record "PASS" "$name" "$*"
  else
    record "FAIL" "$name" "$*"
  fi
}

run_optional() {
  local name="$1"
  local enabled="$2"
  local dir="$3"
  shift 3
  if [ "$enabled" != "1" ]; then
    record "SKIP" "$name" "set the relevant env flag to 1 to run"
    return
  fi
  run_check "$name" "$dir" "$@"
}

echo "KIS Phase 5 validation"
echo "Django:   $ROOT_DIR"
echo "Nest:     ${NEST_DIR:-missing}"
echo "Frontend: $FRONTEND_DIR"

run_check "Django system check" "$ROOT_DIR" python3 manage.py check
run_check "Django deployment verifier" "$ROOT_DIR" python3 manage.py verify_deployment_security --target-production --strict
run_check "Django migration dry run" "$ROOT_DIR" python3 manage.py makemigrations --check --dry-run
run_check "Django security helper compile" "$ROOT_DIR" python3 -m py_compile \
  apps/chat/internal_signing.py \
  apps/chat/internal_auth.py \
  apps/media/views.py \
  apps/core/management/commands/verify_deployment_security.py
run_check "Django focused security tests" "$ROOT_DIR" python3 manage.py test \
  apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_accepts_signed_request_and_rejects_replay \
  apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_rejects_legacy_token_only_request \
  apps.media.tests.PrivateMediaAccessTests \
  --noinput --keepdb
run_optional "Django full test suite" "$RUN_FULL_TESTS" "$ROOT_DIR" python3 manage.py test --noinput --keepdb

if [ -n "$NEST_DIR" ] && [ -d "$NEST_DIR" ]; then
  run_check "Nest production env verifier syntax" "$NEST_DIR" node --check scripts/verify-production-env.js
  run_check "Nest production env verifier" "$NEST_DIR" node scripts/verify-production-env.js
  run_check "Nest focused typecheck" "$NEST_DIR" npx tsc --noEmit --pretty false --incremental false --types node --module commonjs --target ES2021 --experimentalDecorators --emitDecoratorMetadata --esModuleInterop \
    src/security/internal-signing.ts \
    src/auth/internal-auth.guard.ts \
    src/auth/django-auth.service.ts \
    src/chat/integrations/django/django-seq.client.ts \
    src/chat/integrations/django/django-conversation.client.ts \
    src/uploads/uploads.controller.ts \
    src/storage/local-storage.service.ts
  run_check "Nest formatting check" "$NEST_DIR" npx prettier --check \
    src/security/internal-signing.ts \
    src/auth/internal-auth.guard.ts \
    src/auth/django-auth.service.ts \
    src/chat/integrations/django/django-seq.client.ts \
    src/chat/integrations/django/django-conversation.client.ts \
    src/uploads/uploads.controller.ts \
    src/storage/local-storage.service.ts \
    scripts/verify-production-env.js
  run_optional "Nest full typecheck" "$RUN_FULL_TESTS" "$NEST_DIR" npx tsc --noEmit --pretty false --incremental false
  if [ -f "$NEST_DIR/pnpm-lock.yaml" ]; then
    run_optional "Nest production dependency audit" "$RUN_DEPENDENCY_AUDIT" "$NEST_DIR" pnpm audit --prod
  else
    run_optional "Nest production dependency audit" "$RUN_DEPENDENCY_AUDIT" "$NEST_DIR" npm audit --omit=dev
  fi
else
  record "SKIP" "Nest checks" "Nest directory not found"
fi

if [ -d "$FRONTEND_DIR" ]; then
  run_check "React Native targeted lint" "$FRONTEND_DIR" npx eslint src/Module/ChatRoom/uploadFileToBackend.ts
  run_check "React Native typecheck" "$FRONTEND_DIR" npx tsc --noEmit --pretty false
  run_optional "React Native full lint" "$RUN_FULL_TESTS" "$FRONTEND_DIR" npm run lint -- --max-warnings=0
  run_optional "React Native production dependency audit" "$RUN_DEPENDENCY_AUDIT" "$FRONTEND_DIR" npm audit --omit=dev
else
  record "SKIP" "React Native checks" "frontend directory not found"
fi

run_check "Secret exposure scan" "$ROOT_DIR" python3 scripts/security/secret_scan.py \
  --root "$ROOT_DIR" \
  --root "$NEST_DIR" \
  --root "$FRONTEND_DIR"

echo ""
echo "Phase 5 validation summary"
printf "%-6s  %-42s  %s\n" "STATUS" "CHECK" "DETAIL"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r status name detail <<< "$row"
  printf "%-6s  %-42s  %s\n" "$status" "$name" "$detail"
done
echo ""
echo "Totals: PASS=$PASS_COUNT FAIL=$FAIL_COUNT SKIP=$SKIP_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
