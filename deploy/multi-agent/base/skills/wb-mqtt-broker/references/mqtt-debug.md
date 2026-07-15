# Verbose PUBLISH tracing — `wb-cli mqtt-debug`

Find **which MQTT client** publishes to a topic (wb-rules, web UI, external client, misbehaving driver) via the plugin instead of editing `conf.d/` by hand. **Always quote topics — WB control names often contain spaces** (e.g. `Channel 1 Dimming Level`); over SSH wrap the whole command in double quotes.

> `client_id` is the **literal MQTT identifier** the publisher chose at CONNECT, as reported after `Received PUBLISH from`. Not a systemd unit name, not always the package name — see table.

## Capture examples

```bash
# Single substring filter (grep-style); multiple --topic / --client-id OR together
ssh root@<HOST> "wb-cli --json mqtt-debug capture --seconds 60 \
    --topic '/devices/wb-mr6c_7/controls/K1' --client-id wb-rules"

# MQTT wildcards: + = one level, # = all remaining
ssh root@<HOST> "wb-cli --json mqtt-debug capture --seconds 60 \
    --topic '/devices/+/controls/Channel 1 Dimming Level/on'"

# Toggle persistently (verbose logging stays on after capture)
ssh root@<HOST> wb-cli mqtt-debug enable   # also: status | disable

# Long capture (hours/days) — runs as a wb-cli job, JSON on disk
ssh root@<HOST> "wb-cli --json mqtt-debug capture --seconds 86400 --background \
    --output /mnt/data/ai/wb-cli/mqtt-debug-\$(date +%s).json"
ssh root@<HOST> wb-cli --json job wait <unit>   # poll
ssh root@<HOST> "jq '.data.entries[] | select(.client_id != \"wb-adc\")' \
    /mnt/data/ai/wb-cli/mqtt-debug-<TS>.json"
```

The plugin writes drop-in `/etc/mosquitto/conf.d/debug-verbose.conf` (`log_type all`), restarts `mosquitto`, and parses each `Received PUBLISH …` line into records:

```json
{"timestamp": "2026-05-13T09:44:31+00:00",
 "client_id": "system__wb-rules__cAbCdEfGhIjK",
 "topic": "/devices/wb-mr6c_7/controls/K1/on",
 "qos": 0, "retain": false, "dup": false,
 "message_id": 1234, "payload_size": 1}
```

Inline captures restore the previous on/off state automatically (try/finally); `--background` restores it when the job finishes. Pass `--keep-enabled` to leave verbose logging on.

## Common `client_id` values

| `client_id` mosquitto reports | who that is |
|---|---|
| `wb-modbus` | `wb-mqtt-serial` (legacy client_id, back-compat) |
| `system__wb-rules__<hex>` | a wb-rules engine instance |
| `wb-mqtt-homeui-<hex>` | the web UI |
| `wb-mqtt-knx`, `wb-zigbee2mqtt`, `wb-w1` | corresponding drivers |
| `wb-cli-<pid>` | `wb-cli mqtt write` (since 1.5.2) |
| `mosquitto_pub-<pid>` | `mosquitto_pub` (`-i` set automatically) |
| `auto-<UUID>` | client with an **empty** client_id — mosquitto ≥2.0 auto-generates a UUID; can't be tied to a process |
| `tasmota_*`, `shellyplus_*` | external IoT clients |

For your own ad-hoc publishes showing `auto-<UUID>`, pass `-i some-name` to `mosquitto_pub`.
