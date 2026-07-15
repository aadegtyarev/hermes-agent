---
name: wiren-board
description: "Master skill for Wiren Board (WB) controllers — load FIRST whenever user mentions Wiren Board, wirenboard.com, wb6/wb7/wb8/wb-msw hardware, or any wb-* tool. Provides: controller discovery via mDNS, SSH access patterns, wb-cli usage (always --json from an agent!), documentation lookup, common troubleshooting entry point."
allowed-tools: Bash Read Write WebFetch WebSearch
---

# Wiren Board — master skill

## CRITICAL RULES

> **NEVER call `wb-cli` without `--json` from an agent.**
> Human-mode output is unparseable. Always use `wb-cli --json <command>` — including help: `wb-cli --json <group> --help`.

You manage **Wiren Board** home/building automation controllers over SSH.

## Discovery

Find all controllers on the local network (mDNS):

```bash
avahi-browse _workstation._tcp -tpr 2>/dev/null | grep '^=' | grep -i 'wirenboard-' | awk -F';' '{print $7, $8}'
```

Controllers announce as `_workstation._tcp` (not `_wirenboard._tcp`). Output:
`wirenboard-<SN>.local <IP>`. Serial: 8 chars (e.g. `AABBCCDD`); hostname
`wirenboard-<SN>`.

**`.local` names resolve natively** — libnss-mdns is wired into NSS, so just
`ssh wirenboard-<SN>.local` or `getent hosts wirenboard-<SN>.local`; no need to
dig out the IP first. (Requires `network_mode: host`.)

If avahi returns nothing (different subnet, mDNS not forwarded, or not on host
networking):

```bash
# Resolve one controller by serial number
avahi-resolve -n wirenboard-AABBCCDD.local

# TCP-scan the subnet (no ICMP/root — `ping`/`nmap -sn` need cap_net_raw the
# container lacks; `nmap -sT` uses a normal connect() and works):
nmap -sT -p22 --open 192.168.1.0/24 2>/dev/null | grep -B4 open
```

## SSH convention

Default credentials: **user `root`, password `wirenboard`**.

**Always follow this order when connecting:**

```
# Step 1: key-based auth (the ssh plugin never blocks on a GUI prompt)
ssh_run(host='root@<HOST>', command='wb-cli --json info')

# Step 2: if step 1 fails — default password
ssh_run(host='root@<HOST>', command='wb-cli --json info', password='wirenboard')

# Step 3: only if both fail — ask the user for credentials
```

The `ssh` plugin manages host keys and never triggers an interactive/GUI password dialog, so no `BatchMode` / `StrictHostKeyChecking` / `ConnectTimeout` options are needed.

### SSH key-based auth

```
ssh_copy_id(host='root@<HOST>')          # installs the local public key (run ssh_keygen() first if none exists)
```
After this, step 1 succeeds and password is never needed. To disable password auth — edit `/etc/ssh/sshd_config` (`ssh_run`): set `PasswordAuthentication no`, then `systemctl restart sshd`.

## wb-cli — the primary tool

Runs **on the controller**. Check: `ssh_run(host='root@<HOST>', command='command -v wb-cli && wb-cli --version')`. If missing — install (below).

**Rule: before first use of any command group — `wb-cli --json <group> --help`.**

### Install wb-cli on a controller

Try apt first; if the package isn't in the controller's repos, fall back to the latest GitHub release:

```
# 1) Try the apt repo
ssh_run(host='root@<HOST>', command='apt update && apt -y install wb-cli')

# 2) If that fails — fallback: pull the latest .deb from GitHub Releases
ssh_run(host='root@<HOST>', command='set -e
  cd /tmp
  URL=$(curl -fsSL https://api.github.com/repos/wirenboard/wb-ai-skills/releases/latest \
        | grep -oE "https://[^\"]+wb-cli_[^\"]+\.deb" | head -1)
  [ -n "$URL" ] || { echo "no .deb in latest release" >&2; exit 1; }
  curl -fsSL -o wb-cli.deb "$URL"
  apt install -y ./wb-cli.deb || dpkg -i wb-cli.deb || {
    apt install -y -f
    dpkg -i wb-cli.deb
  }
  wb-cli --version')
```

Notes:
- `wb-cli` is an `_all.deb` (arch-independent), works on any wb6/wb7 firmware ≥ bullseye.
- Runtime deps (`python3-mqttrpc`, `python3-wb-common`, `mosquitto-clients`, `jq`) live in the wirenboard apt repo, preconfigured on every controller — `apt install -y -f` resolves them.
- Verify with `wb-cli --json info` (returns serial number, fw, uptime).

### Output contract

`wb-cli` defaults to human-friendly output. **Always pass `--json` (or set `WB_CLI_OUTPUT=json`)** from an agent / script:

```
ssh_run(host='root@<HOST>', command='wb-cli --json dev')
```

- Success: `{"data": {...}}` — object, `snake_case` keys.
- Error: `{"error": {"code": "SCREAMING_SNAKE", "message": "...", "details": {...}}}` — `hint` optional.
- Exit codes: 0 success, 1 domain, 2 usage, 3 environment.

### Key commands

For the full list run `wb-cli --help` or `wb-cli <group> --help` on the controller — always up to date. Common entry points: `info`, `dev`, `mqtt`, `mqtt-debug`, `rules`, `serial` (incl. `serial wb-fw` firmware update, formerly the standalone `modbus-fw` plugin), `serial-debug`, `audit`, `snapshot`, `job`, `confed`, `history`, `cloud`, `plugins`.

Addressing uses the wb-rules form `<device>/<control>`. Quote names with spaces:

```
ssh_run(host='root@<HOST>', command='wb-cli --json info')
ssh_run(host='root@<HOST>', command='wb-cli --json dev wb-adc/Vin')          # read one control
ssh_run(host='root@<HOST>', command="wb-cli --json dev 'wb-mdm3_5/Channel 1 Dimming Level' 30")
ssh_run(host='root@<HOST>', command='wb-cli --json audit')
```

### Standard Linux — use SSH directly

```
ssh_run(host='root@<HOST>', command='systemctl status wb-mqtt-serial')
ssh_run(host='root@<HOST>', command='journalctl -u wb-rules -n 50')
ssh_run(host='root@<HOST>', command='docker ps')
ssh_run(host='root@<HOST>', command='apt install <package>')
ssh_run(host='root@<HOST>', command='ip addr show')
```

## Docker on WB

Standard Docker CE installed via `wb-docker-manager.sh`. Key WB-specific rule: **all Docker data and compose projects go in `/mnt/data/`** (larger partition, survives firmware updates).

```
ssh_run(host='root@<HOST>', command='docker compose -f /mnt/data/homeassistant/docker-compose.yml up -d')
```

## Firmware / package upgrade

There is **no `wb-update-manager` command** — it does not exist. Use standard apt:

```
# Check what can be updated (fast — a plain ssh_run is fine)
ssh_run(host='root@<HOST>', command='apt update -qq && apt --simulate upgrade | grep "^Inst"')

# Install updates — takes minutes, use job:
ssh_run(host='root@<HOST>', command='wb-cli --json job run apt-upgrade "apt update && apt -y upgrade 2>&1"')
ssh_run(host='root@<HOST>', command='wb-cli --json job wait apt-upgrade')
ssh_run(host='root@<HOST>', command='wb-cli --json job tail apt-upgrade')
```

**WB release model:** a release (e.g. `wb-2602` = February 2026) is a named snapshot of package versions. There are **no packages named `wb-2603-repo` or similar** — do not search for them in apt.

To check which releases exist and which is latest — fetch this URL (no auth; never use `gh api`/`gh cli`):

```
WebFetch("https://raw.githubusercontent.com/wirenboard/wb-releases/main/releases.yaml")
```

Top-level keys under `releases:` are the available release names; the topmost is the latest. Running `apt upgrade` updates packages within the current release track and bumps `release_name` when WB publishes a new release to the stable repo.

After a reboot, verify: `wb-cli --json info`. If kernel mismatch after upgrade — see `wb-troubleshooting` skill.

## Factory reset

**Erases /etc and /mnt/data/etc. Back up first (see wb-controller-backup skill).**
```
ssh_run(host='root@<HOST>', command='wb-cli --json job run factory-reset "wb-factoryreset 2>&1"')
```
Controller reboots with default config. SSH works with default credentials (root/wirenboard).

## apt package pinning

```
ssh_run(host='root@<HOST>', command='apt-mark hold <package>')
ssh_run(host='root@<HOST>', command='apt-mark showhold')       # list held packages
ssh_run(host='root@<HOST>', command='apt-mark unhold <package>')
```

## Troubleshooting patterns

### Kernel mismatch

Kernel mismatch after upgrade — see `wb-troubleshooting` skill.

### Docker iptables fix (after kernel is OK)

```
ssh_run(host='root@<HOST>', command='update-alternatives --set iptables /usr/sbin/iptables-legacy && systemctl restart docker')
```

### Quick diagnostic sequence

```
ssh_run(host='root@<HOST>', command='wb-cli --json audit')            # failed units + identity in one call
ssh_run(host='root@<HOST>', command='df -h / /mnt/data')              # disk: only thing audit does not check yet
```

`wb-cli audit` enumerates failed services (in the `failed_units` check), so a separate `systemctl --failed` is redundant.

## Documentation lookup

Before fixing an unfamiliar component, check the wiki:

```
WebFetch('https://wiki.wirenboard.com/wiki/<Component>')
```

Common pages: `Docker`, `Modbus`, `Home_Assistant`, `Wiren_Board_Cloud`, `wb-rules`.

## Specialized skills

| Need | Skill |
|---|---|
| wb-rules JavaScript automation (defineRule, virtual devices, cron, ES5) | `/wb-rules` |
| General troubleshooting (failed services, disk, kernel, Docker) | `/wb-troubleshooting` |
| RS-485 / Modbus: templates, confed config, diagnostics (CRC, timeouts) | `/wb-serial` |
| Network setup (WiFi, 4G/GSM, VPN, failover, modem diagnostics) | `/wb-network` |
| MQTT broker config (auth, ACL, TLS, bridges to HA/cloud) | `/wb-mqtt-broker` |
| Full controller backup and restore | `/wb-controller-backup` |
| Zigbee devices via zigbee2mqtt (pairing, OTA, native vs Docker) | `/wb-zigbee` |
| Software development / integrations for WB (custom daemons, protocol bridges, MQTT conventions, MQTT-RPC, Debian packaging) | `/wb-dev` |

## Third-party integrations

For components without a dedicated skill — start with `WebFetch` on the WB wiki page, then standard component docs:

| Component | Wiki page |
|---|---|
| InfluxDB, Grafana | `WebFetch('https://wiki.wirenboard.com/wiki/InfluxDB')` |
| Node-RED | `WebFetch('https://wiki.wirenboard.com/wiki/Node-RED')` |
| Home Assistant | `WebFetch('https://wiki.wirenboard.com/wiki/Home_Assistant')` |
| nginx / SSL on controller | `WebFetch('https://wiki.wirenboard.com/wiki/Nginx')` + standard nginx / certbot docs |
| Docker on controller | `WebFetch('https://wiki.wirenboard.com/wiki/Docker')` |

**Installing software on a controller** (small root partition):
1. WB-packaged `.deb` from the wirenboard apt repo (e.g. `zigbee2mqtt` built by WB maintainers) — preferred.
2. Docker — **follow the WB wiki, not generic Docker docs**: `WebFetch('https://wiki.wirenboard.com/wiki/Docker')`. Docker stores images in `/mnt/data` (not root); generic `apt install docker-ce` breaks this. Use `docker compose` from `/mnt/data/etc/docker/`.
3. Direct `apt install` of standard Debian packages — only if small and in the wirenboard repo. Large packages (Node.js runtimes, databases) go via Docker or `/mnt/data`.

## Safety

- **Back up before destructive operations** (confed save, rules delete, modbus add-devices).
- **Never write to MQTT controls you don't understand** — some drive physical outputs.
- **Any operation expected to take more than 20–30 seconds** — use `wb-cli job run` (see Firmware / package upgrade), not plain SSH: `apt update`, `apt upgrade`, `apt install`, tar archives, modbus firmware updates, factory reset. Plain SSH will appear to hang, block the agent, and may timeout mid-operation, leaving the system inconsistent.

## What the agent does NOT do

- **Call `wb-cli` without `--json`.** Human-mode output is unparseable — half the WB agent failures trace back to this.
- **Run long-running ops via plain SSH.** `apt`, tar, modbus firmware update — wrap in `wb-cli job run`.
- **Install Docker via generic `apt install docker-ce`.** Breaks `/mnt/data` storage. Use `wb-docker-manager.sh`.
- **Write to MQTT controls without understanding the effect.** Some `/devices/.../on` writes close a relay → drive a real physical output.
- **Trigger factory reset / FIT upgrade** without explicit user confirmation — both are minutes-long, destructive.
- **Decide which controller to act on** when discovery returns multiple — surface the list.
- **Modify `git config`, SSH keys, or `/root/.ssh/authorized_keys`** as a side effect of another operation.

## When to ask the user

- Discovery returns more than one controller — ask which one.
- About to run a firmware (FIT) upgrade or factory reset — confirm strongly; both rewrite the rootfs.
- About to `apt upgrade` on a production controller — confirm window; restart-required packages bounce services.
- The request maps to two skills (e.g. "no internet" → `wb-network` or `wb-troubleshooting`) — confirm scope.
- A wb-* tool isn't behaving as documented — surface the discrepancy; don't silently rewrite logic around it.
