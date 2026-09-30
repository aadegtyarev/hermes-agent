#!/usr/bin/env bash
# Force-refresh one or more already-seeded custom skills, then redeploy.
#
# render.py's skill seeding is copy-IF-ABSENT by design (preserves the
# agent's own edits/additions across routine re-renders — see render.py's
# seed_skills() docstring). That means editing a skill under base/skills/
# and just re-rendering does NOTHING if that skill was already seeded once.
# This deletes the seeded copy first (root-in-container — data/ is sub-UID
# owned, same reasoning as redeploy.sh), so the next render.py re-seeds it
# fresh from base/skills/.
#
# WARNING: only the named skill dirs are removed. Nothing else under
# data/skills/ is touched — agent-accumulated skills not named here are
# never affected. Do not pass a skill name whose *live* (agent-edited)
# content you want to keep — this discards it in favor of the base/skills/
# version.
#
# USAGE: bin/refresh-skill.sh <agent> <skill-name> [<skill-name> ...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(dirname "$SCRIPT_DIR")"

if [ $# -lt 2 ]; then
  echo "USAGE: $0 <agent> <skill-name> [<skill-name> ...]" >&2
  echo "Example: $0 gpio telegram-guide research" >&2
  exit 1
fi

AGENT="$1"; shift
DATA_DIR="$DEPLOY_DIR/instances/$AGENT/data"
if [ ! -d "$DATA_DIR" ]; then
  echo "error: instances/$AGENT/data not found — is '$AGENT' a real agent name?" >&2
  exit 1
fi

TARGETS=()
for s in "$@"; do
  TARGETS+=("/d/skills/$s")
done

echo "==> Removing seeded copies so render.py re-seeds them fresh: $*"
docker run --rm -v "$DATA_DIR":/d busybox rm -rf "${TARGETS[@]}"

echo "==> Redeploying to re-seed"
"$SCRIPT_DIR/redeploy.sh" "$AGENT"
