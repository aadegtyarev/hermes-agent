---
name: wb-troubleshooting
description: "General Wiren Board controller diagnostics — failed systemd services, low disk space, kernel/firmware mismatch, Docker, iptables, diagnostic archive (wb-diag-collect), boot issues, web UI inaccessible. Use when user says controller is broken, not working, service down, asks for logs for support, or needs a diagnostic archive. NOT for serial/Modbus (use wb-serial), NOT for network-only issues (use wb-network)."
allowed-tools: Bash Read Write WebFetch WebSearch
---

# troubleshooting

## CRITICAL RULES

> **NEVER call `wb-cli` without `--json` from an agent.**
> Human-mode output is unparseable. Always use `wb-cli --json <command>` — including help: `wb-cli --json <group> --help`.

General diagnostics for a Wiren Board controller. Load when the user says: "doesn't work", "fix it", "broken", "error", "won't start", "service crashed", "collect diagnostics", "diagnostic archive", "logs and state" — and it's NOT serial/Modbus (use `wb-serial`).

Don't confuse with backup (`wb-controller-backup`). The diagnostic archive is for analysis and support, not restore. Collected by `wb-diag-collect`; includes configs from `/etc`, service logs (wb*, mosquitto, NetworkManager, etc.), and diagnostic command output (df, ps, ip, dpkg, etc.).

**HOST variable:** in all examples `<HOST>` means `wirenboard-<SN>.local`, `<SN>` = serial number (e.g. `wirenboard-AABBCCDD.local`). Substitute the real address.

## First steps — always

Find the cause before fixing. Don't fix symptoms.

### 0. Quick health check

```
ssh_run(host='root@<HOST>', command='wb-cli --json audit')
```

Runs automated checks (failed units, controller identity). If wb-cli is not installed, use the manual steps below.

### 0a. Documentation — MANDATORY

**Before any fix**, `WebFetch` the wiki page of the problem component — e.g. Docker `WebFetch('https://wiki.wirenboard.com/wiki/Docker')`, Modbus `WebFetch('https://wiki.wirenboard.com/wiki/Modbus')`, Home Assistant `WebFetch('https://wiki.wirenboard.com/wiki/Home_Assistant')`. Look for "Known issues", "Troubleshooting", "Limitations". If a solution is there — apply it, don't invent your own.

### 1. Kernel mismatch

**The most common cause of issues after upgrade.** Check first:

```
ssh_run(host='root@<HOST>', command='echo "running: $(uname -r)"; dpkg -l "linux-image-wb*" 2>/dev/null | grep ^ii | awk "{print \"installed:\", \$3}"')
```

If versions don't match — the controller is on the old kernel. Kernel modules (br_netfilter, iptable_nat, can, i2c, etc.) won't load; Docker/iptables/network may fail. **The only fix is a reboot.** Don't work around via modprobe/iptables-legacy — useless under kernel mismatch.

### 2. Disk space

```
ssh_run(host='root@<HOST>', command='df -h / /mnt/data')
```

`use% > 95%` or free `< 100 MB` (typical 2 GB rootfs) is critical: apt fails, logs aren't written, services crash. Look at percent used, not absolute values — `/` size depends on platform (wb6 — 2 GB, wb7/wb8 — 2 GB, old builds can be ~700 MB). Cleanup: `apt clean; journalctl --vacuum-time=3d; rm -rf /tmp/*`.

### 3. Failed services

```
ssh_run(host='root@<HOST>', command='systemctl --failed --no-pager')
```

For each failed unit — two queries (together they give the full picture):
```
ssh_run(host='root@<HOST>', command='systemctl status <unit> --no-pager')        # exit code, Result, ExecMainStatus — short summary
ssh_run(host='root@<HOST>', command='journalctl -u <unit> -n 50 --no-pager')    # detailed logs with the failure cause
```

`systemctl status` for a failed unit returns exit code 3 — that's **normal** (systemctl status code, not an ssh error). Don't confuse it with a real connection error when automating.

### 4. Error journal

```
ssh_run(host='root@<HOST>', command="journalctl -p err --since '1 hour ago' --no-pager")
```

Without `--since`, `journalctl` returns N latest lines regardless of age — they may be week-old errors. Pick the period by context (`'10 minutes ago'`, `'today'`, `'1 hour ago'`).

### 5. Load and memory

```
ssh_run(host='root@<HOST>', command='uptime; free -h')
```

Load > 4 on WB — overloaded.
```
ssh_run(host='root@<HOST>', command='top -bn1 | head -20')
```
Shows who's eating CPU.

## Typical issues

| Symptom | First step |
|---|---|
| Service won't start after upgrade | Kernel mismatch -> reboot |
| Docker won't start, iptables errors | First kernel mismatch. If kernel OK — iptables-legacy fix (below) |
| modprobe: module not found | Kernel mismatch -> reboot |
| apt doesn't work, dpkg lock | `fuser /var/lib/dpkg/lock-frontend` — who holds it. Zombie from interrupted apt: `dpkg --configure -a` |
| Service crashes in a loop | `journalctl -u <unit> -n 100` — find the cause, don't restart blindly |
| `fstrim.service` failed, `status=64/USAGE` | An `/etc/fstab` entry points to a physically absent partition (typically `/mnt/sdcard` without an SD). `fstrim --listed-in /etc/fstab` fails before reaching other mount points. Check `mount` and `ls /dev/mmcblk1*`. Fix: remove the fstab line or drop-in with `ExecStart=/sbin/fstrim --fstab --quiet-unsupported` |
| No network | `ip addr`, `nmcli`, `ping 8.8.8.8`, `cat /etc/resolv.conf` |
| MQTT doesn't work | `systemctl is-active mosquitto`, `wb-cli --json audit` |
| Web UI doesn't open | `systemctl is-active nginx wb-mqtt-homeui` |

## Docker and iptables

If Docker won't start with errors like `Chain 'MASQUERADE' does not exist`, `DOCKER-ISOLATION-STAGE`, `Failed to Setup IP tables` — and kernel mismatch is ruled out:

1. Switch iptables to legacy:
```
ssh_run(host='root@<HOST>', command='update-alternatives --set iptables /usr/sbin/iptables-legacy && update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy')
```

2. Create the missing NAT rule:
```
ssh_run(host='root@<HOST>', command='iptables -w10 -t nat -I POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE')
```

3. Restart Docker:
```
ssh_run(host='root@<HOST>', command='systemctl restart docker && systemctl is-active docker')
```

If that didn't help — reboot:
```
ssh_run(host='root@<HOST>', command='reboot')
```

More: <https://wiki.wirenboard.com/wiki/Docker>.

## Diagnostic archive

**Collect ONLY in two cases:**
1. The user explicitly asks for the "diag archive" / "diagnostic archive".
2. Composing a bug report — the archive is mandatory as an attachment together with issue-specific logs.

Otherwise (diagnosis, root cause, fix) — **don't create the archive**; work with logs directly via SSH.

Collection takes 30-60 seconds; run as a background task:
```
ssh_run(host='root@<HOST>', command='systemd-run --unit=wb-ai-job-$(cat /dev/urandom | tr -dc a-z0-9 | head -c8) --collect bash -c "wb-diag-collect /tmp/diag"')
```

`wb-diag-collect` treats the argument as a **prefix** and appends `_SN_DATE.zip` — the actual name isn't known in advance.

After completion — find and download the file:
```
ssh_run(host='root@<HOST>', command='ls /tmp/diag*.zip | tail -1')
ssh_read_file(host='root@<HOST>', path='<path from ls output>')   # download the archive
```

## What the agent does NOT do

- **Fix symptoms before identifying the root cause.** "Restarting made it work" is not a fix — surface the root cause.
- **Collect the diagnostic archive unless the user asks** or it's a bug report. The archive is heavy; use direct SSH for routine diagnosis.
- **`rm` files to free disk space without showing the user what's being deleted** — especially under `/mnt/data/`, `/var/log/`.
- **Restart services blindly** in a "try everything" loop — each restart loses the chance to read the failure state.
- **Run `reboot`** without the user's explicit OK — the controller may not come back cleanly (FIT in progress, broken filesystem).
- **Edit configs** to "see if it helps". Back up first, then change one thing at a time.
- **Trust the kernel mismatch warning silently** — surface it; firmware/kernel skew is a known cause of obscure failures.

## When to ask the user

- Root cause is uncertain — propose a hypothesis and ask before testing.
- The fix requires a service restart that interrupts production (mqtt-serial, wb-rules, mosquitto) — confirm window.
- About to clear or aggressively rotate logs — confirm.
- The diagnostic archive contains MQTT passwords / API tokens in configs — confirm whether to redact before sending to support.
- The problem requires a full reboot — confirm timing; the controller is offline for ~60 seconds.

## Principle

Diagnose -> read documentation -> explain the cause -> propose a solution -> wait for confirmation. Don't fix blindly.
