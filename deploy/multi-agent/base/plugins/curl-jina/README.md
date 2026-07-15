# web-curl-jina — free `web_extract` backend (no local browser)

Free replacement for the paid Firecrawl `web_extract` backend, built for hosts
that cannot run headless Chromium (e.g. the 512 MB hermes container on node1).

Two-tier strategy (see `provider.py`):

- **Tier 1** — fetch raw HTML over the proxy (`httpx`, `trust_env` →
  `HTTP(S)_PROXY`) and extract markdown with **`lxml` only** (no
  trafilatura/bs4 — they are not in the lean image; `lxml` is).
- **Tier 2** — when Tier 1 is thin (JS-rendered SPA) or fails, fall back to
  **Jina AI Reader** (`https://r.jina.ai/<url>`), which renders JavaScript
  remotely and returns clean markdown. Nothing renders locally.

Search is not implemented — pair with `ddgs`/`searxng` for `web_search`.

## Install / enable (node1)

1. Deploy to `~/.hermes/plugins/curl-jina/` (owner `kern:kern`).
2. `config.yaml`:
   - `plugins.enabled:` add `web-curl-jina`
   - `web.extract_backend: curl-jina`
3. Optional `JINA_API_KEY` (raises Reader rate limit) — put it in
   `/home/kern/hermes/.env` **and** add `- JINA_API_KEY=${JINA_API_KEY}` to the
   compose `environment:` block (env only reaches the container via compose
   `environment:`, not from `.env` alone).
4. Recreate the container (`docker compose up -d`).

## ⚠️ Required core patch (FRAGILE — re-apply after every `hermes update`)

The hermes-agent dispatcher `tools/web_tools.py::_is_backend_available()` has a
**hardcoded list of backend names** and does not recognise plugin-provided web
providers. Without a patch, `web.extract_backend: curl-jina` is silently
ignored and `web_extract` falls back to Firecrawl.

Fix: make `_is_backend_available()` consult the web registry for unknown names.
The idempotent patcher is committed here as `patch_web_tools.py`.

Apply (on node1):

```bash
# host build source (so it is baked on next image rebuild)
sudo python3 patch_web_tools.py /home/kern/hermes-agent/tools/web_tools.py
# rebuild + recreate so the patch lands in the image
sudo docker compose --env-file /home/kern/hermes/.env \
  -f /home/kern/hermes/docker-compose.yml build gateway
sudo docker compose --env-file /home/kern/hermes/.env \
  -f /home/kern/hermes/docker-compose.yml up -d
```

`hermes update` / an image rebuild from fresh upstream **wipes this patch** —
re-run the patcher and rebuild. The patcher is a no-op if already applied.

Verify routing: `_get_extract_backend()` must return `curl-jina`, and a real
`web_extract` call must return content (Firecrawl with 0 credits would return
"Payment Required").

## Egress note

Tier 1 fetches each target URL **directly**, so every extracted domain appears
as an UNKNOWN destination in the egress watchdog (`r.jina.ai` is trusted, but
Tier-1 targets cannot be predicted). This is accepted in exchange for keeping
most extraction local/private. The alternative — a Jina-only mode routing all
`web_extract` through the single trusted `r.jina.ai` — is not enabled.
