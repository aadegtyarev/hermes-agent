# TP / Integrations Team Assistant — deployment notes

A Hermes Agent deployment for a support/integrations team: answers TP questions
in chat, researches the web, digs through zip/rar/7z archives, writes small
automation scripts, schedules cron jobs, and reaches Discourse + YouTrack.

This folder is the deployment bundle (no secrets — those go in `.env`):

- `Dockerfile` — terminal-backend image (`unar` + `ripgrep` on the default base)
- `config.yaml` — the full settled config (env-specific ids/secrets are placeholders)

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

---

## Setup

1. **Build the terminal image:**
   ```bash
   docker build -t hermes-tp:latest deploy/tp-assistant
   ```
2. **Fill `config.yaml`:** replace the `model.default` TODO and the
   `-100<...>` chat-id placeholders (work chats in `allowed_chats` /
   `group_allowed_chats`, the public chat in `observe_only_chats`).
3. **Fill `.env`** (gitignored) with secrets:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_ALLOWED_USERS=<id1>,<id2>,<id3>
   DISCOURSE_URL=...            # DISCOURSE_API_KEY optional (private forums)
   YOUTRACK_URL=...   YOUTRACK_TOKEN=...   # dedicated bot account, project-scoped
   ```
4. **Disable the bot's privacy mode** in @BotFather so it receives all group
   messages (required for the public observe-only chat to be readable).
5. Start the gateway with this profile/config.

---

## Open items (TODO in config.yaml)

1. Model / provider for the 128k window.
2. Final skill selection for TP (e.g. research, github/codebase-inspection,
   productivity/ocr-and-documents, security/oss-forensics).
3. Cron jobs (digests / monitoring / backups).
4. Keep `session_search`? (One profile, no DMs → no cross-user leak.)
5. Public-bot role beyond observe-only (client replies?) — currently read-only.

---

## Status

- `observe_only` feature + Discourse/YouTrack plugins: PR
  [aadegtyarev/hermes-agent#1](https://github.com/aadegtyarev/hermes-agent/pull/1)
  (branch `feature/readonly-observe-chats`). Tests + lint green.
- This deploy bundle is intended for a separate secrets-free PR once the open
  items above are settled.
