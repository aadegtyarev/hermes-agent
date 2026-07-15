"""Bounded-but-capable SSH tools (generic). Runs parent-side (gateway).

Design notes:
- Host-key checking is DISABLED (StrictHostKeyChecking=no + UserKnownHostsFile
  =/dev/null). Deliberate: managed devices get reflashed/reset and their host
  key changes, which would otherwise wedge ssh. LogLevel=ERROR hides the noise.
- Auth: key (SSH_KEY, default ~/.ssh/id_ed25519) OR login/password (per-call
  `password` or SSH_PASSWORD, via sshpass). Password path drops BatchMode.
- One-shot (`ssh_run`) AND long/background sessions: `ssh_start` launches a
  command as a tracked subprocess that keeps running; `ssh_poll` drains its
  output, `ssh_send` writes to its stdin, `ssh_stop` kills it. Sessions live in
  the gateway process (across tool calls, not across gateway restarts).
- Key provisioning: `ssh_keygen` makes a keypair; `ssh_copy_id` appends the
  public key to a host's authorized_keys (using password auth the first time).
- Host allowlist SSH_ALLOWED_HOSTS ('host'/'user@host', or '*' for all;
  empty = none). Every call is audit-logged by __init__.

Env: SSH_ALLOWED_HOSTS, SSH_KEY, SSH_USER, SSH_PASSWORD.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import uuid

from tools.registry import tool_error, tool_result

_STDOUT_CAP = 20000
_SESS_BUF_CAP = 200_000       # per-session output buffer cap (chars)
_MAX_TIMEOUT = 300
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


# --- config ------------------------------------------------------------------

def _allowed_hosts() -> list[str]:
    raw = os.environ.get("SSH_ALLOWED_HOSTS", "").strip()
    return [h.strip() for h in raw.split(",") if h.strip()]


def _hostpart(h: str) -> str:
    return h.split("@", 1)[-1]


def check_available() -> bool:
    return True   # plugin is opt-in via plugins.enabled; tools always available once on


def _gate(host: str):
    # Default (SSH_ALLOWED_HOSTS unset/empty) = ANY host allowed. Set a
    # comma-separated list (or '*') to RESTRICT to specific hosts.
    allow = _allowed_hosts()
    if not allow or "*" in allow:
        return None
    if host in allow or _hostpart(host) in [_hostpart(a) for a in allow]:
        return None
    return tool_error(
        f"Host '{host}' is not in the SSH_ALLOWED_HOSTS restriction "
        f"({', '.join(allow)}). Add it to SSH_ALLOWED_HOSTS (or set '*' for any) — "
        f"do NOT work around this via code_execution."
    )


def _target(host: str) -> str:
    user = os.environ.get("SSH_USER", "").strip()
    return host if ("@" in host or not user) else f"{user}@{host}"


def _key_path() -> str:
    key = os.environ.get("SSH_KEY", "").strip()
    if key:
        return key
    return os.path.expanduser("~/.ssh/id_ed25519")


def _ssh_prefix(timeout: int, password: str | None, interactive: bool = False) -> list[str]:
    """ssh argv up to (not including) the target host."""
    opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", f"ConnectTimeout={min(int(timeout), 30)}",
    ]
    argv: list[str] = []
    if password:
        argv += ["sshpass", "-p", password]          # non-interactive password
    else:
        opts += ["-o", "BatchMode=yes"]              # key/no-prompt
        key = _key_path()
        if os.path.exists(key):
            opts += ["-i", key]
    if interactive:
        argv += ["ssh", "-tt", *opts]
    else:
        argv += ["ssh", *opts]
    return argv


def _password(args: dict) -> str | None:
    return (str(args.get("password") or "").strip()
            or os.environ.get("SSH_PASSWORD", "").strip() or None)


def _run(host: str, remote_cmd: str, timeout: int, password: str | None) -> str:
    argv = [*_ssh_prefix(timeout, password), _target(host), remote_cmd]
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=min(int(timeout), _MAX_TIMEOUT))
    except subprocess.TimeoutExpired:
        return tool_error(f"ssh to {host} timed out after {timeout}s")
    except FileNotFoundError as e:
        return tool_error(f"missing binary ({e.filename}) — need openssh-client / sshpass in image")
    except Exception as e:  # noqa: BLE001
        return tool_error(f"ssh to {host} failed: {e}")
    out = r.stdout or ""
    return tool_result({
        "host": host, "exit_code": r.returncode,
        "stdout": out[:_STDOUT_CAP], "stderr": (r.stderr or "")[-4000:],
        "truncated": len(out) > _STDOUT_CAP,
    })


# --- schemas -----------------------------------------------------------------

_HOST = {"type": "string", "description": "Target host ('host' or 'user@host'). Any host by default; restricted only if SSH_ALLOWED_HOSTS is set."}
_PW = {"type": "string", "description": "Optional login password (else key auth / SSH_PASSWORD)."}

SSH_RUN = {"name": "ssh_run", "description": "Run a shell command on an allowlisted host over SSH (one-shot, time-limited).",
           "parameters": {"type": "object", "properties": {
               "host": _HOST, "command": {"type": "string", "description": "Command for the remote shell."},
               "timeout": {"type": "integer", "description": "Seconds (default 15, max 300)."}, "password": _PW},
               "required": ["host", "command"]}}

SSH_READ_FILE = {"name": "ssh_read_file", "description": "Read a remote file over SSH (cat).",
                 "parameters": {"type": "object", "properties": {"host": _HOST,
                     "path": {"type": "string", "description": "Absolute remote path."}, "password": _PW},
                     "required": ["host", "path"]}}

SSH_LIST = {"name": "ssh_list", "description": "List a remote path over SSH (ls -la).",
            "parameters": {"type": "object", "properties": {"host": _HOST,
                "path": {"type": "string", "description": "Remote path."}, "password": _PW},
                "required": ["host", "path"]}}

SSH_PUT = {"name": "ssh_put", "description": "Write text content (or a local text file) to a remote path over SSH (upload). Creates parent dirs.",
           "parameters": {"type": "object", "properties": {"host": _HOST,
               "remote_path": {"type": "string", "description": "Destination path on the host."},
               "content": {"type": "string", "description": "Text to write (use this OR local_path)."},
               "local_path": {"type": "string", "description": "Local text file to upload (use this OR content)."},
               "password": _PW}, "required": ["host", "remote_path"]}}

SSH_START = {"name": "ssh_start", "description": (
    "Start a LONG-RUNNING command on a host over SSH as a background session. Returns a session_id "
    "immediately; the command keeps running. Read output with ssh_poll, feed stdin with ssh_send, "
    "kill with ssh_stop."),
    "parameters": {"type": "object", "properties": {"host": _HOST,
        "command": {"type": "string", "description": "Long-running command (e.g. a build, tail -f, a daemon in foreground)."},
        "interactive": {"type": "boolean", "description": "Allocate a TTY (for interactive programs). Default false."}, "password": _PW},
        "required": ["host", "command"]}}

SSH_POLL = {"name": "ssh_poll", "description": "Read NEW output from a background SSH session and its status (running/exit_code).",
            "parameters": {"type": "object", "properties": {
                "session_id": {"type": "string"}}, "required": ["session_id"]}}

SSH_SEND = {"name": "ssh_send", "description": "Send a line to a background SSH session's stdin (for interactive/long sessions).",
            "parameters": {"type": "object", "properties": {
                "session_id": {"type": "string"}, "data": {"type": "string", "description": "Text to write (newline appended)."}},
                "required": ["session_id", "data"]}}

SSH_STOP = {"name": "ssh_stop", "description": "Terminate a background SSH session.",
            "parameters": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}}

SSH_SESSIONS = {"name": "ssh_sessions", "description": "List active background SSH sessions.",
                "parameters": {"type": "object", "properties": {}, "required": []}}

SSH_KEYGEN = {"name": "ssh_keygen", "description": (
    "Generate an SSH keypair (ed25519) and return the PUBLIC key. Default path ~/.ssh/id_ed25519 "
    "(writable volume). Use ssh_copy_id to install it on a host."),
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Private key path (default ~/.ssh/id_ed25519)."},
        "comment": {"type": "string", "description": "Optional key comment."},
        "overwrite": {"type": "boolean", "description": "Overwrite if exists. Default false."}}, "required": []}}

SSH_COPY_ID = {"name": "ssh_copy_id", "description": (
    "Install our public key into a host's authorized_keys (enables key login). Uses login/password "
    "for this first connection (per-call `password` or SSH_PASSWORD)."),
    "parameters": {"type": "object", "properties": {"host": _HOST,
        "password": {"type": "string", "description": "Login password for the initial connection."},
        "key_path": {"type": "string", "description": "Private key whose .pub to install (default ~/.ssh/id_ed25519)."}},
        "required": ["host"]}}


# --- one-shot handlers -------------------------------------------------------

def handle_ssh_run(args, **kw):
    host, command = str(args.get("host") or "").strip(), str(args.get("command") or "")
    if not host or not command:
        return tool_error(
            "ssh_run needs 'host' and 'command'. "
            "Example: ssh_run(host='root@10.0.0.5', command='uptime', timeout=15). "
            "'host' must be in SSH_ALLOWED_HOSTS. For a long-running command use ssh_start instead."
        )
    if err := _gate(host):
        return err
    try:
        timeout = int(args.get("timeout", 15))
    except (TypeError, ValueError):
        timeout = 15
    return _run(host, command, timeout, _password(args))


def handle_ssh_read_file(args, **kw):
    host, path = str(args.get("host") or "").strip(), str(args.get("path") or "").strip()
    if not host or not path:
        return tool_error(
            "ssh_read_file needs 'host' and 'path'. "
            "Example: ssh_read_file(host='10.0.0.5', path='/etc/hostname')."
        )
    if err := _gate(host):
        return err
    return _run(host, f"cat -- {shlex.quote(path)}", 30, _password(args))


def handle_ssh_list(args, **kw):
    host, path = str(args.get("host") or "").strip(), str(args.get("path") or "").strip()
    if not host or not path:
        return tool_error(
            "ssh_list needs 'host' and 'path'. "
            "Example: ssh_list(host='10.0.0.5', path='/etc')."
        )
    if err := _gate(host):
        return err
    return _run(host, f"ls -la -- {shlex.quote(path)}", 20, _password(args))


def handle_ssh_put(args, **kw):
    host = str(args.get("host") or "").strip()
    remote = str(args.get("remote_path") or "").strip()
    if not host or not remote:
        return tool_error(
            "ssh_put needs 'host' and 'remote_path', plus 'content' or 'local_path'. "
            "Example: ssh_put(host='10.0.0.5', remote_path='/tmp/f.conf', content='...')."
        )
    if err := _gate(host):
        return err
    local = str(args.get("local_path") or "").strip()
    mkdir = f'mkdir -p "$(dirname -- {shlex.quote(remote)})"'
    if local:
        # binary-safe: base64 the local file, decode on the remote
        if not os.path.isfile(local):
            return tool_error(f"local_path not found: '{local}'. Pass an absolute path to an existing file.")
        import base64
        payload = base64.b64encode(open(local, "rb").read()).decode()
        remote_cmd = f"{mkdir} && base64 -d > {shlex.quote(remote)}"
        nbytes = os.path.getsize(local)
    elif args.get("content") is not None:
        payload = str(args.get("content"))
        remote_cmd = f"{mkdir} && cat > {shlex.quote(remote)}"
        nbytes = len(payload)
    else:
        return tool_error("ssh_put needs 'content' (text) or 'local_path' (a local file, binary ok).")
    argv = [*_ssh_prefix(30, _password(args)), _target(host), remote_cmd]
    try:
        r = subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return tool_error(f"ssh_put to {host} timed out")
    except FileNotFoundError as e:
        return tool_error(f"missing binary ({e.filename})")
    except Exception as e:  # noqa: BLE001
        return tool_error(f"ssh_put failed: {e}")
    if r.returncode != 0:
        return tool_error(f"ssh_put to {host}:{remote} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return tool_result({"host": host, "remote_path": remote, "bytes": nbytes})


# --- background sessions -----------------------------------------------------

def _reader(sid: str) -> None:
    sess = _SESSIONS.get(sid)
    if not sess:
        return
    proc = sess["proc"]
    for line in iter(proc.stdout.readline, ""):
        with sess["lock"]:
            sess["buf"].append(line)
            over = sum(len(x) for x in sess["buf"]) - _SESS_BUF_CAP
            while over > 0 and len(sess["buf"]) > 1:
                over -= len(sess["buf"].pop(0))
    proc.stdout.close()


def handle_ssh_start(args, **kw):
    host, command = str(args.get("host") or "").strip(), str(args.get("command") or "")
    if not host or not command:
        return tool_error(
            "ssh_start needs 'host' and 'command' (a long-running command). "
            "Example: ssh_start(host='10.0.0.5', command='journalctl -f'). "
            "It returns a session_id — read output with ssh_poll(session_id)."
        )
    if err := _gate(host):
        return err
    argv = [*_ssh_prefix(15, _password(args), interactive=bool(args.get("interactive"))),
            _target(host), command]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as e:
        return tool_error(f"missing binary ({e.filename})")
    except Exception as e:  # noqa: BLE001
        return tool_error(f"ssh_start failed: {e}")
    sid = uuid.uuid4().hex[:12]
    sess = {"proc": proc, "host": host, "command": command,
            "buf": [], "read_at": 0, "lock": threading.Lock(), "started": time.time()}
    with _LOCK:
        _SESSIONS[sid] = sess
    threading.Thread(target=_reader, args=(sid,), daemon=True).start()
    return tool_result({"session_id": sid, "host": host, "command": command,
                        "note": "running in background; use ssh_poll to read output"})


def handle_ssh_poll(args, **kw):
    sid = str(args.get("session_id") or "").strip()
    sess = _SESSIONS.get(sid)
    if not sess:
        return tool_error(
            f"No active session '{sid}'. Call ssh_sessions to list active session_ids "
            f"(a session disappears once its command exits — start one with ssh_start)."
        )
    with sess["lock"]:
        new = "".join(sess["buf"])
        sess["buf"].clear()
    rc = sess["proc"].poll()
    return tool_result({"session_id": sid, "running": rc is None, "exit_code": rc,
                        "new_output": new[-_STDOUT_CAP:], "truncated": len(new) > _STDOUT_CAP})


def handle_ssh_send(args, **kw):
    sid = str(args.get("session_id") or "").strip()
    data = str(args.get("data") or "")
    sess = _SESSIONS.get(sid)
    if not sess:
        return tool_error(
            f"No active session '{sid}'. Call ssh_sessions to list active session_ids "
            f"(a session disappears once its command exits — start one with ssh_start)."
        )
    if sess["proc"].poll() is not None:
        return tool_error(f"session {sid} has exited")
    try:
        sess["proc"].stdin.write(data + "\n")
        sess["proc"].stdin.flush()
    except Exception as e:  # noqa: BLE001
        return tool_error(f"write failed: {e}")
    return tool_result({"session_id": sid, "sent": data})


def handle_ssh_stop(args, **kw):
    sid = str(args.get("session_id") or "").strip()
    sess = _SESSIONS.get(sid)
    if not sess:
        return tool_error(
            f"No active session '{sid}'. Call ssh_sessions to list active session_ids "
            f"(a session disappears once its command exits — start one with ssh_start)."
        )
    try:
        sess["proc"].terminate()
    except Exception:  # noqa: BLE001
        pass
    with _LOCK:
        _SESSIONS.pop(sid, None)
    return tool_result({"session_id": sid, "stopped": True})


def handle_ssh_sessions(args, **kw):
    out = []
    for sid, s in list(_SESSIONS.items()):
        out.append({"session_id": sid, "host": s["host"], "command": s["command"],
                    "running": s["proc"].poll() is None,
                    "uptime_s": round(time.time() - s["started"])})
    return tool_result({"count": len(out), "sessions": out})


# --- key provisioning --------------------------------------------------------

def handle_ssh_keygen(args, **kw):
    path = str(args.get("path") or "").strip() or os.path.expanduser("~/.ssh/id_ed25519")
    comment = str(args.get("comment") or "gpio").strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not args.get("overwrite"):
        pub = path + ".pub"
        if os.path.exists(pub):
            return tool_result({"path": path, "existed": True, "public_key": open(pub).read().strip()})
    try:
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", path, "-N", "", "-C", comment],
                       capture_output=True, text=True, timeout=30, check=True,
                       input="y\n")
    except subprocess.CalledProcessError as e:
        return tool_error(f"ssh-keygen failed: {(e.stderr or e.stdout or '')[:300]}")
    except FileNotFoundError:
        return tool_error("ssh-keygen not found")
    return tool_result({"path": path, "public_key": open(path + ".pub").read().strip()})


def handle_ssh_copy_id(args, **kw):
    host = str(args.get("host") or "").strip()
    if not host:
        return tool_error("host is required")
    if err := _gate(host):
        return err
    key_path = str(args.get("key_path") or "").strip() or _key_path()
    pub = key_path + ".pub"
    if not os.path.exists(pub):
        return tool_error(f"public key not found: {pub} (run ssh_keygen first)")
    pubkey = open(pub).read().strip()
    remote = (f'umask 077; mkdir -p ~/.ssh && '
              f'grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys 2>/dev/null || '
              f'echo {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys')
    return _run(host, remote, 30, _password(args))
