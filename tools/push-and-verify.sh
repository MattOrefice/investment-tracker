#!/usr/bin/env bash
set -euo pipefail

# Push current branch to origin and verify the local HEAD matches origin's HEAD.
# Exit 0 only if push reached origin and SHAs match.

BRANCH="${1:-main}"

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
