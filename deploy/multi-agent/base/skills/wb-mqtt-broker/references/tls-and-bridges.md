# MQTT TLS and bridges

## TLS on port 8883

### Certificates

Self-signed CA + server cert for home tasks; for production prefer Let's Encrypt (certbot/acme.sh) with a public domain.

```bash
# self-signed CA + server cert (one-time)
ssh root@<HOST> 'mkdir -p /etc/mosquitto/certs && cd /etc/mosquitto/certs && \
  openssl genrsa -out ca.key 2048 && \
  openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=WB-MQTT-CA" && \
  openssl genrsa -out server.key 2048 && \
  openssl req -new -key server.key -out server.csr -subj "/CN=wirenboard-<SN>.local" && \
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 3650 -sha256 && \
  chown mosquitto:mosquitto *.key *.crt && chmod 0640 *.key'
```

### TLS listener

```bash
ssh root@<HOST> 'cat >> /etc/mosquitto/conf.d/10listeners.conf' <<'EOF'

listener 8883
allow_anonymous false
acl_file /etc/mosquitto/acl/default.conf
password_file /etc/mosquitto/passwd/default.conf
cafile /etc/mosquitto/certs/ca.crt
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
EOF
ssh root@<HOST> 'systemctl restart mosquitto'
```

### TLS test

```bash
ssh root@<HOST> "mosquitto_sub -h localhost -p 8883 --cafile /etc/mosquitto/certs/ca.crt -u <u> -P <p> -t test -C 1 -W 5"
```

From an external host — distribute `ca.crt` to the client, connect to `wirenboard-<SN>.local:8883`. Self-signed without `--cafile` → `certificate verify failed`. For Let's Encrypt — `cafile` not needed (system CA), `certfile`/`keyfile` point to the certbot paths.

## Bridges to other brokers

A bridge is a mode where mosquitto connects to another broker and copies selected topics back and forth. Typical: replication to Home Assistant, copy to cloud, backup broker.

### Example: bridge to Home Assistant

`/etc/mosquitto/conf.d/20bridges.conf`:

```bash
ssh root@<HOST> 'cat > /etc/mosquitto/conf.d/20bridges.conf' <<'EOF'
connection ha-bridge
address ha.local:1883
topic /devices/# out 0 wb/AABBCCDD/
topic ha/wb/cmd/+ in 0
remote_username <ha_mqtt_user>
remote_password <ha_mqtt_password>
keepalive_interval 60
restart_timeout 10
notifications true
notifications_topic wb/AABBCCDD/bridge/state
cleansession false
try_private false
EOF
ssh root@<HOST> 'systemctl restart mosquitto'
```

Topic parameter: `<pattern> <direction> <qos> <local-prefix> <remote-prefix>`.

- `out` — publish there (outbound), `in` — pull here, `both` — both directions.
- `wb/AABBCCDD/` — prefix on the remote side (`wb/AABBCCDD/devices/...` visible there).
- `notifications` creates `wb/AABBCCDD/bridge/state` (`online`/`offline`) — convenient for monitoring.
- `cleansession false` — on disconnect, QoS≥1 messages accumulate and deliver after recovery.

### Bridge with TLS

Add to the connection block:

```
bridge_cafile /etc/mosquitto/certs/ha-ca.crt
bridge_certfile /etc/mosquitto/certs/wb-client.crt
bridge_keyfile /etc/mosquitto/certs/wb-client.key
bridge_insecure false
```

`bridge_insecure true` disables hostname verification — debugging only.
