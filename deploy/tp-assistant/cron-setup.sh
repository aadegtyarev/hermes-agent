#!/usr/bin/env bash
# Scaffold the TP/integrations assistant's scheduled jobs via the bot's own
# cron tool (`hermes cron create`). Cron jobs are NOT declarative in
# config.yaml — they live in the profile state — so run this once after the
# gateway/profile is up. Re-running creates duplicates; `hermes cron list`
# then `hermes cron remove <id>` to clean up.
#
# Schedules and the delivery target are placeholders — edit before running.
# Delivery target forms: origin | local | telegram | discord | signal |
# platform:chat_id  (e.g. telegram:-1001234567890, or :topic for a forum).
set -euo pipefail

# Work chat the digests/summaries are posted to (NOT the public observe-only
# chat — the bot is hard-blocked from sending there anyway).
DELIVER="${DELIVER:-telegram:-100<work1>}"

# --- 1. Discourse digest (daily 09:00) -------------------------------------
hermes cron create "0 9 * * *" \
  "Summarize new and unanswered Discourse topics since yesterday. Group by
   category, link each topic, and flag anything that looks like an unhandled
   support question. If there is nothing new, reply with [SILENT]." \
  --name "Discourse digest" \
  --deliver "$DELIVER"

# --- 2. YouTrack morning summary (daily 09:30) -----------------------------
hermes cron create "30 9 * * *" \
  "Give a short status of YouTrack: tickets opened in the last 24h, and
   tickets with no update for 7+ days (stale). One line each, with links.
   If nothing qualifies, reply with [SILENT]." \
  --name "YouTrack summary" \
  --deliver "$DELIVER"

# --- 3. Monitoring watcher (hourly, silent unless changed) -----------------
# MONITOR_URL is the page/changelog/release feed to watch. The [SILENT]
# convention means no message is sent when nothing changed (no spam).
MONITOR_URL="${MONITOR_URL:-https://example.com/changelog}"
hermes cron create "every 1h" \
  "Check ${MONITOR_URL}. Compare against what you saw on the previous run
   (use your memory). If something changed, summarize what changed in 2-3
   lines with the link. If nothing changed, reply with exactly [SILENT]." \
  --name "Changelog monitor" \
  --deliver "$DELIVER"

# --- 4. Nightly profile backup (daily 02:00, no LLM) -----------------------
# Runs the backup script directly (--no-agent): its stdout is delivered as-is,
# empty stdout = silent. Copy scripts/backup.sh to ~/.hermes/scripts/ first.
hermes cron create "0 2 * * *" \
  --name "Profile backup" \
  --script ~/.hermes/scripts/backup.sh \
  --no-agent \
  --deliver local

echo "Created 4 jobs. Verify with: hermes cron list"
