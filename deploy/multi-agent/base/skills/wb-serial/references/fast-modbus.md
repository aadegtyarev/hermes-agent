# Fast Modbus (WB extension protocol)

WB devices support a Wirenboard extension to Modbus RTU that enables:

- **Bus scan by serial number** — find all WB/Onokom devices even with duplicate slave_ids
- **Targeted commands by SN** — send any Modbus command to a device by its SN instead of slave_id (critical when two devices share an address)
- **Events** — devices proactively push register changes (inputs, counters, alarms) without polling; useful for low-latency diagnostics

All frames use broadcast address `0xFD` and command byte `0x46`.

## Key frame types

| Subcommand | Direction | Purpose |
|---|---|---|
| `0x01` | → | Scan start — all devices reset scan status |
| `0x02` | → | Scan next — request next unscanned device |
| `0x03` | ← | Scan response — device replies with SN + slave_id |
| `0x04` | ← | Scan end — no more unscanned devices |
| `0x08` | → | Send standard Modbus PDU addressed by SN |
| `0x09` | ← | Response to `0x08` |
| `0x10` | → | Poll events |
| `0x11` | ← | Event packet from device |
| `0x12` | ← | No events |

## Change slave_id by serial number

When two devices share a slave_id (factory default collision), target by SN:

```
→ FD 46 08 [SN 4 bytes BE] 06 00 80 00 [new_id u16 BE] [CRC16 LE]
← FD 46 09 [SN 4 bytes BE] 06 00 80 00 [new_id u16 BE] [CRC16 LE]
```

- `0x08` = standard PDU by SN; `06` inside = FC6 write single holding register; `0x0080` = reg 128 (slave_id)
- SN comes from the extended scan result (`sn` field in wb-cli scan output)

## Change baud rate by SN

```
→ FD 46 08 [SN 4 bytes BE] 06 00 6E [baud_abbrev u16 BE] [CRC16 LE]
```

- Register `0x006E` = 110 (baud); value abbreviated: 96=9600, 1152=115200
- Send at the **device's current baud rate**; device switches immediately after ACK

## Sending Fast Modbus via port/Load RPC (no driver stop needed)

`wb-mqtt-serial` exposes `port/Load` RPC with `"protocol": "raw"` — sends arbitrary bytes through the driver's own serial queue and returns the response. **No need to stop wb-mqtt-serial.**

Use the MQTT RPC base pattern: driver `wb-mqtt-serial`, service `port`, method `Load`, timeout = `total_timeout` seconds + 2.

```json
{
  "path": "/dev/ttyRS485-1",
  "baud_rate": 9600,
  "parity": "N",
  "data_bits": 8,
  "stop_bits": 2,
  "protocol": "raw",
  "format": "HEX",
  "msg": "FD460800020B860600800001<CRC-LE>",
  "response_size": 14,
  "response_timeout": 100,
  "frame_timeout": 20,
  "total_timeout": 5000
}
```

- `msg`: hex string, no spaces. CRC is part of the message — build it manually.
- `response_size`: expected response length in bytes
- `response_timeout`: ms to wait for first byte
- `frame_timeout`: ms inter-byte gap that ends the frame
- Response: `{"response": "<hex bytes>"}` in `HEX` format

**Modbus CRC-16** (LE): polynomial `0xA001`, init `0xFFFF`, append low byte then high byte.

This is how `wb-cli serial add-devices` and `modbus_client_rpc` work internally — same queue, no conflict with ongoing polling.

## When to use Fast Modbus in diagnostics

- **Duplicate slave_id** on scan — `wb-cli serial add-devices` resolves it automatically via SN. Manual:
  ```bash
  wb-cli serial send --port ... --msg 'FD 46 08 <SN 4B> 06 00 80 00 <new_id>' --add-modbus-crc --response-size 14
  ```
- **Device in scan but won't respond to modbus_client_rpc** — address conflict; use `0x08` by SN to read model/firmware first
- **Event-based debugging** — subscribe to device events for input changes, counter ticks, resets. Catches rare events without log noise.
- **wb-modbus-scanner** (`apt install wb-modbus-ext-scanner`) — reference CLI for Fast Modbus. Not installed by default; conflicts with the driver while running.

Protocol spec: <https://github.com/wirenboard/wb-modbus-ext-scanner/blob/main/docs/protocol.en.md>
