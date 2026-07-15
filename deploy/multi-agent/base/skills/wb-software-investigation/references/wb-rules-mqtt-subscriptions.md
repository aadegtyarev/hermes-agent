# wb-rules: MQTT subscription mechanics

## Источник

Эксперименты на контроллере (wb-rules 2.x, go-crazy engine), README, исходный код (rule.go, engine.go, master v2.257.2).

## `whenChanged` — подписка по конвенции WB

**Формат:** `whenChanged: "device/control"` или массив `["device1/ctrl1", "device2/ctrl2"]`

Подписывается через драйвер `wbgong` на `/devices/<device>/controls/<control>`.

**Работает только** для устройств по Wiren Board MQTT Convention:
- `defineVirtualDevice('name', ...)` — виртуальные в wb-rules
- `wb-mqtt-mapped` — ремаппинг внешних топиков
- `wb-mqtt-serial` — RS-485
- `wb-mqtt-zigbee` (Python) — zigbee через мост

**Не работает** для произвольных топиков (`zigbee2mqtt/device/state`, `custom_sensor/occupancy`, любой не в формате `/devices/.../controls/...`).

## `trackMqtt(topic, callback)` — произвольный топик

Подписывается на любой MQTT-топик, допустимы wildcards `#` и `+`.

```js
// ВАЖНО: trackMqtt можно вызывать ТОЛЬКО внутри then функции правила
defineRule({
  whenChanged: "some_virtual_device/control",
  then: function() {
    trackMqtt("zigbee2mqtt/device/state", function(msg) {
      log(msg.value);     // строковое значение
      log(msg.topic);     // полный топик
      log(msg.retained);  // bool
      log(msg.qos);       // 0|1|2
    });
  }
});
```

**Ограничение:** trackMqtt внутри then может создавать множественные подписки при каждом срабатывании. Флаг-защита:
```js
var subscribed = false;
defineRule({
  whenChanged: "init/ready",
  then: function() {
    if (subscribed) return;
    subscribed = true;
    trackMqtt("zigbee2mqtt/device/state", function(msg) { ... });
  }
});
```

## `dev["device/control"] = value` — запись в MQTT

- **Виртуальные устройства:** публикует в `/devices/device/controls/control`
- **Внешние (wb-mqtt-serial):** публикует в `/devices/device/controls/control/on`

Разница важна! Для zigbee через wb-mqtt-zigbee:
```js
dev["hallway_lamp/state"] = "ON";  // → /devices/hallway_lamp/controls/state → wb-mqtt-zigbee шлёт в zigbee2mqtt
```

## `publish(topic, value, qos, retain)` — произвольный топик

Не триггерит `whenChanged` и `dev`-записи.
```js
publish("zigbee2mqtt/hallway_lamp/set", JSON.stringify({state: "ON"}), 2, false);
```
**Это bypass wb-mqtt-zigbee** — команда идёт напрямую в zigbee2mqtt, минуя bridge. Bridge не отслеживает такие команды и не защищает от race condition.

## `defineVirtualDevice(name, config)`

```js
defineVirtualDevice('Light_hallway', {
  title: {'en': 'Light hallway', 'ru': 'Свет прихожей'},
  cells: {
    hallway_lamp: { title: {'en': 'Lamp', 'ru': 'Светильник'}, type: "switch", value: false, readonly: false },
    hallway_auto: { type: "switch", value: true },
    hallway_timeout: { type: "range", value: 60, min: 5, max: 600, step: 5 }
  }
});
```

**Типы ячеек:** `switch` (булево, выключатель), `text` (строка), `value` (число), `range` (число со слайдером min/max/step), `pushbutton` (кнопка — true, затем сразу false).

**Важно:** `whenChanged` для switch срабатывает при любом изменении булева. Но при старте `PrevValue == nil` — событие игнорируется (кроме pushbutton).

## `defineRule(config)`

```js
// Один триггер
defineRule({ whenChanged: "device/control", then: function(newValue) { ... } });

// Массив триггеров
defineRule({
  whenChanged: ["dev1/ctrl1", "dev2/ctrl2"],
  then: function(newValue, triggerName) { /* triggerName — какой сработал */ }
});

// Именованное (имя видно в логах)
defineRule("my_rule_name", { whenChanged: "...", then: function(newValue) { ... } });
```

## `setTimeout / clearTimeout`, `setInterval / clearInterval`

```js
var timer_id = setTimeout(function() { timer_id = null; }, 5000);
if (timer_id) { clearTimeout(timer_id); timer_id = null; }

var interval_id = setInterval(function() { ... }, 1000);
clearInterval(interval_id);
```

**Осторожно:** при hot-reload (редактирование файла без restart wb-rules) ссылки на таймеры в глобальных переменных могут потеряться → таймер работает, но управлять им нельзя. При добавлении/удалении таймеров — restart wb-rules.

## Триггер `type: "function()"` — deprecated

```js
defineRule({ whenChanged: "device/control", type: function() { ... } });  // используй then
```

## Глобальные переменные и hot-reload

При hot-reload (перезапись через scp без restart): код перезагружается, глобальные переменные пересоздаются, уже работающие setTimeout могут не получить доступ к новому состоянию. **Надёжнее:** при изменении правил с таймерами — restart wb-rules.

## Механизм `CellChangedRuleCondition` — фильтрация событий

Из rule.go — событие игнорируется, если:
1. `PrevValue == nil` (первичная инициализация)
2. `IsRetained && PrevValue == Value` (retained с тем же значением)

Это объясняет, почему `whenChanged` не срабатывает при старте — все MQTT-сообщения приходят как retained с PrevValue == nil.
