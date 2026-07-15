# wb-scenarios Runtime Bugs

## Баг 1: field name mismatch `value` → `actionValue`

**Файл:** `scenarios/devices-control/devices-control.mod.js`, строка 266

**Контекст:** при добавлении threshold events (whenGreaterThan/whenLessThan) в `inputChangeHandler` было изменено:
```javascript
// правильно:
var curActionValue = self.cfg.outControls[j].actionValue;
// неправильно (новый код):
var curActionValue = self.cfg.outControls[j].value;
```

**Проблема:** JSON Schema хранит поле как `actionValue` (во всех схемах outControls). Остальные модули (astronomical-timer, schedule) читают `.actionValue`. Новый код читал `.value` → для любого старого сценария `undefined`.

**Результат:** `curActionValue = undefined` → `handler(actualValue, undefined)` → для `setValue` это `Number(undefined) = NaN` → сценарий запускается, но output-контрол не меняется.

**Фикс:** вернуть `.actionValue`.

**Как проверить самому:**
1. diff — какие поля читает новый код
2. JSON Schema (`schema/wb-scenarios.schema.json`) — как называются поля outControls
3. Сравнить с другими модулями (astronomical-timer, schedule)
4. Live-конфиг: `cat /etc/wb-scenarios.conf` — какие поля в outControls

**Урок:** при смене любого поля в `outControls` → сначала проверить JSON Schema + grep по всем модулям на имя поля.

## Баг 2: `typeof newValue !== 'number'` в whenGreaterThan/whenLessThan

**Файл:** `src/table-handling-events.mod.js`, строки 44-64

**Проблема:** MQTT значения всегда строки. `typeof '46.875' !== 'number'` = `true` → возвращается `false`.

**Симптом:** whenGreaterThan/whenLessThan никогда не срабатывают.

**Фикс:** `Number(newValue)` вместо typeof check.

## Баг 3: log label — косметический

**Файл:** `wbsc-scenario-base.mod.js`, строки 18, 151

**Проблема:** `log` — одна модульная переменная, `log.setLabel(...)` перезаписывается при инициализации каждого сценария.

**Симптом:** все сценарии в логах показывают label последнего инициализированного.

**Фикс:** новый Logger на экземпляр, а не модульный.

## Когда обращаться к этому файлу

- Диагностика "wb-scenarios создан, активен, но не срабатывает"
- NaN на output-контролах
- whenGreaterThan/whenLessThan не срабатывают
- Странные log-label в journalctl
- **Перед любыми изменениями `outControls`** — проверка имён полей против JSON Schema
