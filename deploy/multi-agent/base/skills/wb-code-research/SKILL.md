---
name: wb-code-research
description: "Методика верификации утверждений о поведении ПО Wiren Board через исходный код на GitHub, с обязательной сверкой stable и testing релизов."
---

# wb-code-research

## Когда использовать

- Нужно проверить, правда ли драйвер/компонент Wiren Board ведёт себя определённым образом
- Есть утверждение, которое можно подтвердить/опровергнуть кодом
- Нужно понять, как работает конкретная функция в компоненте WB
- Пользователь сомневается в ответе — «это точно так? проверь реально»

## Порядок действий

### 0. Прочитай README и документацию — прежде чем смотреть код или экспериментировать

Многие ответы уже есть в README/Wiki. Экономит часы:
- `https://raw.githubusercontent.com/wirenboard/<repo>/master/README.md`
- `https://wiki.wirenboard.com/wiki/<Статья>`

Если ответа нет — переходи к коду.

### 1. Определить версии — всегда первым шагом

Прежде чем смотреть любой код — узнай, какие версии пакетов в:

- **Stable**: последний `wb-YYMM` (сейчас wb-2606). Changelog на `https://wirenboard.com/statics/release-changelogs/wb-XXXX/changelog.html`
- **Testing**: rolling-релиз, последние GitHub-релизы попадают туда сразу
- **Master**: HEAD на GitHub

Список всех релизов — `https://github.com/wirenboard/wb-releases`

Это нужно, чтобы:
- Не ответить про master, когда пользователь на stable
- Указать, изменилось ли поведение между stable и testing/master
- Точно знать, на какой версии сделан вывод

### 2. Найти репозиторий

GitHub: `https://github.com/wirenboard/<имя-пакета>`

Основные: `wb-mqtt-serial`, `wb-mqtt-homeui`, `wb-rules`, `wb-mqtt-smartbus`, `wb-mqtt-dali`, `wb-mqtt-confed`, `wb-device-manager`, `wb-hwconf-manager`, `wb-mqtt-knx`.

### 3. Смотреть код по тегу, а не master — если вопрос про конкретный релиз

URL для просмотра по тегу:
`https://github.com/wirenboard/<repo>/blob/<tag>/<path>`

Raw (для web_extract):
`https://raw.githubusercontent.com/wirenboard/<repo>/<tag>/<path>`

Если вопрос про stable — смотри код на теге, который указан в changelog этого релиза.
Если про testing/master — HEAD master ок.

### 4. Сначала changelog, потом код

`debian/changelog` на master — история изменений. Читай его перед погружением в исходники. Часто ответ уже там: «добавлено», «исправлено», «изменено».

### 5. Ключевые файлы для типовых компонентов

**wb-mqtt-serial:**
- `src/serial_client_events_reader.cpp` — Fast Modbus (SetDevices, EnableEvents, ReadEvents)
- `src/serial_client_events_reader.h` — HasDevicesWithEnabledEvents()
- `src/serial_client.cpp` — OpenPortCycle, Cycle, таймауты
- `src/serial_config.cpp` — схема конфига порта
- `debian/changelog` — история версий
- `debian/control` — зависимости

### 6. Проверка изменений между stable и testing

- Сравни changelog master с changelog стабильного релиза
- Если логика не менялась — ответ одинаков для всех веток (скажи об этом явно)
- Если менялась — укажи, с какой версии поведение изменилось

### 7. Формат ответа

- Компактный, с прямыми ссылками на строки кода (ссылка по тегу, не master)
- Версия драйвера: когда смотрел, какой тег/коммит
- Цитата из кода: функция, строка, условие — достаточно для верификации
- Каждый пункт — отдельно, без воды
- Если ошибся в предыдущем ответе — явно признай и исправь

## Pitfalls

- GitHub code search требует авторизации — не используй `github.com/search?q=...&type=code`. Вместо этого: `raw.githubusercontent.com` для прямого доступа к файлам, или grep через `execute_code` с `web_extract` нескольких raw-файлов
- Версии пакетов WB имеют суффиксы `-wbXXX` (например `2.248.1-wb101`). GitHub-теги могут называться так же или без суффикса — не путай
- He путай «версия на GitHub» (тег) и «версия в apt» (debian-версия) — в changelog они совпадают, но в названии релиза wb-YYMM может быть другая нумерация
- В testing версия может меняться от дня к дню — если ответ критически зависит от testing, укажи дату проверки
- **wb-rules: `whenChanged` не подписывается на произвольные MQTT-топики** — только на `/devices/.../controls/...`. Для zigbee2mqtt/... топиков нужен `trackMqtt()`.
- **wb-rules: `whenChanged` НЕ триггерится на VD-controls того же процесса** — если control принадлежит `defineVirtualDevice` в том же экземпляре wb-rules (driver = wb-rules), change events не генерируются. Проверить driver: `mosquitto_sub -t '/devices/<device>/meta/driver' -v`. Если `wb-rules` — `whenChanged` не поможет, нужен внешний MQTT-источник. Не эмулируй вслепую — сначала прочитай документацию. Проверено экспериментально: `whenChanged: "test_custom/topic"` не срабатывает при publish() в этот топик, а `trackMqtt("test_custom/topic", callback)` — срабатывает.
- **wb-mqtt-zigbee (Python, v1.4.6) — двусторонний bridge с защитой от петли.** Старый wb-zigbee2mqtt (JS) — read-only. При диагностике проблем с zigbee: проверяй, какой bridge установлен. `command_debounce_sec = 5.0` с PendingCommand — bridge игнорирует state от zigbee2mqtt 5 секунд после своей команды. Если пользователь публикует через `publish("zigbee2mqtt/.../set", ...)` напрямую — bridge не видит команду, защита не срабатывает. Фикс: `dev["device/control"] = ...` вместо publish().

## Пример работы

См. `references/fast-modbus-verification.md`
См. `references/wb-mqtt-knx-architecture.md` — архитектура wb-mqtt-knx (legacy vs group objects, DPT, типовые проблемы из реальных кейсов)
См. `references/pr-review-using-glm.md` — ревью WB PR через GLM-5.2, если claude-glm CLI не работает (прямой API-вызов z.ai)
См. `references/wb8-can-h616.md` — CAN на WB8: H616 не имеет CAN-контроллера в SoC, реализация через GPIO + внешний трансивер
