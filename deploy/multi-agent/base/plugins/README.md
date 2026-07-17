# Custom plugins (multi-agent base)

Fork-owned plugins, mounted read-only into each agent via the curated
`HERMES_BUNDLED_PLUGINS` dir. Exposed to an agent only when listed in its
`plugins` (→ `plugins.enabled`) AND the plugin's toolset is granted. Nothing
here is added to the upstream `plugins/` tree.

| Plugin | Toolset | Tools | Access | Backend / key |
|---|---|---|---|---|
| `curl-jina` | (web_extract) | — | read | lxml + httpx; `HTTPS_PROXY` optional |
| `github` | `github` | issue_list/view/**create**, pr_list/view/**merge**, repo_search | **read+write** | `gh` CLI; `GITHUB_TOKEN` (repo scope) |
| `youtrack` | `youtrack` | `yt_search`, `yt_get_issue`, `yt_list_projects`, `yt_get_comments`, `yt_work_items` (read) + `yt_create_issue`, `yt_add_comment` (write) | mixed | requests; `YOUTRACK_URL/TOKEN` |
| `google-docs` | `google_docs` | `gdoc_read`, `gdrive_search` | **read-only** | google-api; `GOOGLE_OAUTH_TOKEN` (`GOOGLE_DRIVE_CACHE_TTL` opt) |
| `google-sheets` | `google_sheets` | `gsheet_list_sheets`, `gsheet_read` | **read-only** | google-api; `GOOGLE_OAUTH_TOKEN` |
| `ssh` | `ssh` | run/read/list, start/poll/send/stop/sessions, keygen/copy_id | run | openssh + sshpass; `SSH_*` |
| `archive` | `archive` | `archive_list/extract/create` | run | unar/lsar + stdlib |
| `bitwarden` | `vault` | `vault_list/get/field/totp` | read | `bw` CLI; `BW_*` |

Image generation is **not** a custom plugin here — it uses the upstream bundled
backend `image_gen/openai` (`gpt-image-2`, `OPENAI_API_KEY`), granted via
`bundled_plugins`.

## Read-only vs write

`youtrack`, `google-*` register **no write tools** — defense-in-depth; the hard
guarantee is a read-scoped credential per service. `github` is **full read+write**
by deliberate config choice (issue create + PR merge) — use a repo-scoped token,
optionally pin `GITHUB_ALLOWED_REPOS`. Its guardrail hooks still steer the model
to `github_*` tools over raw `gh`.

## ssh / archive / bitwarden

These give the agent bounded surfaces instead of a raw shell (the `terminal`
tool is disabled deployment-wide). Each shells out **inside its own handler**
(parent-side) via subprocess — independent of the disabled terminal tool. Runtime
binaries (`gh`, `ssh`, `ssh-keygen`, `sshpass`, `unar`, `lsar`, `bw`) are baked
by the overlay image (`deploy/multi-agent/Dockerfile`). See the `ssh-guide`,
`archive-guide`, `vault-guide` skills for usage.

- **ssh**: host-key checking disabled (reflashed devices change keys); any host
  by default (`SSH_ALLOWED_HOSTS` restricts); key or login/password; long/background
  sessions; key generation + install (`ssh_keygen`/`ssh_copy_id`).
- **bitwarden**: fetch a secret on demand (audit-logged). `BW_*` creds are env
  vars → scrubbed from the `code_execution` sandbox, so the agent can only reach
  secrets through `vault_*`, not read them directly.

## Google credentials

Mint a read-only OAuth token once (host-side), drop it in the instance `secrets/`:
```bash
pip install google-auth-oauthlib
python authorize-google.py client_secret.json instances/<agent>/secrets/google-token.json
```

## Runtime deps (baked by the overlay image)

`gh`, `unar`, `p7zip`, `sshpass`, `@bitwarden/cli`; pip `lxml`,
`google-api-python-client`, `google-auth[-oauthlib]`, `hindsight-client`, `ddgs`,
`py7zr`, `rarfile`. openssh-client + Node come from the upstream base image.
