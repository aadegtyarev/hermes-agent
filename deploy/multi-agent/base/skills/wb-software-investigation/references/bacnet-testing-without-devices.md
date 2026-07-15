# BACnet: тестирование шлюза без реальных устройств

## Контекст

Клиенты Wiren Board спрашивают про BACnet (BACnet/IP, BACnet MS/TP) — управление VRV/VRF, BMS, регистры температуры/уставки/вкл-выкл. Из коробки WB не поддерживает BACnet — предлагается сторонний шлюз, например [novatechflow/bacnet-mqtt-gateway](https://github.com/novatechflow/bacnet-mqtt-gateway).

Основная боль: **нет реальных BACnet-устройств** для проверки шлюза.

## Известное из прошлой разведки

- YABE работает через mono на Linux; BAC0 (Python над BACpypes) — работает
- Кандидаты шлюза: `novatechflow/bacnet-mqtt-gateway` и `2pk03/bacnet-mqtt-gateway` (древний, ожил в апреле 2026)
- Идея обёртки: bacnet-mqtt-gateway + `wb-mqtt-bacnet` (по аналогии с zigbee)

## Развилка: шлюз vs встроенный BACnet engine

**Шлюз (MQTT → BACnet):**
- Отдельный сервис/контейнер с bacnet-mqtt-gateway; wb-mqtt говорит только на MQTT, шлюз перегоняет BACnet ↔ MQTT
- Если шлюз упал — BACnet пропал (SPOF на контроллере)
- **Покрывает:** BACnet/IP, Read/Write регистры. **Не покрывает:** MS/TP

**Встроенный BACnet engine:**
- Код реализует BACnet/IP прямо в контроллере, без прослойки; встроен в wb-mqtt (как zigbee) или отдельный сервис в архитектуре WB
- **Покрывает:** BACnet/IP, потенциально MS/TP. **Объём:** принципиально больше (C-стэк с SourceForge или BAC0 как сервис)
- C-стэк: BACnet Stack (SourceForge, BSD) — embedded-ready, MS/TP поддерживает

**Рекомендация:** начать со шлюза, MS/TP добрать вторым этапом при спросе.

## BACnet/IP — тестируется легко

BACnet/IP работает поверх UDP (порт 47808). Достаточно поднять симулятор на той же сети — шлюз увидит его как реальное устройство через Who-Is / I-Am / ReadProperty / WriteProperty.

### Инструменты

| Инструмент | Тип | BACnet/IP | MS/TP | Примечание |
|---|---|---|---|---|
| **YABE** | Java GUI | ✅ | ❌ | Золотой стандарт: скан, эмуляция, чтение/запись. Бесплатный |
| **BACnet Stack** (SourceForge) | C library | ✅ | ✅ | Компилится под Linux. Примеры: эмулятор устройства, MS/TP↔IP роутер |
| **BACpypes** | Python | ✅ | ❌ | Библиотека для своих симуляторов |
| **BAC0** | Python | ✅ | ❌ | Надстройка над BACpypes |
| **Node-RED** (`node-red-contrib-bacnet`) | Node.js | ✅ | ❌ | Эмулятор нодами |
| **CAS BACnet Stack** | C#/.NET | ✅ | ❌ | Коммерческий (есть триал) |

### Настройка симуляции

```
┌─────────────────┐     BACnet/IP (UDP:47808)     ┌──────────────┐
│  BACnet-to-MQTT  │◄────────────────────────────►│  Симулятор   │
│  шлюз (Docker)   │                              │  (YABE/py)   │
└────────┬─────────┘                              └──────────────┘
         │ MQTT
         ▼
┌─────────────────┐
│  WB MQTT broker  │
│  (mosquitto)     │
└─────────────────┘
```

1. Поднять шлюз (`novatechflow/bacnet-mqtt-gateway` в Docker)
2. Запустить симулятор (YABE или BACpypes)
3. Публиковать в MQTT топики WB-формата → шлюз конвертирует в BACnet WriteProperty → симулятор отвечает
4. Симулятор генерирует BACnet-данные → шлюз → MQTT → видно в WB

### Пример: BACpypes симулятор (минимальный)

```python
from bacpypes3.app import Application
from bacpypes3.local.device import LocalDeviceObject
from bacpypes3.primitivedata import Real
from bacpypes3.basetypes import EngineeringUnits

device = LocalDeviceObject(
    objectIdentifier=('device', 123),
    objectName='Test Simulator',
    vendorIdentifier=15,
)
app = Application(device, '192.168.1.100')
# Добавить AI-объект с температурой
app.run()
```

### YABE

- Скачать: https://sourceforge.net/projects/yetanotherbacnetexplorer/
- На Linux — через mono (не JRE)
- Эмуляция: Tools → Create Virtual Device; чтение/запись свойств, лог трафика. Только BACnet/IP

## BACnet MS/TP (RS-485) — сложнее

Без реального железа полноценно не эмулировать.

| Вариант | Реалистичность | Сложность |
|---|---|---|
| Два WB контроллера по RS-485 | ★★★★★ | Средняя (нужен второй контроллер) |
| USB-RS485 + BACnet Stack | ★★★★☆ | Средняя (нужен USB-RS485 адаптер) |
| Virtual Serial Port + socat | ★★☆☆☆ | Низкая (ненадёжно, нет реальных таймингов) |
| BACnet/IP ↔ MS/TP роутер | ★★★☆☆ | Высокая (роутер меняет поведение) |

### USB-RS485 подход

1. USB-RS485 адаптер (~$10)
2. BACnet MS/TP master на Linux через BACnet Stack:
   ```
   ./bin/mstp-server --device 123 /dev/ttyUSB0 38400
   ```
3. Шлюз видит его как MS/TP-устройство

## Типовые проблемы

### Шлюз не видит устройство
- UDP порт 47808 открыт? `tcpdump -i eth0 port 47808`
- Broadcast работает? BACnet/IP использует broadcast для Who-Is
- Шлюз и симулятор в одной L2-сети или настроен BBMD
- Docker? Нужен `--network host` для UDP broadcast

### Не приходят данные в MQTT
- COV (Change of Value) подписка? Не все симуляторы поддерживают
- ReadProperty polling? Шлюз может не уметь polling
- Формат MQTT-топиков?

### Задержки и таймауты
BACnet/IP по Ethernet ~10-50ms. Для тестов: `tc qdisc add dev eth0 root netem delay 200ms`

## Ссылки

- novatechflow/bacnet-mqtt-gateway: https://github.com/novatechflow/bacnet-mqtt-gateway
- 2pk03/bacnet-mqtt-gateway (revived 2026): https://github.com/2pk03/bacnet-mqtt-gateway
- BACnet Stack: https://sourceforge.net/projects/bacnet/
- BACpypes3: https://bacpypes3.readthedocs.io/
- YABE: https://sourceforge.net/projects/yetanotherbacnetexplorer/
- BAC0: https://bac0.readthedocs.io/
- Node-RED BACnet: https://flows.nodered.org/node/node-red-contrib-bacnet
- CAS BACnet Stack: https://www.casbacnet.com/
