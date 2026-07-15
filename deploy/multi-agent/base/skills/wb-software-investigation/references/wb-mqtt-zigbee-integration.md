# wb-mqtt-zigbee: интеграция zigbee в Wiren Board

## Источники

- Код: https://github.com/wirenboard/wb-mqtt-zigbee (Python, v1.4.6); арх — arc42.md в репозитории
- Старый bridge: https://github.com/wirenboard/wb-zigbee2mqtt (JS, read-only)

## Два моста

### wb-zigbee2mqtt (старый, JS) — ⚠️ только чтение

- Read-only: транслирует данные ИЗ zigbee2mqtt в `/devices/.../controls/...`
- **Не может** принимать команды из `/on` и отправлять в zigbee2mqtt
- Подписывается на `zigbee2mqtt/<device_name>` → парсит JSON → маппит на controls; имя control из `controlsTypes` или `.toString()` (текстовый default)
- Для команд пользователь **обязан** использовать `publish("zigbee2mqtt/.../set", ...)`
- Нет защиты от race condition

### wb-mqtt-zigbee (новый, Python v1.4.6) — ✅ двусторонний

```
           wb-mqtt-zigbee (Python)
zigbee2mqtt ───────────► /devices/.../controls/...
zigbee2mqtt ◄────────── /devices/.../controls/.../on
```

- Читает и пишет; команды через `/on` → `zigbee2mqtt/<device>/set`
- **PendingCommand + command_debounce_sec = 5.0** — защита от race condition
- Динамическое построение контролов из `exposes` (без хардкода маппинга)
- Цветные лампы (RGB из hue/saturation), группы, OTA; Conflicts/Replaces: wb-zigbee2mqtt

## Типовой race condition (мигание света)

### Условия
1. Три правила: команда → publish → обратная связь
2. Таймер автоотключения (setTimeout)
3. Задержка zigbee-mesh > 500ms
4. Новое движение накладывается на обработку zigbee-задержки

### Схема
```ascii
wb-rules:                  zigbee2mqtt:              device:
  │── publish(.../set,OFF) ──►│── set to OFF ──────────►│
  │                          │                         │ (0.5-1.2s mesh delay)
  │◄─── new motion ◄─────────┤ (user walks in)         │
  │── publish(.../set,ON) ──►│── set to ON ───────────►│
  │◄── state=OFF (delayed!) ◄├──── state from OFF ─────┤
  │ Rule2 → dev[lamp]=false   │                         │
  │── publish(.../set,OFF) ──►│                         │
  │◄── state=ON (delayed!) ◄─├──── state from ON ──────┤
  │ Rule2 → dev[lamp]=true    │   ...and so on          │
```

### Детали
- **Запоздалое подтверждение:** OFF от предыдущей команды приходит ПОСЛЕ повторного включения
- **Rule 2 безусловно пишет** в dev["hallway_lamp"] при любом state
- **Rule 1 триггерится** изменением hallway_lamp → publish в zigbee2mqtt
- **zigbee-mesh** может переупорядочить доставку (реконнекты, плохой сигнал)

## Фиксы для старого bridge (wb-zigbee2mqtt, JS)

### Вариант A: дебаунс 2с (рекомендуется)
```js
var last_lamp_change = 0;
defineRule({
  whenChanged: "Light_hallway/hallway_lamp",
  then: function (newValue) {
    last_lamp_change = Date.now();
    publish("zigbee2mqtt/hallway_lamp/set", JSON.stringify({ state: newValue ? "ON" : "OFF" }), 1, false);
  }
});
defineRule({
  whenChanged: "hallway_lamp/state",
  then: function (newValue) {
    if (Date.now() - last_lamp_change < 2000) return;
    dev["Light_hallway/hallway_lamp"] = (newValue === "ON");
  }
});
```

### Вариант B: только синхронизация включения
```js
defineRule({
  whenChanged: "hallway_lamp/state",
  then: function (newValue) {
    if (!dev["Light_hallway/hallway_lamp"] && newValue === "ON") {
      dev["Light_hallway/hallway_lamp"] = true;
    }
    // Не выключаем по обратной связи — только автоматика
  }
});
```

### Дополнительно: `isTruthy()`
```js
function isTruthy(v) {
  return v === true || v === "true" || v === "ON" || v === 1 || v === "1";
}
```

## Переход на wb-mqtt-zigbee (Python)

1. `apt remove wb-zigbee2mqtt`
2. `apt install wb-mqtt-zigbee`
3. В wb-rules убрать `publish("zigbee2mqtt/.../set", ...)` — писать в `dev["device/control"]`
4. Bridge сам транслирует команды и защищает (PendingCommand + 5s debounce)

```js
// Было (старый bridge):
publish("zigbee2mqtt/hallway_lamp/set", JSON.stringify({ state: "ON" }), 2, false);
// Стало (новый bridge) — Rule 1 не нужен вообще:
dev["hallway_lamp/state"] = "ON";
```

## Воспроизведение race condition

Два скрипта на тестовом контроллере:

**test-usr.js** — копия правил пользователя с таймаутом 10с (вместо 60 — для быстрого воспроизведения)

**test-emu.js** — эмулятор wb-mqtt-zigbee:
- Виртуальные `hallway_motion_sensor/occupancy` (text) и `hallway_lamp/state` (text)
- Стрессор: каждые 8-25с дёргает occupancy
- Эмулятор задержки: слушает `Light_hallway/hallway_lamp`, через 200-1200мс обновляет `hallway_lamp/state`

Наблюдение: за 30-60 секунд должен возникнуть цикл ON/OFF.

## QoS

Используй QoS 1 для команд в zigbee2mqtt. QoS 2 даёт дубликаты при нестабильной связи → усиливает race condition в старом bridge. Новый bridge (Python) корректно обрабатывает дубликаты.
