#!/usr/bin/env bash
# Redeploy a multi-agent instance: rebuild both images, re-render config/
# plugins/skills, and restart the container — the full sequence from
# SERVER-DEPLOY.md, wrapped so the operator (or the agent itself, via its
# own terminal/ssh_run tools) never has to remember the chown dance or the
# `up -d` vs `restart` distinction by hand.
#
# WHY THE CHOWN DANCE CAN'T JUST GO AWAY: render.py writes two families of
# files with genuinely different ownership under rootless Docker —
# docker-compose.generated.yml / render/ (host-owned, created by whoever
# last ran render.py on the host) and instances/<agent>/{data,config.yaml}
# (owned by the sub-UID rootless Docker maps HERMES_UID to). A single UID
# — host or container — can't write both without one of: (a) matching
# HERMES_UID to the host UID (rejected in CLAUDE.md: breaks rootless
# isolation), or (b) a fragile shared-group/ACL scheme across the user
# namespace remap. Flipping ownership around render.py is the actually-
# correct way to live with that, not a workaround — this script just
# makes sure it always happens in the right order.
#
# Safe to re-run any time (idempotent): if nothing changed, this costs a
# few seconds of Docker build-cache no-ops plus one restart.
#
# USAGE: bin/redeploy.sh [agent-name]   (default: gpio)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(dirname "$SCRIPT_DIR")"          # deploy/multi-agent
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"   # repo root (for the base image build)
AGENT="${1:-gpio}"
IMAGE_BASE="hermes-agent:base"
IMAGE_OVERLAY="hermes-multiagent:latest"
CONTAINER="hermes-$AGENT"
DATA_DIR="$DEPLOY_DIR/instances/$AGENT/data"
CONFIG_FILE="$DEPLOY_DIR/instances/$AGENT/config.yaml"

if [ ! -d "$DATA_DIR" ] || [ ! -f "$CONFIG_FILE" ]; then
  echo "error: instances/$AGENT/{data,config.yaml} not found — is '$AGENT' a real agent name (agents.yaml)?" >&2
  exit 1
fi

cd "$DEPLOY_DIR"

echo "==> [1/5] Rebuilding images (no-op if nothing changed, uses Docker layer cache)"
docker build --network=host -t "$IMAGE_BASE" "$REPO_ROOT"
docker build --network=host -t "$IMAGE_OVERLAY" "$DEPLOY_DIR"

echo "==> [2/5] chown data+config -> 0:0 (host needs write access for render.py)"
docker run --rm -v "$DATA_DIR":/d -v "$CONFIG_FILE":/c \
  --entrypoint chown "$IMAGE_OVERLAY" -R 0:0 /d /c

echo "==> [3/5] render.py"
python3 render.py

echo "==> [4/5] chown data+config -> 10001:10001 (back to the agent)"
docker run --rm -v "$DATA_DIR":/d -v "$CONFIG_FILE":/c \
  --entrypoint chown "$IMAGE_OVERLAY" -R 10001:10001 /d /c

echo "==> [5/5] Recreate + restart $CONTAINER"
docker compose -f docker-compose.generated.yml up -d
# config.yaml is a bind mount, not baked into the image — `up -d` sees no
# service-definition diff for a content-only config/plugin/skill change and
# won't recreate the container. Restart unconditionally so this script
# always actually applies what render.py just wrote, whether or not compose
# thinks anything changed.
docker restart "$CONTAINER"

echo "==> Done. Recent logs:"
sleep 2
docker logs --tail 30 "$CONTAINER"
