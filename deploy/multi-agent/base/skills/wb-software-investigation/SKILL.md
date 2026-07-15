---
name: wb-software-investigation
description: >
  Универсальная процедура исследования поведения любого Wiren Board софта: узнать версии
  в stable и testing, проверить код на GitHub, сравнить изменения между каналами.
  Применимо к wb-mqtt-serial, wb-rules, wb-mqtt-homeui, wb-mqtt-dali и т.д.
author: multi-agent
---

# Wiren Board Software Investigation

## Когда использовать

Любой вопрос по поведению Wiren Board софта: «правда ли что X работает так?», «почему Y ведёт себя не так как в документации?».

Всегда начинай с этого алгоритма — не лезь в код, не узнав сначала версии в stable и testing.

## Порядок действий

### 0. Сначала документация, потом эксперимент

Прежде чем писать тестовые скрипты — прочитай README и wiki целевого пакета: там описаны ограничения, экономящие часы экспериментов.
- GitHub README (raw) → `https://raw.githubusercontent.com/wirenboard/<пакет>/master/README.md`
- Wiki → `https://wiki.wirenboard.com/wiki/<Статья>`

Только если ответа там нет — переходи к коду и экспериментам.

### 1. Определить target-пакет

Название deb-пакета: `wb-mqtt-serial`, `wb-mqtt-homeui`, `wb-rules`, `wb-mqtt-dali`, `wb-mqtt-confed`, `wb-hwconf-manager` и т.д.

### 2. Версия в stable

- Открыть [wb-releases](https://github.com/wirenboard/wb-releases) — найти последний стабильный релиз (`wb-YYMM`)
- Прочитать его changelog — там версии всех пакетов. URL: `https://wirenboard.com/statics/release-changelogs/wb-YYMM/changelog.html`
- Или поискать: `site:wirenboard.com/statics/release-changelogs/ wb-YYMM changelog`

### 3. Версия в testing

- Testing — rolling release, версия ≈ HEAD master на GitHub (плюс-минус несколько дней)
- Посмотреть [GitHub Releases](https://github.com/wirenboard/пакет/releases) target-пакета; если релиз сегодня/вчера — он уже в testing
- Проверить канал @wirenboard_testing в Telegram (через Telegram tool, если доступен)

### 4. Сравнить stable и master

Прочитать `debian/changelog` из master:
```
https://raw.githubusercontent.com/wirenboard/пакет/master/debian/changelog
```

Найти изменения между версией из stable и HEAD master; определить, затрагивают ли они исследуемую функциональность.

### 4.5. (Опционально) apt history на живом контроллере

Чтобы понять, какие пакеты изменились в последнем обновлении конкретного контроллера (а не что в релизе):

```bash
zcat /var/log/apt/history.log.*.gz | grep -E "^(Install|Upgrade|Remove):" | grep -iE "mosquitto|wb-|libwb"
```

Показывает установки/обновления/удаления, включая зависимости — видно цепочку катализа: не только основной пакет, но и обновившиеся с ним библиотеки.

**Пример (mosquitto heap bug):**
```
zcat /var/log/apt/history.log.*.gz | grep -iE "mosquitto|libwbmqtt|paho"
→ Install: mosquitto:armhf (2.0.11-1+deb11u1, 2.0.20-1-wb102)
→ Install: libwbmqtt1-5:armhf (5.3.2, 5.5.1)
→ Install: python3-paho-socket:armhf (0.0.3-2, 0.0.3-3)
```

Также показывает `.dpkg-new` — конфиги, обновлённые но не применённые из-за локальных изменений:
```bash
find /etc /usr/share/wb-configs -name "*.dpkg-new" 2>/dev/null
```

### 5. Читать код

Всегда ссылаться на **конкретный тег** (например `v2.257.2`), а не на `master` — чтобы ответ не устарел:

```
https://github.com/wirenboard/пакет/blob/тег/src/файл.cpp
https://github.com/wirenboard/пакет/blob/тег/include/файл.h
```

Ключевые файлы у каждого пакета свои — смотреть по ситуации.

### 6. Формат ответа

- Версия в stable (релиз, версия пакета)
- Версия в testing/master (тег/коммит)
- Изменилось ли поведение между ними (да/нет, со ссылкой на changelog)
- Код с конкретными строками и ссылкой на тег
- Feature requests / тикеты если есть

### BACnet — тестирование интеграции без устройств

Как проверить BACnet-to-MQTT шлюз без реальных BACnet-устройств — см. `references/bacnet-testing-without-devices.md`.

**BACnet/IP:** поднять симулятор (YABE, BACpypes, BACnet Stack) на той же сети — шлюз видит его как реальное устройство.
**BACnet MS/TP:** сложнее — нужен USB-RS485 или второй контроллер.

## Где брать ссылки

**wb-releases:** https://github.com/wirenboard/wb-releases
**Changelogs:** https://wirenboard.com/statics/release-changelogs/wb-YYMM/changelog.html
**GitHub repos:** https://github.com/wirenboard/пакет
**changelog raw:** https://raw.githubusercontent.com/wirenboard/пакет/master/debian/changelog
**Support:** https://support.wirenboard.com/
**Wiki:** https://wiki.wirenboard.com/

### Типовые ошибки

- Смотреть master без проверки stable — код мог измениться, а вопрос про работающую систему
- Не проверять changelog на изменения между stable и testing — могло и не поменяться
- Ссылаться на master без тега — через неделю ссылка устареет
- Писать тестовые скрипты, не прочитав README — неверная эмуляция из-за непонимания архитектуры
- **Эмулировать слишком глубокий слой стека** — если есть мост (wb-mqtt-zigbee), эмулируй его выход, а не всё подряд. При тесте zigbee-интеграции не эмулируй zigbee2mqtt целиком — создай виртуальные устройства как wb-mqtt-zigbee и эмулируй только задержку обратной связи.

### Nginx proxy на WB

При добавлении кастомного proxy location (Node-RED `/nr`, Zigbee2mqtt `/z2m` и т.д.) **обязательно** используй `location ^~ /nr` (модификатор `^~`), иначе regex-локация `~* \\.(js|css)$` из дефолтного конфига перехватит статику и отдаст 404.

**Симптом:** браузер грузит HTML, но консоль показывает «Загрузка <script> не удалась» для JS/CSS. curl через порт 80 даёт 404, напрямую к сервису (1880) — 200. nginx error.log: `open() "/var/www/... " failed`.

См. `references/wb-nginx-proxy.md` — диагностический чеклист, Node-RED как пример (проверено на WB7.3.1), типовые сервисы.

### wb-rules: подписки на MQTT

**Критическое ограничение (by design):** `whenChanged` НЕ триггерится на controls виртуальных устройств, созданных через `defineVirtualDevice` в том же процессе wb-rules — как встроенных (`hwmon`, `buzzer`), так и тестовых. Предотвращает бесконечные циклы (правило пишет в output — это не должно ре-триггерить input). Подтверждено: `hwmon/Board Temperature`, `buzzer/volume`, `test_temp/temp` — ни один НЕ срабатывает; `wb-gpio/A1_OUT` (внешний драйвер) — срабатывает.

`whenChanged` подписывается на `/devices/<device>/controls/<control>` через драйвер `wbgong`, то есть работает только для устройств по конвенции Wiren Board.

**Следствие для wb-scenarios:** сценарии на `whenChanged` работают ТОЛЬКО с устройствами внешних драйверов — `wb-gpio`, `wb-adc`, `wb-mqtt-serial`, `wb-mqtt-knx` (где `/devices/<device>/meta/driver ≠ wb-rules`). Для мониторинга wb-rules VD (buzzer, hwmon) `whenChanged` бесполезен.

**Диагностика доступных мишеней:**
```bash
timeout 2 mosquitto_sub -t '/devices/+/meta/driver' -v | grep -v wb-rules
```

**Тест `whenChange`:**
1. Найди switch-выход внешнего драйвера (`wb-gpio/A1_OUT`)
2. Сценарий: `whenChange: wb-gpio/<выход> → setValue: <test_value>`
3. Переключи: `mosquitto_pub -t '/devices/wb-gpio/controls/<выход>' -m 1 -r`
4. Проверь target: `timeout 1 mosquitto_sub -t '/devices/<device>/controls/<control>' -v`

**Debug-техника (когда log.info не виден в journalctl):**
```javascript
defineVirtualDevice('rule_debug', {
  cells: { last_triggered: { type: 'text', value: 'not yet' } }
});
defineRule('test', {
  whenChanged: '<external_device/control>',
  then: function(newValue) {
    dev['rule_debug/last_triggered'] = 'val=' + newValue + ' type=' + typeof newValue;
  }
});
```

**Тест прямого action (без whenChanged) — для wb-rules VD:**
```javascript
setTimeout(function() { dev['buzzer/volume'] = 42; }, 10000);
```

Для подписки на **произвольные MQTT-топики** (`zigbee2mqtt/device/state`, `custom_sensor/occupancy`) нужен `trackMqtt()`:

```js
trackMqtt("zigbee2mqtt/device/state", function(msg) {
  // msg.value — новое значение, msg.topic — топик, msg.retained, msg.qos
});
```

**Когда важно:** при диагностике интеграции zigbee2mqtt + wb-rules. Если у пользователя `whenChanged: "zigbee2mqtt/device/state"`, оно **не сработает** без MQTT-моста (wb-mqtt-mapped) — частая причина «правило не работает».

См. `references/wb-rules-mqtt-subscriptions.md`, `references/wb-scenarios-runtime-bugs.md` (runtime-баги: actionValue→value, typeof MQTT-строк, log label).

### wb-mqtt-zigbee: интеграция zigbee

`wb-mqtt-zigbee` (v2, Python) — мост между zigbee2mqtt и WB MQTT Conventions:
- Подписывается на топики `zigbee2mqtt/`, создаёт виртуальные WB-устройства `/devices/<device>/controls/<control>`
- Команды из wb-rules (`dev[...] = ...`) идут в `/on`-топики → мост транслирует в `zigbee2mqtt/device/set`
- Можно писать напрямую в `zigbee2mqtt/device/set` через `publish()` — bypass моста (работает, но даёт рассинхронизации)

**Race condition (мигание света):** типовой сценарий из трёх правил (включение по движению → publish в zigbee → обратная связь) даёт петлю:

```
1. Таймер выключает свет             → publish("zigbee2mqtt/lamp/set", {state: "OFF"})
2. Движение появляется снова         → RULE3 включает, publish(".../set", {state: "ON"})
3. Приходит state=OFF (с шага 1)     → Rule 2: dev["lamp"] = false
4. publish(".../set", {state: "OFF"})
5. Приходит state=ON (с шага 2)      → Rule 2: dev["lamp"] = true
6. publish(".../set", {state: "ON"})
   → Цикл ON/OFF 2-3 секунды (мигание)
```

**Корень:** Rule 2 безусловно перезаписывает виртуальное устройство, а zigbee-задержка (200-1200ms) накладывается на новое событие движения.

**Воспроизведение:** создай виртуальные устройства как wb-mqtt-zigbee — `hallway_motion_sensor/occupancy` (text "true"/"false"), `hallway_lamp/state` (text "ON"/"OFF"); эмулятор задержки слушает `Light_hallway/hallway_lamp` и через random 200-1200ms обновляет `hallway_lamp/state`; timeout автоотключения 10с для быстрого воспроизведения.

**Фикс:** Rule 2 не должен выключать свет по обратной связи при активном движении:
```js
defineRule({
  whenChanged: "hallway_lamp/state",
  then: function (newValue) {
    // Синхронизируем только false→true (включили вручную/из другого места)
    if (!dev["Light_hallway/hallway_lamp"] && newValue === "ON") {
      dev["Light_hallway/hallway_lamp"] = true;
    }
    // Не выключаем по обратной связи — только автоматика
  }
});
```
Или дебаунс: игнорировать подтверждения в течение 2с после своей команды.

См. `references/wb-mqtt-zigbee-integration.md`.

---

## Диагностика MQTT-петель в wb-rules

### Когда применять

Пользователь жалуется на мигание/дребезг света через zigbee2mqtt + wb-rules, или любое зацикленное поведение между двумя правилами, где одно пишет в MQTT/dev, а другое подписано на обратную связь от того же устройства.

### Признаки MQTT-петли

- Свет моргает ритмично (циклы 1-5с)
- Помогает restart wb-rules (чистит очереди таймеров и MQTT-колбеков)
- Успокаивается при отключении одного из двух правил
- Невоспроизводимо вручную (одиночные команды без задержек)

### Шаг 1: Собрать правила пользователя

Все `defineRule` и `defineVirtualDevice`. Выделить MQTT-схему:
```
Правило A (команда): whenChanged: X → publish(...) или dev[...] = ...
    ↓
Правило B (обратная связь): whenChanged: Y → dev[...] = ...
    ↑
```
- Есть ли два правила, образующих кольцо?
- Есть ли таймер (`setTimeout`), меняющий `dev[...]`?

### Шаг 2: Определить топологию MQTT

| Компонент | Роль |
|-----------|------|
| Правило 1 (команда) | Публикует команду (dev[] или publish) |
| Правило 2 (обратная связь) | Подписано на state устройства |
| Правило 3 (автоматика) | Таймер, датчик — меняет состояние |
| Bridge (wb-zigbee2mqtt / wb-mqtt-zigbee) | Транслирует state из zigbee2mqtt в /devices/.../state |
| zigbee2mqtt | Получает set, возвращает state |

**Важнейший вопрос:** команда идёт через bridge или напрямую?
- `publish("zigbee2mqtt/.../set", ...)` — **напрямую** (bypass моста)
- `dev["device/control"] = ...` — **через bridge** (если мост подписан на /on-топики)

Старый `wb-zigbee2mqtt` (JS, read-only) — только прямая публикация. Новый `wb-mqtt-zigbee` (Python) — оба варианта, но защита (PendingCommand) работает только при записи через dev[].

### Шаг 3: Определить тип задержки

Чем дольше задержка, тем выше вероятность race condition:
- **zigbee-mesh** (200-1200ms) — основная причина
- **QoS 2 дубликаты** — нестабильная MQTT-связь
- **Retained при реконнекте** — долгая перезагрузка

### Шаг 4: Найти race condition

```
1. Таймер (setTimeout): dev["lamp"] = false
2. Rule 1: publish(".../set", {state: "OFF"})
3. [0-1.2s — zigbee-mesh]
4. Датчик: occupancy = true (новое движение)
5. Rule 3: dev["lamp"] = true
6. Rule 1: publish(".../set", {state: "ON"})
7. [0-1.2s паузы]
8. ← state=OFF от шага 2 (запоздалое!)
9. Rule 2: dev["lamp"] = false ← выключил включённый свет!
10. Rule 1: publish(".../set", {state: "OFF"})
11. ← state=ON от шага 6
12. Rule 2: dev["lamp"] = true
13. Rule 1: publish(".../set", {state: "ON"})
    → Цикл ON/OFF
```

Критерии присутствия race condition: есть таймер автоотключения; команда идёт напрямую в zigbee2mqtt (bypass bridge); Rule 2 безусловно пишет в dev[] при любом state; нет дебаунса/фильтрации.

### Шаг 5: Воспроизвести (если нужно)

Два скрипта на тестовом контроллере:
**src.js** — копия правил пользователя (таймаут → 10с)
**emu.js** — эмулятор bridge + датчика + zigbee-задержки:
- Виртуальные устройства: датчик (occupancy text), реле (state text)
- Стрессор: случайные изменения occupancy (8-25с покоя, 3-6с движения)
- Эмулятор задержки: при изменении command → через random 200-1200ms обновить state

**Важно:** эмулировать только выход bridge (виртуальные устройства + задержку обработки команды), не всю шину zigbee2mqtt — это лишний слой.

### Шаг 6: Выбрать фикс

| Сценарий | Фикс | Сложность |
|----------|------|-----------|
| Старый bridge + прямой publish | Дебаунс 2с в Rule 2 | ★☆☆ |
| Старый bridge + прямой publish | Rule 2 только синхронизирует false→true | ★☆☆ |
| Новый bridge + dev[] | Bridge сам защищает (PendingCommand 5s), Rule 1 не нужен | ★★☆ |
| Переход wb-zigbee2mqtt → wb-mqtt-zigbee | Полное обновление bridge + рефакторинг | ★★★ |

**Фикс A: Дебаунс** (рекомендуется для старых систем)
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

**Фикс B: Только синхронизация включения**
```js
defineRule({
  whenChanged: "hallway_lamp/state",
  then: function (newValue) {
    // Синхронизируем только внешнее включение (с пульта)
    if (!dev["Light_hallway/hallway_lamp"] && newValue === "ON") {
      dev["Light_hallway/hallway_lamp"] = true;
    }
    // Выключение — только от автоматики (таймер, occupancy)
  }
});
```

**Фикс C: isTruthy + lamp_state_actual** — отдельный флаг `lamp_state_actual`, сравнение с запросом. Работает, но не защищает от race condition таймера + нового движения.

**Критерии выбора:** дебаунс в Rule 2 — минимальное изменение; нужна ручная синхронизация → Фикс B проще; готовы обновлять bridge → Фикс C с переходом на wb-mqtt-zigbee.

### Шаг 7: Валидировать ответ инженера ТП (если задача — проверка)

- Определил ли инженер, что это петля обратной связи?
- Совпадает ли механизм (race condition таймера + задержка zigbee)?
- Упомянул ли bridge (какой, как работает)?
- Предложил ли защиту (дебаунс / фильтрация / isTruthy)?
- Есть ли миграция на wb-mqtt-zigbee как вариант?
- **Проверить фикс на уязвимость:** не сломается ли предложенный код при race condition таймера и нового движения?

### Mosquitto: accounting bug (#3192)

При подозрении на ложный OOM (счётчик HEAP растёт, RSS нет) — рецепт с ускоренным воспроизведением в `references/mosquitto-heap-accounting-bug-3192.md` и скрипт `scripts/mqtt-heap-stress.py` (5000 MQTTv5 PUBLISH с properties за 17с, HEAP +22 MB).

#### Диагностика: проверка config source

Когда спрашивают «почему memory_limit начал применяться» — не смотри на `30limits.conf` (он не менялся), проверь **кто его активирует**:

```bash
# 1. Кто владелец конфига
dpkg -S /etc/mosquitto/mosquitto.conf                # пакет mosquitto
dpkg -S /usr/share/wb-configs/mosquitto/30limits.conf # пакет wb-configs

# 2. Что изменилось vs stock Debian
diff /etc/mosquitto/mosquitto.conf /etc/mosquitto/mosquitto.conf.dpkg-dist
# → + include_dir /usr/share/wb-configs/mosquitto  ← активирует 30limits.conf
# → + include_dir /usr/share/wb-configs/mosquitto-post
# → - persistence true / log_dest file → syslog

# 3. Кто внёс изменения в conffile
dpkg --verify mosquitto
# → ??5?????? c /etc/mosquitto/mosquitto.conf  ← conffile модифицирован

# 4. Какой скрипт модифицирует
cat /usr/lib/wb-configs/fix_mosquitto.sh             # из пакета wb-configs
# → добавляет include_dir /usr/share/wb-configs/mosquitto в mosquitto.conf
# → commit 75ef8debc85f (2025-05-23) — когда появилась директива
```

**Ключевой урок:** `30limits.conf` не менялся с января 2024, но `fix_mosquitto.sh` начал добавлять на него `include_dir` в мае 2025. Разница между «файл существует» и «файл читается» — истинная причина регрессии, которую легко пропустить.

#### Валидация через сабагента

Когда выводы неуверенны или пользователь просит привлечь сабагента при сомнениях:
- Делегируй валидационного сабагента через `delegate_task`, передав анализ, гипотезы и открытые вопросы
- Toolsets `['execute_code', 'ssh_run', 'search_files', 'read_file', 'web_search', 'web_extract']` — нужно проверять код, запускать локальные команды (execute_code) и команды на контроллере (ssh_run), сверять источники
- Передай контекст: дата, суть бага, что доказано (со ссылками), что спекулятивно, что противоречит
- Явно попроси играть адвоката дьявола: «попробуй опровергнуть вывод ниже. Какие данные его фальсифицировали бы?»
- Не для каждой мелочи — только при неуверенности или кросс-валидации критичных утверждений перед сдачей

### Подготовка к сдаче: verification chain

Перед передачей выводов команде разработки — пройти 7-шаговую цепочку:
1. Подтвердить баг в коде форка
2. Подтвердить upstream-фикс
3. Проверить, что патч ложится чисто
4. Верифицировать собранный пакет
5. Воспроизвести баг на стенде (до фикса)
6. Установить фикс и проверить, что HEAP перестал расти
7. Оформить два архива

Полный рецепт с командами: `references/bug-fix-verification-chain.md`.

### Оформление результата: два архива

После расследования бага — упаковать в **два отдельных архива**:

**Архив 1: Статья + ассеты** (для публикации/обсуждения)
- `article-<bugname>.md` — статья с grounding каждого факта `([source: ...])`
- `doc/root-cause.md`, `patches/`, `findings.md` (замеры, A/B, цифры)
- `changelog_evidence.txt`, `reproduction_results.txt`, `links.txt`, `README.md`

**Архив 2: Багрепорт** (для разработчика)
- `BUGREPORT.md` — текст для issue, `INSTRUCTIONS.txt` — воспроизведение
- `scripts/`, `packages/` (собранные deb с фиксом), `patches/`
- `evidence/` (замеры, логи, changelog), `links.txt`

**Правила упаковки:**
- Раскрывать относительные пути в абсолютные (не `~/path`)
- Grounding на file:// URI только в статье; в багрепорте — URL GitHub/web
- Мета-анализ (debugging notes, анализ дублей) — отдельным файлом, не в статью
- Evidence — реальные цифры с живого оборудования, не синтетика
- Ложные гипотезы задокументировать как «ложные следы», чтобы разработчик не пошёл той же дорогой

**Protocol version ID как первый шаг** — перед скриптом определи, есть ли на контроллере MQTTv5-клиенты (без них #3192 не триггерится):
```bash
journalctl -u mosquitto --since "48 hours ago" -o cat | grep -oP "as \S+ \(p\d" | sort | uniq -c | sort -rn
```
- `p5` — MQTTv5 (потенциальный триггер); `p2` — MQTT v3.1.1 (не триггерит)
- `auto-*` с UUID = виртуальные устройства WB (локальные, wb-rules); всегда проверяй, что стоит за IP/unix-socket
- **unix socket = локальный процесс** (`/var/run/mosquitto/mosquitto.sock` → клиент на том же хосте: wb-rules, wb-mqtt-serial)

Если p5 нет — ищи другой механизм утечки, не #3192.

**Timeline-связывание: когда апгрейд внёс баг.** При вопросе «почему OOM после последнего обновления» свяжи три факта:
1. Когда был апгрейд (apt history rotated logs)
2. Версия mosquitto до/после (Debian stock vs WB-fork)
3. Есть ли upstream-тикет между этими версиями (changelog / GitHub issues)

Пример: 2.0.11 (чист) → 2.0.20 (с #3192) → баг проявляется только при наличии p5-клиентов. В 2.0.11 баг **был (тот же `free(*property)`), но невидим** — без `memory_limit` accounting-счётчик не управлял аллокациями. Подробнее в `references/mosquitto-heap-accounting-bug-3192.md` → раздел «Why now? Катализатор».

**Сборка patched deb для armhf-контроллера:** если билд-машина arm64 — scp source tarball на контроллер и `dpkg-buildpackage -b -uc -us` нативно. Всё в `references/wb-deb-native-build.md`.

### CI-пайплайн wb-scenarios (Jenkins)

При диагностике сбоев CI — `references/wb-scenarios-ci-pipeline.md`. Каскадные ошибки: если `Check version has bumped` упал, Lintian и Setup deploy показывают «Failed to build stage» **транзитивно**, а не из-за реальных проблем lintian.
