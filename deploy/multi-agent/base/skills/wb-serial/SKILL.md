---
name: wb-serial
description: "Serial bus (RS-485/Modbus) on WB — custom templates, adding devices via confed, and diagnostics: CRC errors, timeouts, device not responding, slow polling, bus scan, health check."
allowed-tools: Bash Read Write WebFetch WebSearch
---

# wb-serial

## CRITICAL RULES

> **NEVER call `wb-cli` without `--json` from an agent.** Human-mode output is unparseable. Always `wb-cli --json <command>`, including help: `wb-cli --json <group> --help`.

**`<HOST>` variable:** `wirenboard-<SN>.local`, `<SN>` = serial number (e.g. `wirenboard-AABBCCDD.local`). Substitute the real address.

## When to load this skill

- **Templates**: "no template for the device", "add a third-party Modbus device", "create a template", custom registers, energy meter / thermometer templates.
- **Diagnostics**: Modbus errors, CRC, timeouts, "device not responding", data not updating, slow polling, read/write errors.

**IMPORTANT for diagnostics: act without pauses.** The user ALREADY asked for diagnostics — don't ask permission per step. Execute the full sequence (logs → debug → scan → health), report at the end.

## wb-cli — primary tool

`wb-cli serial` wraps every wb-mqtt-serial / wb-device-manager RPC the diagnostics flow needs; prefer it to raw MQTT RPCs.

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --port /dev/ttyRS485-1')     # bus scan (Fast Modbus)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --slow --timeout 300')       # exhaustive UART poll
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --bootloader')               # devices in bootloader mode
ssh_run(host='root@<HOST>', command='wb-cli --json serial config')                            # what's in /etc/wb-mqtt-serial.conf
ssh_run(host='root@<HOST>', command='wb-cli --json serial ports')                              # ports the driver currently serves
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52')                   # current firmware settings
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52 in0_mode=1')     # update firmware settings
ssh_run(host='root@<HOST>', command='wb-cli --json serial-debug --port /dev/ttyRS485-1 --seconds 60')
ssh_run(host='root@<HOST>', command='wb-cli --json dev wb-mr6c_52')                            # live values from the device
```

## MQTT RPC via Bash — fallback pattern

Use only for RPCs wb-cli does not wrap (custom drivers, ad-hoc exploration). Anything covered by `wb-cli serial …` goes through wb-cli.

```
ssh_run(host='root@<HOST>', command='CID=ai-$(date +%s)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d " "); mosquitto_sub -t "/rpc/v1/<driver>/<service>/<method>/$CID/reply" -C 1 -W <timeout> & sleep 0.2; mosquitto_pub -t "/rpc/v1/<driver>/<service>/<method>/$CID" -m '"'"'{"id":1,"params":{...}}'"'"'; wait')
```

---

# Part 1 — Templates and device configuration

## Where templates live

| Directory | What | Editable? |
|---------|-----|----------------|
| `/usr/share/wb-mqtt-serial/templates/config-<mqtt-id>.json` | Packaged WB and Onokom templates | NO — overwritten by `apt upgrade` |
| `/etc/wb-mqtt-serial.conf.d/templates/<any-name>.json` | Custom templates | Yes, survive upgrade |
| `/etc/wb-mqtt-serial.conf.d/confs/*.conf` | Custom parts of the main config | Less common |

`wb-mqtt-serial` scans both directories at start. A custom template with the same `device_type` as a packaged one **overrides** it.

Full template JSON layout (channel fields, parameters, groups, translations, endianness, string/varstring) — see **`references/template-format.md`**. The 6-step creation workflow, `fw-params` read/write, listing devices/ports, firmware-version lookup — see **`references/template-workflow.md`**.

## Adding a device to wb-mqtt-serial

> **CRITICAL: NEVER edit `/etc/wb-mqtt-serial.conf` manually or via raw confed API.** Templates have required parameters; missing them fails config validation → `ports/Load` returns `[]` → **all bus scans stop** until fixed. Always use `wb-cli serial add-devices` — it fills required params from template defaults.

### Standard workflow: scan → add

```
# Extended scan (WB Fast Modbus devices — default, fast)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan')
# Slow scan (third-party devices without Fast Modbus)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --slow --timeout 300')
# Add all found devices across every scanned port (one call)
ssh_run(host='root@<HOST>', command='wb-cli --json serial add-devices')
# Or limit to a single port
ssh_run(host='root@<HOST>', command='wb-cli --json serial add-devices --port /dev/ttyRS485-1')
```

`add-devices` reads the retained wb-device-manager state — run any scan type, then add without re-scanning. Devices already in config are skipped.

### Add a single device by model (no scan)

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial add-devices \
  --port /dev/ttyRS485-1 --device-type WB-MAI6 --slave-id 19')
```

Automatic-fixups table (baud, duplicate slave_id, conflict resolution) and the `add-devices` result shape — see **`references/template-workflow.md`**.

## Bus-level writes — `slave_id` and `baud rate`

WB devices expose two bus-level registers that aren't template parameters — `slave_id` (reg 128) and `baud rate / 100` (reg 110):

```
# Change slave_id
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-set-slave-id 5 19 --port /dev/ttyRS485-1')
# Collision-safe via Fast Modbus by SN (no need to know current address)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-set-slave-id 5 19 --port /dev/ttyRS485-1 --sn 0x00020B86')
# Change baud; speak to the device at its current baud
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-set-baud 5 9600 --port /dev/ttyRS485-1 --baud 19200')
```

**WB-only.** Both use the *WB Common Modbus Registers* convention. Won't work for non-Modbus devices (Энергомера IEC-61107, DOOYA) or third-party Modbus that change slave_id via another register. The `add-devices` baud-fixup and collision recovery use the same mechanism — also WB-only.

## Sending raw Modbus PDUs

```
# Read 1 holding register (FC3, reg 110)
ssh_run(host='root@<HOST>', command='wb-cli --json serial send-modbus --port /dev/ttyRS485-1 --slave 5 --fc 3 --reg 110')
# Read 10 input registers from a third-party device at 19200
ssh_run(host='root@<HOST>', command='wb-cli --json serial send-modbus --port /dev/ttyRS485-1 --slave 12 --fc 4 \
    --reg 0 --count 10 --baud 19200')
# Write a single holding register (FC6)
ssh_run(host='root@<HOST>', command='wb-cli --json serial send-modbus --port /dev/ttyRS485-1 --slave 5 --fc 6 \
    --reg 128 --value 19')
```

Supports FC3 / FC4 / FC6. For Fast Modbus or other FCs use raw `serial send`. Modbus devices only.

## Firmware update — `serial wb-fw`

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-fw check 4 --port /dev/ttyRS485-1')          # one device
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-fw check')                                    # every device
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-fw update 4 --port /dev/ttyRS485-1 --wait')   # flash blocking
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-fw update --all --background \
    --output /mnt/data/ai/wb-cli/fw-$(date +%s).json')                                # flash all in background
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-fw restore 4 --port /dev/ttyRS485-1 --wait')  # recover stuck bootloader
ssh_run(host='root@<HOST>', command='wb-cli mqtt sub /wb-device-manager/firmware_update/state')            # progress feed
```

## Loading / testing without restart

```
ssh_run(host='root@<HOST>', command='systemctl restart wb-mqtt-serial')
ssh_run(host='root@<HOST>', command='journalctl -u wb-mqtt-serial -n 50 --no-pager | grep -iE "(template|<device.id>)"')
```

---

# Part 2 — Diagnostics

## Start here — 7-step sequence

1. **Documentation** — `WebFetch("https://wirenboard.com/wiki/<DeviceModel>")` for "Known issues"; if nothing — `WebSearch("site:wirenboard.com/wiki/ <DeviceModel> <error>")`. Always cite the URL.
2. **Driver alive?** `ssh_run(host='root@<HOST>', command='systemctl is-active wb-mqtt-serial')`.
3. **Logs — count + tail + histogram by slave_id**:
   ```
   ssh_run(host='root@<HOST>', command="journalctl -u wb-mqtt-serial -p warning --since '1 hour ago' --no-pager | wc -l; echo ---; journalctl -u wb-mqtt-serial -p warning --since '1 hour ago' --no-pager | tail -30")
   ssh_run(host='root@<HOST>', command="journalctl -u wb-mqtt-serial -p warning --since '1 hour ago' --no-pager | grep -oP 'device modbus:\\K\\d+' | sort | uniq -c | sort -rn")
   ```
4. **Debug — raw packets. RUN IMMEDIATELY, DON'T ASK.** Duration depends on error rate from step 3:
   ```
   ssh_run(host='root@<HOST>', command='wb-cli --json serial-debug --port /dev/ttyRS485-1 --seconds <DURATION>')
   ```
   Duration heuristic table, long-capture job wrapping, `Debug` post-condition check — see **`references/diagnostics-deep.md`**.
5. **Bus scan** — `wb-cli --json serial wb-scan --port /dev/ttyRS485-1` (Fast Modbus) or `--slow` for third-party. `serial ports` for active ports.
6. **WB device health** — uptime regs 104-105, supply / min voltage regs 121-122 on relays/dimmers/MCM.
7. **Save the report** to `/mnt/data/ai/wb-ai-skills/diag/serial-diag.txt`.

Full step bodies (debug-duration table, raw-packet capture wrapping, health-check commands, save snippet), the **saw → do** table, **tools** (modbus_client_rpc, device/Probe, wb-modbus-scanner, modbus_client), the **useful WB device registers** table, and **experiments** (stop bits, speed, isolation, timeouts) — all in `references/diagnostics-deep.md`.

## Fast Modbus (WB extension protocol)

WB devices support a Modbus-RTU extension adding: scan-by-SN, targeted-PDU-by-SN (for slave_id collisions), and event push (low-latency diagnostics). All frames use broadcast address `0xFD` and command byte `0x46`. Use it when scan finds two devices with the same slave_id, or when a device is in scan but unresponsive to `modbus_client_rpc`.

Frame types table, exact byte sequences (change slave_id / baud by SN), the `port/Load` RPC envelope, when-to-use — see **`references/fast-modbus.md`**.

## Pitfalls

- Template in `/usr/share/wb-mqtt-serial/templates/` — overwritten on upgrade. Only use `/etc/wb-mqtt-serial.conf.d/templates/`.
- Endianness — most common error for u32/s32/float. Value jumps by a 65535 factor → `word_order: little_endian`.
- Scale in the wrong direction — test on one channel.
- Duplicate `device_type` — silently overrides packaged template. Use a prefix like `ACME-`.
- Cyrillic in `device.id` — forbidden (goes into topic name). Only `[a-z0-9-]`.
- Address 0-based vs 1-based — check the device spec.
- No `error_value` — device returning FFFF for "no data" shows 65535 as valid in MQTT.
- `modbus_client`/`wb-modbus-scanner` without stopping the driver → false errors.
- Forgotten debug → disk fills up.
- Wrong baud → COMPLETELY silent. Wrong stop bits → floating errors.
- RS-485 star topology works on short distances; for issues — recommend daisy chain.

## What the agent does NOT do

- **Edit `/etc/wb-mqtt-serial.conf` manually** (or via raw confed API). Use `wb-cli serial add-devices`; manual edits cause `ports/Load` to return `[]` and break all bus scans.
- **Run `modbus_client` / `wb-modbus-scanner`** while `wb-mqtt-serial` is active — they contend for the port → false errors. Stop the driver first (agree with the user — pauses ALL polling).
- **Leave debug mode on after a capture.** `wb-cli serial-debug` restores it automatically; raw-restart flows must re-disable explicitly or the journal fills the disk.
- **Use slave_id 0 outside the specific broadcast write.** Slave_id 0 = broadcast — every device responds (or none reply).
- **Pick `word_order: little_endian` by default.** Modbus is big-endian — override only when a multi-register value is observably wrong (jumps by 65535×).
- **Override a packaged template by reusing its `device_type`.** Custom ones silently win; use a prefix (`ACME-`, `BIDIR-`).

## When to ask the user

- About to broadcast-write reg 110 or 128 (slave_id 0) — confirm; hits every WB device on the bus.
- Firmware-update while production polling runs on the same bus — confirm the timing window.
- Experiments (stop bits, baud, port timeouts) — confirm rollback plan; back up `/etc/wb-mqtt-serial.conf` first.
- Adding a non-Modbus device to a port with Modbus devices — confirm per-device polling priorities; non-Modbus drivers can block Modbus scans.
- Scan shows duplicate slave_ids — surface candidates, confirm which keeps the address.
- Enabling a heavy polling channel on a slow device — confirm; rate-limit errors will fire.

## Documentation

- Template format: <https://github.com/wirenboard/wb-mqtt-serial/blob/master/docs/template.md>
- RS-485: <https://wiki.wirenboard.com/wiki/RS-485>
- Modbus: <https://wiki.wirenboard.com/wiki/Modbus>
- Common registers: <https://wiki.wirenboard.com/wiki/Common_Modbus_Registers>
- Diagnostics guide: <https://wiki.wirenboard.com/wiki/How_to_diagnose>
- Modbus FC spec: <https://modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf>
- Examples: `/usr/share/wb-mqtt-serial/templates/` on the controller (250+ templates).
