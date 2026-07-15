"""Bitwarden vault tools (generic, bounded). Runs parent-side via the `bw` CLI.

The agent fetches a specific secret on demand instead of secrets sitting in env.
Vault credentials (BW_*) are env vars → scrubbed from the code_execution sandbox,
so the agent can't read them directly; it can only ask through these tools.

Unlock is automatic and cached for the gateway process:
  BW_CLIENTID / BW_CLIENTSECRET  → `bw login --apikey`
  BW_PASSWORD                    → `bw unlock` (master password)
  BW_SERVER  (optional)          → self-hosted / Vaultwarden URL

Tools: vault_list, vault_get, vault_field, vault_totp.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

from tools.registry import tool_error, tool_result

_SESSION: dict[str, str | None] = {"key": None}
_LOCK = threading.Lock()
_TIMEOUT = 60


def check_available() -> bool:
    return bool(os.environ.get("BW_PASSWORD") and os.environ.get("BW_CLIENTID"))


def _bw(args: list[str], session: bool = True, input_text: str | None = None):
    argv = ["bw", *args]
    if session and _SESSION["key"]:
        argv += ["--session", _SESSION["key"]]
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, input=input_text)


def _config_hint() -> str:
    return ("Set BW_CLIENTID + BW_CLIENTSECRET (API key from Bitwarden → Account "
            "Settings → Security → Keys) and BW_PASSWORD (master password) in the "
            "agent .env. For self-hosted/Vaultwarden also set BW_SERVER.")


def _ensure_session():
    """Log in (api key) + unlock; cache the session key. Returns (key, err)."""
    if _SESSION["key"]:
        return _SESSION["key"], None
    with _LOCK:
        if _SESSION["key"]:
            return _SESSION["key"], None
        if not check_available():
            return None, tool_error("Bitwarden is not configured. " + _config_hint())
        try:
            server = os.environ.get("BW_SERVER", "").strip()
            if server:
                _bw(["config", "server", server], session=False)
            login = _bw(["login", "--apikey"], session=False)
            blob = (login.stdout + login.stderr).lower()
            if login.returncode != 0 and "already logged in" not in blob:
                return None, tool_error(
                    f"bw login failed: {(login.stderr or login.stdout).strip()[:300]}. "
                    + _config_hint()
                )
            unlock = _bw(["unlock", "--passwordenv", "BW_PASSWORD", "--raw"], session=False)
            if unlock.returncode != 0 or not unlock.stdout.strip():
                return None, tool_error(
                    f"bw unlock failed: {(unlock.stderr or unlock.stdout).strip()[:300]}. "
                    "Check BW_PASSWORD (master password)."
                )
            _SESSION["key"] = unlock.stdout.strip()
            return _SESSION["key"], None
        except FileNotFoundError:
            return None, tool_error("bw CLI not found in image (install @bitwarden/cli).")
        except subprocess.TimeoutExpired:
            return None, tool_error("bw timed out during login/unlock.")
        except Exception as e:  # noqa: BLE001
            return None, tool_error(f"bw login/unlock error: {e}")


def _get_item(name: str):
    """Return (item_dict, err). Retries once after re-unlock on session failure."""
    key, err = _ensure_session()
    if err:
        return None, err
    r = _bw(["get", "item", name])
    if r.returncode != 0:
        blob = (r.stderr + r.stdout).lower()
        if "not found" in blob:
            return None, tool_error(
                f"No vault item matching '{name}'. Use vault_list to find the exact "
                f"name or id (search is case-insensitive substring)."
            )
        if "session" in blob or "unlock" in blob or "locked" in blob:
            _SESSION["key"] = None
            key, err = _ensure_session()
            if err:
                return None, err
            r = _bw(["get", "item", name])
        if r.returncode != 0:
            return None, tool_error(f"bw get item failed: {(r.stderr or r.stdout).strip()[:300]}")
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError:
        return None, tool_error("bw returned non-JSON for the item.")


# --- schemas -----------------------------------------------------------------

VAULT_LIST = {"name": "vault_list", "description": "List Bitwarden vault items (names + ids), optionally filtered by a search substring.",
              "parameters": {"type": "object", "properties": {
                  "search": {"type": "string", "description": "Optional case-insensitive substring to filter item names."}},
                  "required": []}}

VAULT_GET = {"name": "vault_get", "description": "Get a vault item's login (username, password, uris) by name or id.",
             "parameters": {"type": "object", "properties": {
                 "name": {"type": "string", "description": "Item name (substring) or id."}}, "required": ["name"]}}

VAULT_FIELD = {"name": "vault_field", "description": "Get one field of a vault item: 'password', 'username', 'uri', 'notes', 'totp', or a custom field name.",
               "parameters": {"type": "object", "properties": {
                   "name": {"type": "string", "description": "Item name or id."},
                   "field": {"type": "string", "description": "Field: password/username/uri/notes/totp or a custom field name."}},
                   "required": ["name", "field"]}}

VAULT_TOTP = {"name": "vault_totp", "description": "Get the current TOTP (2FA) code for a vault item.",
              "parameters": {"type": "object", "properties": {
                  "name": {"type": "string", "description": "Item name or id."}}, "required": ["name"]}}


# --- handlers ----------------------------------------------------------------

def handle_vault_list(args, **kw):
    key, err = _ensure_session()
    if err:
        return err
    search = str(args.get("search") or "").strip()
    cmd = ["list", "items"]
    if search:
        cmd += ["--search", search]
    r = _bw(cmd)
    if r.returncode != 0:
        return tool_error(f"bw list failed: {(r.stderr or r.stdout).strip()[:300]}")
    try:
        items = json.loads(r.stdout) or []
    except json.JSONDecodeError:
        return tool_error("bw returned non-JSON for the item list.")
    out = [{"id": i.get("id"), "name": i.get("name"),
            "username": (i.get("login") or {}).get("username")} for i in items]
    return tool_result({"count": len(out), "items": out[:500]})


def handle_vault_get(args, **kw):
    name = str(args.get("name") or "").strip()
    if not name:
        return tool_error("vault_get needs 'name' (item name or id). Example: vault_get(name='prod-server'). Use vault_list to discover names.")
    item, err = _get_item(name)
    if err:
        return err
    login = item.get("login") or {}
    return tool_result({
        "id": item.get("id"), "name": item.get("name"),
        "username": login.get("username"), "password": login.get("password"),
        "uris": [u.get("uri") for u in (login.get("uris") or [])],
        "totp": bool(login.get("totp")),
    })


def handle_vault_field(args, **kw):
    name = str(args.get("name") or "").strip()
    field = str(args.get("field") or "").strip()
    if not name or not field:
        return tool_error("vault_field needs 'name' and 'field'. Example: vault_field(name='router', field='password'). Fields: password/username/uri/notes/totp or a custom field name.")
    item, err = _get_item(name)
    if err:
        return err
    login = item.get("login") or {}
    fl = field.lower()
    if fl == "password":
        val = login.get("password")
    elif fl == "username":
        val = login.get("username")
    elif fl in ("uri", "url"):
        uris = login.get("uris") or []
        val = uris[0].get("uri") if uris else None
    elif fl == "notes":
        val = item.get("notes")
    elif fl == "totp":
        return handle_vault_totp({"name": name})
    else:
        val = None
        for f in (item.get("fields") or []):
            if str(f.get("name", "")).lower() == fl:
                val = f.get("value")
                break
    if val is None:
        return tool_error(f"Field '{field}' not found on item '{item.get('name')}'. Available custom fields: "
                          f"{[f.get('name') for f in (item.get('fields') or [])] or 'none'}; standard: password/username/uri/notes/totp.")
    return tool_result({"name": item.get("name"), "field": field, "value": val})


def handle_vault_totp(args, **kw):
    name = str(args.get("name") or "").strip()
    if not name:
        return tool_error("vault_totp needs 'name'. Example: vault_totp(name='github'). The item must have a TOTP secret set.")
    key, err = _ensure_session()
    if err:
        return err
    r = _bw(["get", "totp", name])
    if r.returncode != 0:
        return tool_error(f"No TOTP for '{name}' (or item not found): {(r.stderr or r.stdout).strip()[:200]}. Check with vault_get.")
    return tool_result({"name": name, "totp": r.stdout.strip()})
