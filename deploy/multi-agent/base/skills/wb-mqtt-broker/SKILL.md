---
name: wb-mqtt-broker
description: "Mosquitto MQTT broker administration on Wiren Board — listeners, users, ACLs, password files, TLS, bridges to external brokers. /etc/mosquitto/conf.d/. Use when user mentions MQTT broker config, mosquitto, MQTT auth/password, MQTT TLS, external MQTT bridge, broker not running, MQTT client can't connect from outside."
allowed-tools: Bash Read Write WebFetch
---

# mqtt-broker

## CRITICAL RULES

> **NEVER call `wb-cli` without `--json` from an agent.** Human-mode output is unparseable; always `wb-cli --json <command>` — including help.

**`<HOST>`** = `wirenboard-<SN>.local`, `<SN>` = serial number (e.g. `wirenboard-AABBCCDD.local`). Substitute the real address.

`mosquitto` is the main MQTT broker through which all WB services and user apps communicate. Managed via `/etc/mosquitto/conf.d/*.conf` (DON'T edit `mosquitto.conf` directly).

## Config structure

```
/etc/mosquitto/mosquitto.conf            # includes 3 dirs in order:
  /usr/share/wb-configs/mosquitto/        # WB defaults (DON'T touch)
  /etc/mosquitto/conf.d/                  # user — write here
  /usr/share/wb-configs/mosquitto-post/   # WB post (DON'T touch)

/etc/mosquitto/conf.d/
├── 00default_listener.conf   # Unix socket for WB services — pre-configured, override only if sure
├── 10listeners.conf          # external listeners (1883, 8883) — yours
├── 20bridges.conf            # bridges to other brokers — yours
└── 21bridge.conf.example     # bridge template

/etc/mosquitto/passwd/        # password files (mosquitto_passwd -c)
/etc/mosquitto/acl/           # ACL files (topics per-user)
/etc/mosquitto/certs/         # TLS certificates (you'll create)
```

**Principle**: WB services talk via the Unix socket `/var/run/mosquitto/mosquitto.sock` (anonymously — 00default_listener); external clients via 1883/8883, where you do auth. Factory default: 1883 anonymous = broker open to the world; **for production, close it.**

## Basic commands

```bash
ssh root@<HOST> 'systemctl is-active mosquitto'
ssh root@<HOST> 'mosquitto -c /etc/mosquitto/mosquitto.conf -t'      # config check without starting
ssh root@<HOST> 'journalctl -u mosquitto -n 50 --no-pager'
ssh root@<HOST> "wb-cli --json mqtt sub '\$SYS/#' --count 5"        # broker system stats
ssh root@<HOST> "wb-cli --json mqtt sub '\$SYS/broker/clients/connected' --count 1"
```

### Reading/writing device values via wb-cli

For retained MQTT values (device controls, meta), prefer `wb-cli`:

```bash
ssh root@<HOST> wb-cli --json mqtt read '/devices/wb-mr6c_2/controls/K1'
ssh root@<HOST> wb-cli --json mqtt write '/devices/wb-mr6c_2/controls/K1/on' 1
ssh root@<HOST> wb-cli --json mqtt list '/devices/+/meta/name'
ssh root@<HOST> wb-cli --json dev wb-mr6c_2      # all controls with types and values
```

Use raw `mosquitto_sub`/`mosquitto_pub` only when `wb-cli` can't: config testing with `-u`/`-P` (wb-cli connects via Unix socket, not TCP), and TLS cert verification with `--cafile`.

### Verbose PUBLISH tracing — `wb-cli mqtt-debug`

To find **which MQTT client** publishes to a topic, use the `wb-cli mqtt-debug` plugin instead of hand-editing `conf.d/`. It writes the `log_type all` drop-in, restarts `mosquitto`, parses `Received PUBLISH …` lines into JSON, then restores the previous state.

```bash
ssh root@<HOST> "wb-cli --json mqtt-debug capture --seconds 60 \
    --topic '/devices/wb-mr6c_7/controls/K1' --client-id wb-rules"
```

For multi-filter captures, wildcards, persistent toggle (`enable`/`status`/`disable`), background captures with `--output`, the JSON schema, and the `client_id` → process table — see **`references/mqtt-debug.md`**.

## Authentication: passwords and ACL

External listeners (1883/8883) need `allow_anonymous false` + a password file (`mosquitto_passwd -c …`) + an ACL file (per-user topic permissions). `00default_listener.conf` keeps anonymous-allow over the Unix socket so WB services aren't affected — `per_listener_settings true` enables this; don't reset it. ACL/passwords reload on `systemctl reload`; listener/TLS/bridge changes need `restart`.

Full procedure (password file, listener edit, test; ACL example with admin/frontend/external_app) — see **`references/auth.md`**. A `restart` takes ~1s downtime; WB services on the Unix socket survive it.

## TLS on port 8883 and bridges to other brokers

TLS listener on 8883 uses a CA + server cert (self-signed for home, Let's Encrypt for production) plus the same password/ACL files. A bridge makes mosquitto connect out to another broker and copy selected topics in/out — replication to Home Assistant, cloud, backup broker. `cleansession false` keeps QoS≥1 messages across disconnects.

Full cert generation, listener/bridge config (Home Assistant example), and TLS-on-bridge — see **`references/tls-and-bridges.md`**.

## Checking state and active clients

```bash
ssh root@<HOST> "wb-cli --json mqtt sub '\$SYS/broker/+' --count 20 --timeout 2"
# $SYS/broker/clients/connected, $SYS/broker/messages/received/1min etc.
ssh root@<HOST> wb-cli --json dev                   # all WB devices with names (faster than raw sub)
```

`mosquitto_sub` without `-u` against a closed listener → refused. To hit the Unix socket: `mosquitto_sub -L mqtt://localhost:1883/<topic>` (the `.sock` path directly fails on some versions — easier via 1883).

## Backup and FIT

`/etc/mosquitto/conf.d/`, `passwd/`, `acl/`, `certs/` do NOT survive FIT; `/wb-controller-backup` picks them up (core-tar captures them as modified configs). Full FIT-survival list: `wb-controller-backup` skill.

## Pitfalls and guardrails

- **Never set `per_listener_settings false`** (package default) — makes `allow_anonymous` global with no separate socket mode; the WB `true` value keeps the socket-anonymous + 1883-authed split, and flipping it locks out internal WB services. Closing 1883 anonymous is safe — WB services use the socket.
- **`mosquitto_passwd -c` overwrites all users** — use `-c` only to create a fresh file, add later users without it; without `-c` on a nonexistent file the password isn't saved.
- **`password_file`/`acl_file` apply on `systemctl reload`** — no `restart` for ACL/password changes.
- **ACL without explicit `topic deny #`** — anonymous (if allow_anonymous true) gets full readwrite by default.
- **Bridge without `cleansession false`** — QoS≥1 messages dropped on disconnect.
- **`try_private true`** is mosquitto.conf-only — for non-mosquitto brokers leave `false`.
- **`bridge_insecure true`** disables hostname verification — one-off debugging only.
- **TLS cert expired** — `journalctl -u mosquitto` shows it, clients get `tls handshake failure`. Renew via certbot or regenerate self-signed.
- **`passwd/default.conf` perms** must be `mosquitto:mosquitto 0640`, else mosquitto can't read it (`Unable to open password file ... Permission denied`).
- **Never commit broker passwords / TLS keys** — keep `passwd/`, `certs/`, `bridge_password` out of version control.

## When to ask the user

- About to open port 1883 with `allow_anonymous true` on a network-reachable listener — confirm; exposes the broker to LAN.
- Self-signed TLS vs Let's Encrypt — depends on whether the controller has a public DNS name; ask.
- Enabling a bridge — confirm credentials and topic prefix (typos here are silent).
- Replacing existing ACL with a stricter one — surface which users/services lose access (frontend, external_app may go quiet).
- Certificate expiry < 30 days — ask whether to renew or accept the warning.

## Documentation

- mosquitto.conf: `man mosquitto.conf`, <https://mosquitto.org/man/mosquitto-conf-5.html>
- ACL: <https://mosquitto.org/documentation/dynamic-security/>
- mosquitto_passwd: <https://mosquitto.org/man/mosquitto_passwd-1.html>
- Bridges: <https://mosquitto.org/documentation/bridges/>
