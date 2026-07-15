# Template creation, device parameters, firmware-version lookup

## Template creation workflow

### 1. Device documentation

`WebFetch` the manufacturer's manual — register table (addresses, types, scale). Without it, don't make a template; guessing = endless debugging.

### 2. List existing templates, pick a starter

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial templates')                  # list all template filenames
ssh_run(host='root@<HOST>', command='wb-cli --json serial template wb-mr6c')           # show full JSON of a template
```

Copy a similar one as a starter:

```
ssh_run(host='root@<HOST>', command='cp /usr/share/wb-mqtt-serial/templates/config-wb-mr6c.json /etc/wb-mqtt-serial.conf.d/templates/acme-em100.json')
```

### 3. Test on one channel

Start with **one** channel. Add via `wb-cli serial add-devices`, verify it publishes a plausible value:

```
ssh_run(host='root@<HOST>', command='wb-cli --json dev <device.id>_<slave_id>')
ssh_run(host='root@<HOST>', command="wb-cli --json mqtt read '/devices/<device.id>_<slave_id>/controls/<channel>'")
```

If wrong — tweak `format`, `scale`, `word_order`. Direct ground-truth read:

```
ssh_run(host='root@<HOST>', command='modbus_client_rpc -m rtu -a <slave> -t 4 -r <addr> -c <count> -b <baud> -s 2 -p N <port>')
```

(`-t 4` = input registers FC4.)

### 4. Expand to all channels

Add in batches of 5-10, verifying via MQTT after each.

### 5. Parameters and groups

Once base telemetry works — add `parameters` for settings, `groups` for UI.

### 6. Template in Git/backup

A custom template won't survive FIT. Goes into backup automatically via `wb-controller-backup` (picks up `/etc/wb-mqtt-serial.conf.d/`).

## `add-devices` — automatic fixups and result

**Automatic fixups** (scan mode only):

| Issue | Action |
|---|---|
| Device baud ≠ port baud | Writes reg 110 at device's current speed → device switches to port's baud |
| Two scan devices same slave_id | Reassigns duplicate via Fast Modbus by SN (WB/Onokom) or reg 128; without SN — warns and skips |
| Scan device slave_id conflicts with existing config (different device_type) | Reassigns via Fast Modbus by SN or reg 128 |

### Result shape (multi-port)

```json
{
  "port": null,
  "ports": ["/dev/ttyRS485-1", "/dev/ttyRS485-2"],
  "added": [
    {"slave_id": 7,  "device_type": "WB-MR6C",     "port": "/dev/ttyRS485-1"},
    {"slave_id": 1,  "device_type": "WB-MAO4-20mA", "port": "/dev/ttyRS485-2",
     "slave_id_changed": "18 → 1", "baud_changed": "115200 → 9600"}
  ],
  "skipped": [
    {"slave_id": 4, "port": "/dev/ttyRS485-1"}
  ],
  "count": 2,
  "warnings": []
}
```

`warnings` appears when: template not found (required params unfilled — validate config manually); address collision without SN (can't reassign safely, device skipped); baud change failed (device unreachable — check connectivity, skipped).

After adding, `wb-mqtt-serial` reloads automatically. Verify: `wb-cli --json dev <device_id>`.

## Reading/writing device parameters (`fw-params`)

`serial fw-params` reads/writes the `parameters` section (firmware settings) of one device. The device must already be in `/etc/wb-mqtt-serial.conf` — the driver uses the template's `parameters` to know which registers to touch.

```
# Read by slave_id or device id
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52')
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params wb-mr6c-52')
# Bypass driver cache (re-read live from hardware)
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52 --force')
```

Returns `{"slave_id": 52, "device_type": "WB-MR6C", "model": "...", "fw": {"version": "..."}, "parameters": {"in0_mode": 0, ...}}`.

```
# Write through config (default — persistent across driver restarts):
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52 in0_mode=1 in1_mode=3')
# Write straight to device, skipping config (--force — one-shot, reverts on next restart):
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params 52 in0_mode=1 --force')
```

`KEY=VALUE` — values coerced: integers first, then floats, then strings.

**Note:** `fw-params` is for *template parameters* only (`device.parameters` in the JSON). For bus-level registers — `slave_id` (reg 128), `baud rate` (reg 110) — use `serial wb-set-slave-id` / `serial wb-set-baud`.

## Listing devices and ports

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial config')                       # all devices from config (with protocol)
ssh_run(host='root@<HOST>', command='wb-cli --json serial config --port /dev/ttyRS485-1')  # filter to one port
ssh_run(host='root@<HOST>', command='wb-cli --json serial ports')                        # active ports (driver-side, only open ones)
```

## Device firmware version

If a specific WB device's firmware version is needed — **don't ask the user**:

1. Read directly from hardware — many WB devices expose `fw_version` in `fw-params` without touching config:
   ```
   ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params <slave_id_or_id>')
   ```
   Check `.data.fw.version` / `.data.parameters.fw_version`. Done if present.
2. Otherwise look up the device's `device_type` from config:
   ```
   ssh_run(host='root@<HOST>', command="wb-cli --json serial config | jq -r '.data.devices[] | select(.slave_id==<slave_id>) | .device_type'")
   ```
3. Read the template:
   ```
   ssh_run(host='root@<HOST>', command="wb-cli --json serial template config-<mqtt-id> | jq '.data.template.device.channels[] | {name, enabled}'")
   ```
4. Find a channel named `FW Version`, `Firmware Version`, `SW Version`, `Serial`, etc.
5. Enable it via confed (`wb-cli confed load` → flip `"enabled": true` → `wb-cli confed save`).
6. After 10-20 s read from MQTT:
   ```
   ssh_run(host='root@<HOST>', command="wb-cli --json mqtt read '/devices/<device_id>/controls/<channel_name>'")
   ```
