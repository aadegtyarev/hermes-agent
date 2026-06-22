#!/usr/bin/env bash
# Nightly profile backup for the TP assistant. Invoked by the "Profile backup"
# cron job with --no-agent, so this script IS the job: stdout is delivered
# verbatim and empty stdout = silent. We therefore stay quiet on success and
# only print on failure (which then gets delivered as an alert).
#
# Install: copy to ~/.hermes/scripts/backup.sh and `chmod +x`.
set -uo pipefail

DEST="${HERMES_BACKUP_DIR:-$HOME/.hermes/backups}"
KEEP="${HERMES_BACKUP_KEEP:-14}"
mkdir -p "$DEST"

stamp="$(date +%Y%m%d-%H%M%S)"
out="$DEST/hermes-backup-$stamp.zip"

# Full backup (config, skills, sessions, memory, cron, .env). Excludes the
# hermes-agent codebase itself.
if ! err="$(hermes backup -o "$out" 2>&1)"; then
  echo "Hermes backup FAILED ($stamp): $err"
  exit 1
fi

if [ ! -s "$out" ]; then
  echo "Hermes backup produced no file ($stamp)"
  exit 1
fi

# Rotate: keep the newest $KEEP archives.
ls -1t "$DEST"/hermes-backup-*.zip 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

# Success → no stdout → silent delivery.
exit 0
