# Deep serial diagnostics

SKILL.md has the 7-step hot-path sequence. This reference covers tools, the debug-duration table, useful registers, experiments, and the saw → do table.

## Debug duration heuristic

Divide 18000 by errors-per-hour (from the journal step). Minimum 30, maximum 300. If <10 errors/h — set 120 sec.

| Errors/hour | Duration |
|---|---|
| <10 | 120 sec |
| 10-99 | 300 sec (cap) |
| 100 | 180 sec |
| 500 | 36 sec → floor 30 sec |
| 1000+ | 18 sec → floor 30 sec |

## Running `wb-cli serial-debug`

Safe: enables `wb-mqtt-serial`'s `Debug` control, captures the journal for a window, then restores it even if the capture fails. No driver restart, no config edit, no manual `trap`.

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial-debug --port /dev/ttyRS485-1 --seconds <DURATION>')
```

Returns the collected journal entries as JSON (`.data.entries`). For long captures (>30 s) wrap in a background job so the ssh call doesn't time out:

```
ssh_run(host='root@<HOST>', command='wb-cli --json job run serial-debug "wb-cli --json serial-debug --port /dev/ttyRS485-1 --seconds <DURATION> > /mnt/data/ai/wb-ai-skills/diag/debug-serial.json"')
ssh_run(host='root@<HOST>', command='wb-cli --json job wait serial-debug')
ssh_read_file(host='root@<HOST>', path='/mnt/data/ai/wb-ai-skills/diag/debug-serial.json')
```

**Verify debug control is off afterwards** (in case the job was killed mid-flight):

```
ssh_run(host='root@<HOST>', command="wb-cli --json mqtt read '/devices/wb-mqtt-serial/controls/Debug'")
```

Should return `"0"`. If `"1"` — clear with `wb-cli mqtt write /devices/wb-mqtt-serial/controls/Debug/on 0`.

### Fallback (older firmware without `Debug` control)

Edit `/etc/wb-mqtt-serial.conf` via confed to set `debug:true`, restart the driver, sleep, collect the journal, restore. **Keep the `trap` — without it a hung restart leaves the controller in debug mode, filling the disk.**

## Bus scan and ports

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial ports')                                  # active ports the driver serves
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --port /dev/ttyRS485-1')         # Fast Modbus (WB+Onokom)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --slow --port /dev/ttyRS485-1 --timeout 300')   # exhaustive UART poll (third-party)
ssh_run(host='root@<HOST>', command='wb-cli --json serial wb-scan --bootloader --port /dev/ttyRS485-1')            # devices stuck in bootloader
```

`serial ports` returns only **active** ports — the same list `wb-scan` iterates. Missing port = `wb-mqtt-serial` rejected its stanza (schema validation); repair with `wb-cli confed load /etc/wb-mqtt-serial.conf` + `confed save`. Full filesystem list — `ls /dev/ttyRS485-* /dev/ttyMOD* /dev/ttyUSB*`.

## WB device health — uptime + power

```
# Uptime (regs 104-105) — all WB devices with WB-MS-protocol firmware:
ssh_run(host='root@<HOST>', command='modbus_client_rpc -m rtu -a <slave> -t 3 -r 104 -c 2 -b <baud> -s <stop> -p <parity> <path>')
# Vsupply / Vmin (regs 121-122, mV) — relays/dimmers/MCM:
ssh_run(host='root@<HOST>', command='modbus_client_rpc -m rtu -a <slave> -t 3 -r 121 -c 2 -b <baud> -s <stop> -p <parity> <path>')
```

**Registers 121-122 are not universal** — on WB-MAI6/WB-MAP6S and some MR3 they may return other values. If implausible — see the device wiki page.

## Save the report

```
ssh_put(host='root@<HOST>', remote_path='/mnt/data/ai/wb-ai-skills/diag/serial-diag.txt', content='<report text>')
```

## Patterns: saw → do

| Saw | Do |
|---|---|
| `invalid crc` in logs | Debug → look at raw packet. Bad CRC = noise/contact. Foreign slave_id = duplicate |
| `request timed out` | `device/Probe` → alive? If silent — physical, power, slave_id |
| `invalid data size` | Scan → look for slave_id duplicates. Debug → extra bytes = collision |
| `rate limit exceeded` | Spread devices across ports, increase baud, disable extra channels |
| Device in scan but not in config | May interfere! Add or physically disconnect |
| Device in config but not in scan | Off, broken, or third-party (scan doesn't see) |
| CRC on all devices | Noise, 120 Ω terminator, grounding. Experiment: lower speed |
| CRC on one device | Connect with a short wire. If it works — line problem |
| Other stop bits help | Port/device parameter mismatch |
| Min voltage < 20V (reg 122) | Power dips → PSU, wire gauge |
| Small uptime (regs 104-105) | Device rebooted → power |
| Exception code in debug | 1=illegal FC, 2=illegal addr, 3=illegal value, 4=device failure |
| Non-Modbus protocol in config | modbus_client_rpc and scan won't help, only logs and debug |

## Tools

**modbus_client_rpc** (preferred) — through the driver queue, safe:

```
ssh_run(host='root@<HOST>', command='modbus_client_rpc -m rtu -a <slave> -t <FC> -r <reg> -c <count> -b <baud> -s <stop> -p <parity> <port>')
```

FC: 1=coils, 2=discrete, 3=holding, 4=input, 5=write coil, 6=write reg, 15=write coils, 16=write regs.

**device/Probe** — quick "alive?" check. MQTT RPC base pattern: driver `wb-mqtt-serial`, service `device`, method `Probe`, params `{"path":"/dev/ttyRS485-1","baud_rate":9600,"data_bits":8,"parity":"N","stop_bits":2,"slave_id":<ID>,"total_timeout":10000}`, timeout 10.

**wb-modbus-scanner** — Fast Modbus utility (WB, Onokom). `apt install wb-modbus-ext-scanner`. Conflicts with the driver — stop wb-mqtt-serial first (agree with the user!).

```
ssh_run(host='root@<HOST>', command='wb-modbus-scanner -d <port> -b <baud>')        # scan
ssh_run(host='root@<HOST>', command='wb-modbus-scanner -d <port> -s <sn> -i <id>')  # change slave_id
```

**modbus_client** — direct access. Conflicts with the driver — stop wb-mqtt-serial first (agree with the user!).

## Useful WB device registers

All WB devices expose a standard set of Modbus holding registers (<https://wiki.wirenboard.com/wiki/Common_Modbus_Registers>). Device-specific registers are in the device's own wiki page. Check both.

| Register | What | Format |
|---|---|---|
| 104-105 | Uptime | u32, seconds (universal) |
| 110 | Baud rate | u16, abbreviated: 96=9600, 1152=115200 |
| 121 | Supply voltage | u16, mV — **only relays/dimmers/MCM** |
| 122 | Min voltage (since boot) | u16, mV — same place as 121 |
| 128 | Slave ID | u16 |
| 200-205 | Model | string |
| 270-271 | Serial number | u32 |

Broadcast write (slave_id 0) — change baud/address for all WB devices at once. `1152` = `115200` (abbreviated, NOT an error).

## Reading device parameters during diagnostics

Suspect misconfigured firmware settings? Read directly from hardware:

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params <slave_id>')
```

Returns current `parameters` (input modes, relay behaviours, thresholds). Compare with expected values. Apply a fix in-place without editing the config:

```
ssh_run(host='root@<HOST>', command='wb-cli --json serial fw-params <slave_id> <param>=<value>')
```

## Experiments (backup + agree with the user)

```
ssh_run(host='root@<HOST>', command='cp /etc/wb-mqtt-serial.conf /etc/wb-mqtt-serial.conf.bak-$(date +%s)')
```

- **Stop bits**: try `modbus_client_rpc -s 1` / `-s 2`
- **Speed**: broadcast `modbus_client_rpc -a 0 -t 6 -r 110 ... 96` → change port via confed. Errors gone = cable/termination
- **Isolation**: `wb-cli confed load` → flip suspect device `"enabled": false` → `confed save`. Errors gone on the rest = this device interferes
- **Timeouts**: `response_timeout_ms`, `guard_interval_us` in port config

**Roll everything back after experiments.**
