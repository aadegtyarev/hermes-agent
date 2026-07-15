# Multi-agent Hermes deploy

Run several **isolated agent-personalities** on one server from a single base
image, each with its own memory / context / persona / config / skill+plugin
subset and its **own Telegram bot** — all driven by one manifest (`agents.yaml`).
Add an agent by editing the manifest and running two commands.

Everything custom lives under this directory. Upstream (`NousResearch/hermes-agent`)
has no `deploy/`, and we edit **no** upstream file, so `git pull origin` stays
conflict-free.

The shipped example agent is **Gpio** (an engineering assistant for Wiren Board
engineers). See the checklist at the bottom for its full configuration.

## Isolation model

The agent runs with `terminal` disabled and its tools sandboxed away from what
it must not break:

| What | How | Reachable by agent? |
|---|---|---|
| `config.yaml` | bind-mounted **read-only** (`instances/<a>/config.yaml`) | read only |
| plugins | curated `HERMES_BUNDLED_PLUGINS` mounted **read-only** | read only |
| **memory** | local SQLite fact_store `$HERMES_HOME/memory_store.db` (holographic) — per-agent file | read+write (own file) |
| secrets (tokens/keys) | **env vars** — `code_execution` scrubs `KEY/TOKEN/SECRET/PASSWORD/AUTH/…` from its sandbox | no (scrubbed) |
| `terminal` tool | `agent.disabled_toolsets: [terminal]` — not in schema | absent |
| **skills** | writable `$HERMES_HOME/skills` (edited via `skill_manage`) | **read+write** (by design) |

`code_execution` (Python) stays on — scripts are allowed — but its env is
scrubbed of secrets and it can't reach the memory container. `approvals: off`
(full control over granted tools). MCP is permitted (`mcp: true`); add servers
under `config.mcp_servers`.

## Clean chat

`display:` is set to final-answer-only for Telegram — no tool-progress, no
reasoning dumps, no interim/status/busy chatter (`tool_progress: off`,
`show_reasoning/interim_assistant_messages/long_running_notifications/
busy_ack_detail/streaming` off, `cleanup_progress` on).

## Strict allowlist

An agent reaches ONLY what it's granted:
- **plugins** — `plugins.enabled` + curated `HERMES_BUNDLED_PLUGINS` (upstream
  plugins we didn't grant aren't even present).
- **skills** — curated: the kit is seeded into `$HERMES_HOME/skills`, and
  `HERMES_BUNDLED_SKILLS` points at an empty dir so upstream image skills are blocked.
- **toolsets** — `platform_toolsets.telegram` is the exact granted list.

## Layout

```
deploy/multi-agent/
├── agents.yaml            # THE manifest — edit this
├── render.py              # agents.yaml -> compose + per-agent seed + curated dirs
├── Dockerfile             # overlay: upstream base + gh/ssh-tools/unar/bw/py-deps
├── base/
│   ├── config.base.yaml   # shared config (models, lockdown, display, curator/BSI, vision)
│   ├── persona.base.md    # shared behaviour prepended to every SOUL
│   ├── plugins/           # custom plugins (see base/plugins/README.md)
│   └── skills/            # skill kit (research / support / WB / ssh|archive|vault guides)
├── allowed-plugins.txt / allowed-skills.txt   # reference menus
├── personas/              # per-agent identity SOUL files
└── instances/<name>/      # per-agent: config.yaml (ro), .env, memory.env, secrets/, data/ ($HERMES_HOME)
```
`render/`, `docker-compose.generated.yml`, real `.env`/`memory.env`, `data/`,
`secrets/` are generated/secret (gitignored). The `*.env.example` templates ARE
committed.

## Models (OpenAI, single provider)

- main chat: **gpt-5.4-nano** · vision (`vision_analyze`): **gpt-5.4-nano** ·
  delegation (subagents): **gpt-5.6-terra** · image gen: **gpt-image-2**.
- One `OPENAI_API_KEY` for everything. Memory (holographic) is local SQLite — no LLM key.

## Setup

1. **Build images** (once):
   ```bash
   docker build -t hermes-agent:base .                       # upstream base (repo root)
   docker build -t hermes-multiagent:latest deploy/multi-agent  # overlay + deps
   ```
2. **Render**: `cd deploy/multi-agent && python render.py`
3. **Fill secrets** (templates are committed as reference):
   ```bash
   cp instances/gpio/.env.example        instances/gpio/.env         # OPENAI/TELEGRAM/GITHUB/YOUTRACK/SSH/BW…
   # Google: python base/plugins/authorize-google.py client_secret.json instances/gpio/secrets/google-token.json
   ```
4. **Launch**: `docker compose -f docker-compose.generated.yml up -d`

## Add a new agent

1. Add a block to `agents.yaml` (name, soul, toolsets, plugins, skills, env…).
2. `python render.py`
3. `cp instances/<name>/.env.example instances/<name>/.env` and fill it.
4. `docker compose -f docker-compose.generated.yml up -d <name>`

## Known follow-ups (before/at first boot)

- **Model slugs**: verified against the account — chat/vision/memory `gpt-5.4-nano`, delegation `gpt-5.6-terra`, image `gpt-image-2` all exist.
- **config.yaml perms**: it's mounted `:ro`; the entrypoint's chmod on it fails
  non-fatally (verified against stage2-hook) — check the first-boot log.

---

## Gpio — profile checklist

- **Persona**: Gpio, для инженеров Wiren Board (`personas/gpio.md`) + общий базис
  (`base/persona.base.md`: Telegram-формат, заземление, веб-поиск wiki/support,
  Discourse off).
- **Models**: chat/vision/memory = gpt-5.4-nano, delegation (subagents) = gpt-5.6-terra, image = gpt-image-2 (OpenAI). All slugs verified against the account. Context windows auto-detected per model (nano 400k · terra 1.05M).
- **Toolsets** (20): web, browser, vision, skills, clarify, file, code_execution,
  ssh, archive, vault, cronjob, delegation, session_search, todo, memory,
  github, youtrack, google_docs, google_sheets, image_gen. (`terminal` off, MCP on.)
- **Plugins**: curl-jina, github (read+write), youtrack (read+write: create/comment), jenkins (CI job/build/log/trigger), http-fetch (curl), google-docs (ro),
  google-sheets (ro), ssh, archive, bitwarden; backends web/ddgs, image_gen/openai,
  memory/holographic.
- **Memory**: holographic — local SQLite fact_store per agent
  (`$HERMES_HOME/memory_store.db`, FTS5 + trust + HRR); no sidecar, no LLM key.
- **Skills** (26 total): research (arxiv, blogwatcher, llm-wiki, obsidian,
  jupyter, searxng/duckduckgo/scrapling/parallel), support/dev (systematic-debugging,
  spike, codebase-inspection), WB (wiren-board, wb-troubleshooting, wb-serial,
  wb-network, wb-mqtt-broker, wb-zigbee, wb-code-research, wb-support-portal-audit,
  diagnostic-cross-weaving, internet-archaeology, wb-software-investigation) +
  guides (ssh-guide, archive-guide, vault-guide). Writable — the agent may edit/add.
- **Self-improvement**: Curator (7d/2h/30d/90d, consolidate, prune, backup keep 5)
  + BSI (creation_nudge 10, write_approval off).
- **Isolation**: config/plugins ro, memory local per-agent SQLite, secrets scrubbed from
  sandbox, terminal off, skills writable. **Clean chat**: final answer only.
- **Secrets** (`instances/gpio/.env`): OPENAI_API_KEY, TELEGRAM_BOT_TOKEN,
  TELEGRAM_ALLOWED_USERS, TELEGRAM_ADMIN_USERS, GITHUB_TOKEN(+ALLOWED_REPOS),
  YOUTRACK_URL/TOKEN, GOOGLE_OAUTH_TOKEN, SSH_* (optional), BW_* (vault).
- **Runtime chat allowlist**: the work/read-only chat list is `env ∪ store`.
  `TELEGRAM_WORK_CHATS`/`READONLY_CHATS` are optional seeds; a bot operator listed in
  `TELEGRAM_ADMIN_USERS` enrols chats live with `/hermes_here` (work), `/hermes_readonly`,
  `/hermes_forget`, `/hermes_chats` — no file edits, no restart. The DM allowlist is
  already runtime (auto-collected from work-chat members, getChatMember-verified).
