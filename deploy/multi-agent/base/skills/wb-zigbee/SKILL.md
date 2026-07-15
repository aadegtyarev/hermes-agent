---
name: wb-zigbee
description: Zigbee devices on WB via zigbee2mqtt — bridge/state liveness probe, native vs Docker install detection, wb-mqtt-zigbee vs wb-zigbee2mqtt converter recognition, IEEE-address (0x...) and zigbee_<id> topic patterns, pairing, control, OTA.
allowed-tools: Bash Read WebFetch
---

# zigbee

## CRITICAL RULES

> **NEVER call `wb-cli` without `--json` from an agent.** Human-mode output is unparseable; always `wb-cli --json <command>` — including help: `wb-cli --json <group> --help`.

**`<HOST>` variable:** `<HOST>` means `wirenboard-<SN>.local`, `<SN>` = serial number (e.g. `wirenboard-AABBCCDD.local`). Substitute the real address.

Zigbee devices on a Wiren Board controller via zigbee2mqtt.

## Architecture

**zigbee2mqtt** talks to the Zigbee adapter via `/dev/ttyMOD<N>` and publishes to `zigbee2mqtt/<friendly_name>`. It runs **either natively** (`systemctl is-active zigbee2mqtt`) **or in Docker** (`docker ps | grep zigbee`) — both occur. `systemctl` doesn't determine the method: it shows `inactive` for a containerized install even when the bridge works.

WB converters turn Z2M devices into native WB MQTT (`/devices/...`) so wb-rules and the web UI can see them:

| Converter | Topic prefix | Notes |
|-----------|---------------|-------------|
| **wb-mqtt-zigbee** (new) | `/devices/zigbee_*/controls/*` | Bidirectional controls, via `/on` |
| **wb-zigbee2mqtt** (old, `1.x`) | `/devices/0x<ieee>/controls/*` (topic name = full IEEE address) | Read-only bridge, control via `wb-cli mqtt write zigbee2mqtt/<friendly>/set` |

Determine which is installed via `dpkg -l | grep -E 'wb-(mqtt-zigbee\|zigbee2mqtt)'` and check device names:
```bash
ssh root@<HOST> wb-cli --json mqtt list '/devices/+/meta/name'
```
`0x...` = old converter (`wb-zigbee2mqtt`), `zigbee_<id>` = new converter (`wb-mqtt-zigbee`).

## How to identify

- MQTT devices named `0x00158d...`, `0x00124b...`, `0x04cd15...`, `0xd44867...` — IEEE addresses (Zigbee).
- Topics `zigbee2mqtt/bridge/state`, `.../bridge/devices`, `.../bridge/info` — published by Z2M itself, independently of the WB converter.

## Bridge probe

**True liveness check is `bridge/state`, not `systemctl`:**

```bash
ssh root@<HOST> wb-cli --json mqtt read 'zigbee2mqtt/bridge/state'
```

Expected: `{"state":"online"}` (or just `online` on older versions). Empty/timeout → bridge dead or no MQTT connectivity.

Only if `bridge/state` is empty, find **where** Z2M lives:

```bash
ssh root@<HOST> 'systemctl is-active zigbee2mqtt 2>&1; docker ps --format "{{.Names}} {{.Status}}" 2>/dev/null | grep -i zigbee'
```

One (or both) answers. Then `journalctl -u zigbee2mqtt -n 50` or `docker logs --tail 50 zigbee2mqtt`.

## Bridge and device info

`bridge/devices` is a large JSON (tens of KB). Don't `head -c 200` — that gives broken JSON. Write it whole:

```bash
ssh root@<HOST> wb-cli --json mqtt read 'zigbee2mqtt/bridge/devices' | jq -r '.data.payload' > /tmp/z2m-devices.json
ssh root@<HOST> wb-cli --json mqtt read 'zigbee2mqtt/bridge/info'    | jq -r '.data.payload' > /tmp/z2m-info.json
```

**Parse via jq** (present on all current WB firmwares):

```bash
# friendly_name | ieee | model | vendor
jq -r '.[] | select(.type != "Coordinator") | [.friendly_name, .ieee_address, .definition.model // "?", .definition.vendor // "?"] | @tsv' /tmp/z2m-devices.json
```

If `jq` is missing (minimal/old image), copy the `.json` locally and parse there (nesting python f-strings in one SSH call breaks on quoting).

`bridge/info` has: `version` (Z2M), `coordinator.type` (ZStack3x0, EmberZNet, etc.), `permit_join` (should be `false` when idle), `restart_required`, `config.availability.enabled`.

**`last_seen` per-device** — in `bridge/devices` **only if** `availability.enabled: true` in `configuration.yaml`. Disabled by default — absence **doesn't mean** offline.

## Current device values

### Via wb-cli (preferred for WB-converted devices)

```bash
ssh root@<HOST> wb-cli --json dev zigbee_<id>          # new wb-mqtt-zigbee converter
ssh root@<HOST> 'wb-cli --json dev "0x<ieee>"'         # old wb-zigbee2mqtt converter
```

Returns all controls with values, types, and error flags in JSON.

### Via raw MQTT (Z2M-native data only — no WB converter)

```bash
ssh root@<HOST> wb-cli --json mqtt read 'zigbee2mqtt/<friendly_name>'
```

## Controlling a device

```bash
# Via wb-cli (if wb-mqtt-zigbee converter present):
ssh root@<HOST> wb-cli --json dev zigbee_<id>/<channel> <value>

# Via raw MQTT (WB converter):
ssh root@<HOST> "wb-cli --json mqtt write '/devices/zigbee_<id>/controls/<channel>/on' '<value>'"

# Via Z2M directly (always works, even without WB converter):
ssh root@<HOST> "wb-cli --json mqtt write 'zigbee2mqtt/<friendly_name>/set' '{\"state\":\"ON\"}'"
```

## Pairing

⚠️ **This changes bridge state.** Coordinate with the user first — after `permit_join: true` any Zigbee device in range can join without authorization.

```bash
# Enable pairing for 4 minutes:
ssh root@<HOST> "wb-cli --json mqtt write 'zigbee2mqtt/bridge/request/permit_join' '{\"value\": true, \"time\": 240}'"
```

Hold the pair button on the device. After pairing **must disable**:
```bash
ssh root@<HOST> "wb-cli --json mqtt write 'zigbee2mqtt/bridge/request/permit_join' '{\"value\": false}'"
# verify:
ssh root@<HOST> wb-cli --json mqtt read 'zigbee2mqtt/bridge/info' | jq -r '.data.payload | fromjson | .permit_join'   # should be false
```

## Pitfalls and guardrails

- **`systemctl is-active zigbee2mqtt` ≠ bridge probe** — Docker installs always show `inactive`. Use the retained `bridge/state` topic.
- **`wb-cli mqtt list 'zigbee2mqtt/#'`** — pulls megabytes of retained history. Don't.
- **`head -c 200` on `bridge/devices`** — broken JSON, doesn't parse. Parse with `jq -r '.payload | fromjson'`.
- **Absence of `last_seen` ≠ offline** — check `bridge/info → config.availability.enabled`.
- **Never run `bridge/request/permit_join` without user confirmation** — pairing lets ANY nearby Zigbee device join. Always disable and re-check `bridge/info → permit_join == false` afterwards.
- **`battery: 100%` isn't reliable** — LQI < 80 + voltage < 2900 mV = battery about to die (CR2032 reports 100% until the very end, then drops sharply).
- **WBE2R-R-ZIGBEE and similar modules aren't on the web UI "Devices" page** — normal, they're on the Z2M side.

## When to ask the user

- Pairing a device on a production controller — confirm timing (240s window) and which device.
- Removing a device — confirm; some need manual factory-reset afterwards.
- Initiating OTA on a live device — confirm; lights/switches may flicker or restart.
- Bridge JSON shows an unrecognized device — surface it (rogue join during a previous `permit_join`).
- Switching converter (wb-zigbee2mqtt 1.x → wb-mqtt-zigbee) — confirm; topic structure changes and existing wb-rules break.

## Documentation

- <https://wiki.wirenboard.com/wiki/Zigbee>
- <https://wiki.wirenboard.com/wiki/Zigbee2MQTT>
- <https://wiki.wirenboard.com/wiki/WBE2R-R-ZIGBEE_v.2_ZigBee_Extension_Module>
