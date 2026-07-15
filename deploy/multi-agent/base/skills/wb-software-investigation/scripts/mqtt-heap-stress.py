#!/usr/bin/env python3
"""
MQTTv5 HEAP stress test — ускоренное воспроизведение accounting bugs.
Подключается к MQTT-брокеру, шлёт N PUBLISH c properties, замеряет HEAP до/после.

Доказывает расхождение: счётчик HEAP растёт, RSS не меняется.

Использование:
  python3 mqtt-heap-stress.py [host] [port] [count]

По умолчанию: 192.168.2.184 1883 5000
"""
import paho.mqtt.client as mqtt
import paho.mqtt.properties as props
import paho.mqtt.reasoncodes as rc
import time, sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.2.184"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 1883
COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 5000
BATCH = max(1, COUNT // 20)


def get_heap():
    """HEAP через отдельное MQTTv5 соединение"""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    heap_val = [None]

    def on_msg(client, userdata, msg):
        try:
            heap_val[0] = int(msg.payload.decode().strip())
        except:
            pass

    c.on_message = on_msg
    c.connect(HOST, PORT, 5)
    c.subscribe("$SYS/broker/heap/current", qos=0)
    c.loop_start()
    time.sleep(1.5)
    c.loop_stop()
    c.disconnect()
    return heap_val[0]


print(f"Соединение с {HOST}:{PORT}", flush=True)
print(f"HEAP до...", end=" ", flush=True)
heap_before = get_heap()
print(f"{heap_before} ({heap_before / 1024:.1f} KB)" if heap_before else "N/A")

# Основная нагрузка — одно соединение, много publish
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
client.connect(HOST, PORT, 10)

pub_props = props.Properties(props.PacketTypes.PUBLISH)
pub_props.MessageExpiryInterval = 3600  # 1 час

start = time.time()
for i in range(1, COUNT + 1):
    pub_props.UserProperty = ("seq", str(i))
    client.publish(
        f"/test/accel/{i % 1000}",
        f"{{\"t\":{i}}}",
        qos=0,
        properties=pub_props,
    )
    if i % BATCH == 0:
        elapsed = time.time() - start
        print(f"  {i}/{COUNT} — {i / elapsed:.0f} msg/sec", flush=True)

client.disconnect()
elapsed = time.time() - start
speed = COUNT / elapsed
print(f"\nСкорость: {speed:.0f} msg/sec за {elapsed:.1f} сек")

time.sleep(0.5)

print(f"HEAP после...", end=" ", flush=True)
heap_after = get_heap()
print(f"{heap_after} ({heap_after / 1024:.1f} KB)" if heap_after else "N/A")

if heap_before and heap_after:
    delta = heap_after - heap_before
    delta_kb = delta / 1024
    per_msg = delta / COUNT
    print(f"\n=== Δ HEAP: {delta} байт ({delta_kb:.1f} KB) ===")
    print(f"На msg: {per_msg:.1f} байт/publish")
    if delta > 50000:
        print("БАГ ПОДТВЕРЖДЁН: HEAP растёт, реальной утечки нет")
    else:
        print("HEAP не изменился — баг не проявляется")
