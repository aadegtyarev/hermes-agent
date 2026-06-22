# TP / Integrations Team Assistant — deployment notes

A Hermes Agent deployment for a support/integrations team: answers TP questions
in chat, researches the web, digs through zip/rar/7z archives, writes small
automation scripts, schedules cron jobs, and reaches Discourse + YouTrack.

This folder is the deployment bundle (no secrets — those go in `.env`):

- `Dockerfile` — terminal-backend image (`unar` + `ripgrep` on the default base)
- `config.yaml` — the full settled config (env-specific ids/secrets are placeholders)
- `cron-setup.sh` — scaffolds the scheduled jobs via `hermes cron create`
- `scripts/backup.sh` — nightly profile backup (run by the backup cron job)

---

## Architecture decisions

**One profile, not two.** Internal work chats and one public chat share a single
Hermes profile. The confidentiality boundary that matters (work chats → public
chat must not leak) is enforced in code, not by separate profiles.

**Public chat is read-only (`observe_only`).** The bot ingests every message in
the public chat as context but is hard-blocked from emitting anything there
(reply / @mention / reply-to / wake word / `send_message` tool / cron / streaming
/ reaction / typing). Implemented as the `observe_only_chats` feature
(`gateway/platforms/telegram.py`, `tools/send_message_tool.py`). Internal
work-chat data can only leak out if the bot speaks publicly — and it can't.

**Container is the real security boundary, not the tool list.** With
`code_execution` (or `terminal`) the agent runs arbitrary code, so disabling a
tool does not prevent reaching a capability. The boundary is the environment:

- `terminal.backend: docker` — hardened container (`--cap-drop ALL`,
  `no-new-privileges`, `--pids-limit 256`); containerized backends bypass the
  dangerous-command approval layer (can't touch the host), so `approvals.mode: off`
  is safe and there are no prompts.
- **Keys stay out of the container** (`docker_forward_env: []`). Hermes also
  scrubs `KEY/TOKEN/SECRET/PASSWORD/AUTH/CREDENTIAL/WEBHOOK` env vars from the
  `execute_code` sandbox child, so even the model key never reaches it.
- Arbitrary outbound network is allowed (open web research). Residual risk is
  exfiltration of whatever is *in* the container — mitigated by keeping nothing
  sensitive there (no host folders mounted, no secrets forwarded).

**Plugin keys live parent-side.** Discourse/YouTrack tool handlers run in the
gateway process, so their keys/tokens never reach the model or the docker
sandbox. A dedicated bot account on each service should bound the write surface.

**Trusted small team, non-secret data** → no per-user isolation, no PII
redaction, shared team memory is a feature.

**Memory is a real DB, not flat files.** `memory.provider: holographic` swaps
the default `builtin` MEMORY.md/USER.md store for the `holographic` plugin: a
local SQLite database (`$HERMES_HOME/memory_store.db`) with FTS5 full-text
search, trust scoring, and entity resolution. Self-hosted — no external service
or API key. `auto_extract: true` pulls facts from the conversation at session
end. Still profile-scoped (shared across the team), which here is intended.

---

## Archives — no mounting

Data sources: Discourse (plugin), Telegram (gateway), internet (`web_*` tools)
are live. Offline archives are small (~few MB) zip/rar/7z that arrive ad-hoc —
there is **no host folder**, so **no `docker_volumes` for archives**.

- A file dropped in a work chat is auto-cached to `~/.hermes/cache/documents`,
  which is auto-mounted into the container — the agent gets a path it can open
  in the sandbox. Default size limit: 20 MB (`_max_doc_bytes`).
- Internet archives: the bot downloads into the ephemeral `/workspace`.
- Either way it unpacks with `unar` (handles rar/RAR5/7z/zip) and greps with
  `ripgrep` / `search`.

---

## Tools & plugins

Toolset (`platform_toolsets.telegram`): `web, file, vision, skills, memory,
session_search, todo, code_execution, cronjob, discourse, youtrack`. No raw
`terminal` — `code_execution` is the sandboxed scripting surface.

- **discourse** (read-only): `discourse_search`, `discourse_read_topic`.
- **youtrack** (read+write): `youtrack_search`, `youtrack_read_issue`,
  `youtrack_comment`, `youtrack_create_issue`.

**Web search:** `web.search_backend: ddgs` (DuckDuckGo, no API key). Web search
runs parent-side in the gateway, so the **gateway** Python env needs
`pip install ddgs` — it is NOT part of the terminal Dockerfile.

**disk-cleanup** (`plugins.enabled`): auto-removes ephemeral files (unpacked
archives, scratch scripts, cron logs) via session hooks — no agent action.

(Surveyed but not enabled: security-guidance, observability/langfuse, n8n MCP,
kanban — add later if needed.)

---

## Setup

1. **Build the terminal image** and install the gateway-side search dep:
   ```bash
   docker build -t hermes-tp:latest deploy/tp-assistant
   pip install ddgs          # in the gateway's Python env (web search is parent-side)
   ```
2. **Fill `config.yaml`:** replace the `-100<...>` chat-id placeholders (work
   chats in `allowed_chats` / `group_allowed_chats`, the public chat in
   `observe_only_chats`). Model/provider is set: OpenRouter +
   `anthropic/claude-sonnet-4.6` — swap the slug to trade capability for cost.
3. **Fill `.env`** (gitignored) with secrets:
   ```
   OPENROUTER_API_KEY=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_ALLOWED_USERS=<id1>,<id2>,<id3>
   DISCOURSE_URL=...            # DISCOURSE_API_KEY optional (private forums)
   YOUTRACK_URL=...   YOUTRACK_TOKEN=...   # dedicated bot account, project-scoped
   ```
4. **Disable the bot's privacy mode** in @BotFather so it receives all group
   messages (required for the public observe-only chat to be readable).
5. Start the gateway with this profile/config.
6. **Scaffold cron jobs:** copy `scripts/backup.sh` to `~/.hermes/scripts/`
   (`chmod +x`), edit the `DELIVER` / `MONITOR_URL` placeholders in
   `cron-setup.sh`, then run it once. Cron jobs live in profile state (not
   `config.yaml`), so this is a post-start step. Verify with `hermes cron list`.

## Scheduled jobs (cron-setup.sh)

Created via the bot's own cron tool — not declarative in config:

- **Discourse digest** (daily 09:00) — new/unanswered topics → work chat.
- **YouTrack summary** (daily 09:30) — opened-24h + stale-7d tickets.
- **Changelog monitor** (hourly) — watches a URL, `[SILENT]` unless it changed.
- **Profile backup** (nightly 02:00) — `--no-agent` runs `backup.sh`, keeps the
  newest 14 archives, silent unless it fails.

---

## Open items (TODO in config.yaml)

1. ~~Model / provider for the 128k window.~~ DONE — OpenRouter +
   `anthropic/claude-sonnet-4.6` (`provider_routing.data_collection: deny`).
2. ~~Final skill selection for TP.~~ DONE — kept research, productivity,
   data-science, software-development, github, note-taking; disabled the rest
   via `skills.disabled` (opt-out). Open sub-item: enable optional
   `security/oss-forensics` via `hermes claw` if archive forensics is needed.
3. ~~Cron jobs (digests / monitoring / backups).~~ DONE — scaffolded in
   `cron-setup.sh` (+ `scripts/backup.sh`); schedules/targets are placeholders.
4. ~~Keep `session_search`?~~ DONE — KEPT. Shared history search is a feature
   for a trusted team; the work→public boundary is enforced by observe-only
   (the bot can't emit publicly), not by search scope; data is non-secret.
5. ~~Public-bot role beyond observe-only (client replies?)~~ DECIDED — stays
   observe-only here; client help, if added, ships as a SEPARATE public
   profile (see "Future: public client profile" below).

---

## Future: public client profile (planned, not built)

If the bot should ever *answer* clients in the public chat, it must run as a
**separate Hermes profile**, never by un-blocking observe-only here. The profile
is Hermes's hard boundary (profile-scoped `state.db`, memory, sessions, skills),
so a second profile physically cannot read this assistant's work-chat history —
which is exactly the work→public guarantee we need.

Sketch of that second profile:
- Its own bot token and OS/profile dir; **only** the public chat in
  `allowed_chats` / `group_allowed_chats`; no `observe_only_chats`, no work chats.
- Minimal toolset: drop `session_search` and the internal `discourse`/`youtrack`
  plugins; keep `web` + public-facing skills only. No access to internal
  archives (nothing internal mounted).
- Shares the same hardened-docker / no-keys posture.
- Multi-gateway note: only one gateway owns the kanban dispatcher
  (`kanban.dispatch_in_gateway: true`); the public profile's gateway sets it
  `false`. (See docs/kanban/multi-gateway.md.)

This is a design note only — no config for it ships in this bundle yet.

---

## Status

- `observe_only` feature + Discourse/YouTrack plugins: PR
  [aadegtyarev/hermes-agent#1](https://github.com/aadegtyarev/hermes-agent/pull/1)
  (branch `feature/readonly-observe-chats`). Tests + lint green.
- This deploy bundle is intended for a separate secrets-free PR once the open
  items above are settled.
