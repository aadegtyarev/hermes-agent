"""http-fetch plugin — one bounded HTTP(S) fetch tool (aka curl / wget).

`terminal` is disabled for the locked-down agent, but sometimes you just need to
pull a URL — a page, a JSON REST endpoint, or an image. `http_fetch` does a
single HTTP(S) request with a size cap and timeout and either returns the body
(text/JSON inline) or saves it to a file (binary — images, archives).

No secrets: it never reads tokens/keys from the environment and does no auth
injection. Public URLs, or endpoints that need no credentials. The caller may
pass explicit headers (e.g. Accept, User-Agent). Runs parent-side.

Registers into the `web` toolset (already granted), so it needs no new toolset —
opt in via plugins.enabled: [http-fetch].
"""
from __future__ import annotations

import logging
import os

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

_MAX_BYTES_CAP = 25 * 1024 * 1024        # hard ceiling regardless of max_bytes
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_TEXT_INLINE_CAP = 200_000               # chars returned inline for text bodies

HTTP_FETCH_SCHEMA = {
    "name": "http_fetch",
    "description": (
        "Fetch a single URL over HTTP(S) — aka curl / wget. GET or POST with "
        "optional headers and a request body. Returns the status, a few response "
        "headers, and the body: text/JSON inline, binary (images, archives) only "
        "when you pass save_path to download it. No auth/secrets — public URLs or "
        "endpoints needing no credentials. Use it to grab a page, poke a REST API, "
        "or download a file when `terminal` is unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http:// or https:// URL."},
            "method": {"type": "string", "description": "GET (default) or POST."},
            "headers": {"type": "object", "description": "Optional request headers, e.g. {\"Accept\": \"application/json\"}."},
            "data": {"type": "string", "description": "Optional request body for POST (raw string; set Content-Type via headers)."},
            "save_path": {"type": "string", "description": "Save the body to this path (relative to the agent home) instead of returning it inline. Required for binary/large files."},
            "max_bytes": {"type": "integer", "description": f"Max bytes to read (default {_DEFAULT_MAX_BYTES}, ceiling {_MAX_BYTES_CAP})."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)."},
        },
        "required": ["url"],
    },
}


def _check() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _home() -> str:
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.getcwd()


def handle_http_fetch(args, **_kw):
    import requests

    url = str(args.get("url") or "").strip()
    if not url:
        return tool_error("http_fetch needs 'url'.")
    if not (url.startswith("http://") or url.startswith("https://")):
        return tool_error("url must be an absolute http:// or https:// URL.")
    method = str(args.get("method") or "GET").strip().upper()
    if method not in ("GET", "POST"):
        return tool_error("method must be GET or POST.")
    headers = args.get("headers") or {}
    if not isinstance(headers, dict):
        return tool_error("headers must be an object of name -> value.")
    headers = {str(k): str(v) for k, v in headers.items()}
    headers.setdefault("User-Agent", "hermes-http-fetch/1.0")
    data = args.get("data")
    try:
        max_bytes = min(int(args.get("max_bytes") or _DEFAULT_MAX_BYTES), _MAX_BYTES_CAP)
    except (TypeError, ValueError):
        max_bytes = _DEFAULT_MAX_BYTES
    try:
        timeout = min(int(args.get("timeout") or 30), 120)
    except (TypeError, ValueError):
        timeout = 30
    save_path = str(args.get("save_path") or "").strip()

    try:
        resp = requests.request(
            method, url, headers=headers,
            data=(data.encode("utf-8") if isinstance(data, str) else data),
            timeout=timeout, stream=True, allow_redirects=True,
        )
    except Exception as e:  # noqa: BLE001
        return tool_error(f"request failed: {e}")

    # Read the body with a hard byte cap so a huge response can't blow up memory.
    chunks, total, truncated = [], 0, False
    try:
        for chunk in resp.iter_content(8192):
            if not chunk:
                continue
            if total + len(chunk) > max_bytes:
                chunks.append(chunk[: max_bytes - total])
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
    except Exception as e:  # noqa: BLE001
        resp.close()
        return tool_error(f"failed reading response: {e}")
    body = b"".join(chunks)
    ctype = resp.headers.get("Content-Type", "")
    status = resp.status_code
    final_url = resp.url
    hdrs = {k: resp.headers.get(k) for k in ("Content-Type", "Content-Length", "Location", "Server")
            if resp.headers.get(k)}
    resp.close()

    if save_path:
        home = _home()
        dest = save_path if os.path.isabs(save_path) else os.path.join(home, save_path)
        dest = os.path.normpath(dest)
        try:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as f:
                f.write(body)
        except Exception as e:  # noqa: BLE001
            return tool_error(f"failed to save to {dest}: {e}")
        return tool_result({"status": status, "url": final_url, "headers": hdrs,
                            "saved_to": dest, "bytes": len(body), "truncated": truncated})

    is_text = (not ctype) or ctype.startswith("text/") or any(
        t in ctype for t in ("json", "xml", "javascript", "x-www-form-urlencoded"))
    if is_text:
        text = body.decode(resp.encoding or "utf-8", errors="replace") if resp.encoding \
            else body.decode("utf-8", errors="replace")
        if len(text) > _TEXT_INLINE_CAP:
            text, truncated = text[:_TEXT_INLINE_CAP], True
        return tool_result({"status": status, "url": final_url, "headers": hdrs,
                            "body": text, "truncated": truncated})

    return tool_result({"status": status, "url": final_url, "headers": hdrs, "bytes": len(body),
                        "note": f"binary response ({ctype or 'unknown'}); pass save_path to download it."})


def register(ctx) -> None:
    """Register the http_fetch tool into the web toolset."""
    ctx.register_tool(
        name="http_fetch",
        toolset="web",
        schema=HTTP_FETCH_SCHEMA,
        handler=handle_http_fetch,
        check_fn=_check,
        emoji="🌐",
    )
