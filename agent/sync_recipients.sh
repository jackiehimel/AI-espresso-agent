#!/usr/bin/env bash
# Sync agent/recipients.local.txt to the AI_ESPRESSO_TO GitHub secret.
#
# Source of truth: agent/recipients.local.txt (one email per line; # comments
# and blank lines ignored). That file is gitignored -- it never gets pushed to
# the public repo. This script reads it, joins addresses with commas, and
# updates the GitHub secret that the daily workflow consumes.
#
# Usage:
#   ./agent/sync_recipients.sh
#
# Requires: `gh` CLI authenticated to a user with write access to repo secrets.

set -euo pipefail

REPO="jackiehimel/AI-espresso-agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="${SCRIPT_DIR}/recipients.local.txt"

if [[ ! -f "$FILE" ]]; then
  echo "error: $FILE not found." >&2
  echo "Create it with one email per line (# comments allowed)." >&2
  exit 1
fi

# Strip comments + blank lines, trim each address, then comma-join.
LIST=$(grep -vE '^[[:space:]]*(#|$)' "$FILE" | awk '{$1=$1; print}' | tr '\n' ',' | sed 's/,$//')

if [[ -z "$LIST" ]]; then
  echo "error: no non-comment lines in $FILE; refusing to set an empty list." >&2
  exit 1
fi

COUNT=$(echo "$LIST" | tr ',' '\n' | wc -l | tr -d '[:space:]')

echo "Recipients ($COUNT):"
echo "$LIST" | tr ',' '\n' | sed 's/^/  - /'
echo
read -r -p "Push these $COUNT addresses to AI_ESPRESSO_TO on $REPO? [y/N] " ans
case "$ans" in
  y|Y|yes|YES)
    gh secret set AI_ESPRESSO_TO --repo "$REPO" --body "$LIST"
    echo "Synced."
    ;;
  *)
    echo "Aborted; secret unchanged."
    exit 1
    ;;
esac
