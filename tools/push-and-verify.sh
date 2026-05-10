#!/usr/bin/env bash
set -euo pipefail

# Run fast tests, then push to origin and verify SHAs match.
# Usage: bash tools/push-and-verify.sh [branch]
# Skip test gate: SKIP_TESTS=1 bash tools/push-and-verify.sh

BRANCH="${1:-main}"

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  echo "Running fast test gate (slow tests excluded)..."
  python -m pytest -m "not slow" --tb=short -q
  echo "Tests passed."
fi

echo "Pushing $BRANCH to origin..."
git push origin "$BRANCH"

LOCAL_SHA=$(git rev-parse HEAD)
ORIGIN_SHA=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL_SHA" = "$ORIGIN_SHA" ]; then
  echo "PUSHED OK"
  echo "  Local  HEAD: $LOCAL_SHA"
  echo "  Origin HEAD: $ORIGIN_SHA"
  git log origin/"$BRANCH" --format='  %h %s' -1
  exit 0
else
  echo "PUSH VERIFICATION FAILED" >&2
  echo "  Local  HEAD: $LOCAL_SHA" >&2
  echo "  Origin HEAD: $ORIGIN_SHA" >&2
  echo "  SHAs do not match. Investigate before declaring done." >&2
  exit 1
fi
