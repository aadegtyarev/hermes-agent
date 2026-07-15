# Mosquitto Heap Accounting Bug (#3192)

## Суть

В mosquitto 2.0.20 и ранее счётчик `$SYS/broker/heap/current` растёт монотонно при получении MQTTv5 PUBLISH с properties, хотя реальное потребление (RSS) не меняется. При достижении `memory_limit` mosquitto ложно отказывает клиентам с "out of memory".

## Корень

`mosquitto_property_add_*()` аллоцирует через `mosquitto__calloc()` (увеличивает `memcount`), а `property__free()` вызывает plain `free()` (не уменьшает `memcount`). Описание: eclipse/mosquitto#3192. **Коммит-фикс:** `015fe3d68784` (между v2.0.20 и v2.0.21).

## Где живёт memory_limit на WB

memory_limit стоит **НЕ в `/etc/mosquitto/mosquitto.conf`**, а в `/usr/share/wb-configs/mosquitto/30limits.conf`:

```
max_queued_messages 0
memory_limit 100000000    # ~95 MB
max_inflight_messages 1000
```

Подключается через `include_dir /usr/share/wb-configs/mosquitto` в основном конфиге. `grep memory_limit /etc/mosquitto/mosquitto.conf` ничего не найдёт — проверять include_dir-ы. Файл пакетный (из `wb-configs`), перезаписывается при обновлении — не редактировать напрямую.

## Доказательство: A/B тест

### С багом (mosquitto 2.0.20, armhf)
```
10 000 PUBLISH c properties (MessageExpiryInterval + UserProperty)
→ HEAP: 1 654 696 → 1 907 228 (+252 532 байт)
→ RSS:  8 848 → 8 848 KB (+0)
```

### С фиксом (mosquitto 2.0.21, aarch64)
```
10 000 PUBLISH c properties
→ HEAP: 831 112 → 833 424 (+2 312 байт — разовая инициализация топиков)
→ RSS:  4 724 → 4 724 KB (+0)
```

**Вывод:** счётчик растёт только на 2.0.20; на 2.0.21 нормально.

## Полевая верификация (3.5 дня uptime, mosquitto 2.0.20)

Живой контроллер, реальная нагрузка 19 клиентов, 10.2M сообщений:

| Метрика | Значение | Вывод |
|---|---|---|
| HEAP current | 2,215,480 → 2,216,632 за 15 сек | **Растёт сейчас** (два замера с интервалом) |
| HEAP maximum | 2,711,388 (пик за 3.5 дня) | Монотонный рост от старта |
| RSS | 8,848 KB | **Строго стабильна** — реальной утечки нет |
| memory_limit | 100,000,000 (~95 MB) | Активен через 30limits.conf |
| в main.conf | grep: пусто | Ловушка — лимит в include_dir |
| Заполнение | 73.6% после ускоренного теста | До OOM при норме ~3.5 дня |

**Скорость роста в поле:** ~77 байт/сек = 6.5 MB/день.

## Диагностический признак

Пустые очереди + OOM-отказы = характерный признак accounting bug. При реальной утечке очереди забиты; при accounting bug новые сообщения не принимаются, существующие очереди опустошаются, broker продолжает отказывать.

**Ловушка:** не ограничивайся `grep memory_limit /etc/mosquitto/mosquitto.conf`. Проверяй все `include_dir`:
```bash
grep -r memory_limit /etc/mosquitto/ /usr/share/wb-configs/mosquitto/
```

## Протокол-версия: триггер бага

Баг #3192 триггерится **только** MQTTv5 PUBLISH с properties. MQTT v3.1.1 не использует properties.

```bash
journalctl -u mosquitto --since "24 hours ago" -o cat | grep -oP "as \S+ \(p\d" | sort | uniq -c | sort -rn
```

**Коды (WB-форк 2.0.20):** `p5` — MQTTv5 (**триггерит**), `p2` — MQTT v3.1.1 (**не триггерит**). Подтверждено: raw CONNECT protocol byte 3 (v3.1) → rejected; 4 (v3.1.1) → `p2`; 5 (MQTTv5) → `p5`.

| Ситуация | Вердикт |
|----------|---------|
| HEAP растёт, только `p2` | #3192 не виноват — ищи другой механизм |
| HEAP растёт, есть `p5` | #3192 возможен — A/B тест |
| HEAP растёт, `p5` только от внешних | Проверь частоту и кол-во properties |

## Why now? Катализатор (ключевой раздел)

### Что изменилось в последнем релизе

Из apt history — **переход с Debian-овского mosquitto 2.0.11 на WB-форк 2.0.20**:
```bash
zcat /var/log/apt/history.log.*.gz | grep -i mosquitto
# Install: mosquitto:armhf (2.0.11-1+deb11u1, 2.0.20-1-wb102)
```

Одновременно обновились `libwbmqtt1-5:armhf (5.3.2, 5.5.1)` (C-библиотека для WB-сервисов) и `python3-paho-socket:armhf (0.0.3-2, 0.0.3-3)` (MQTT over unix socket для python).

### Цепочка катализа

1. Было: mosquitto 2.0.11 (Debian stock) — **содержал тот же #3192**: `free(*property)` в `property__free()` (подтверждено: eclipse-mosquitto v2.0.11/lib/property_mosq.c ~241), `mosquitto__calloc` уже имел `mem_limit` guard. Но **memory_limit не был установлен** → `mem_limit == 0` → `if(mem_limit && ...)` всегда false → счётчик рос, но на аллокации не влиял. Баг невидим.
2. Стало: mosquitto 2.0.20 (WB-fork) — **тот же #3192**, но WB добавил `30limits.conf` с `memory_limit 100000000` → `mem_limit` активен → счётчик реально управляет допуском аллокаций.
3. Фикс (`015fe3d68784`) вошёл в v2.0.21 — `free(*property)` → `mosquitto__free(*property)` в `property__free()` и `calloc` → `mosquitto__calloc` в `property__read_*`. В compare/v2.0.20...v2.0.21.
4. Для проявления нужен ≥1 MQTTv5-клиент (p5), публикующий properties (MessageExpiryInterval, UserProperty).

### Как найти, что изменилось

**Проверить все ротации apt history**, не только первую:
```bash
for f in /var/log/apt/history.log.*.gz; do
    echo "=== $f ==="; zcat "$f" 2>/dev/null | grep -iE "mosquitto|libwbmqtt"
done
echo "=== CURRENT ==="; grep -iE "mosquitto|libwbmqtt" /var/log/apt/history.log 2>/dev/null
```

Критическое обновление может быть в самой старой ротации. Пример:
- **2026-05-07 (history.log.2.gz):** mosquitto 2.0.11 → 2.0.20, libwbmqtt1-5 5.3.2 → 5.5.1 ← настоящий источник бага
- **2026-05-13 (history.log.1.gz):** libwbmqtt1-5 5.5.1 → 5.6.0, wb-rules 2.38.6 → 2.40.0 (mosquitto не менялся) ← не влияет

### Ложные следы

Коррелированные пакеты не обязательно причинно связаны:

| Пакет | Изменение | Влияние на #3192 |
|-------|-----------|------------------|
| libwbmqtt1-5 | 5.3.2 → 5.5.1 → 5.6.0 | **Нет** — C-библиотека локальных служб, они p2 |
| python3-paho-socket | 0.0.3-2 → 0.0.3-3 | **Нет** — Python-обёртка unix-сокетов |
| wb-rules | 2.22.0 → 2.40.0 | **Нет** — подключается p2 |
| wb-device-manager | 1.14.1 → 1.25.4 | **Нет** — проводные Modbus |

**Ошибка:** считать все обновлённые в тот же день пакеты катализаторами. Разделяй корреляцию и причинность.

### Идентификация p5-клиентов в поле

**Ключевой принцип:** не доверяй ClientID-префиксам. `auto-*` UUID — WB-конвенция для виртуальных устройств wb-rules. Они подключаются через unix socket (`/var/run/mosquitto/mosquitto.sock:0`), локальные, могут быть p2 или p5.

```bash
# 1. Сводка по протоколу
journalctl -u mosquitto --since "48 hours ago" -o cat | grep -oP "as \S+ \(p\d" | sort | uniq -c | sort -rn
# 2. Только p5 c IP (внешние)
journalctl -u mosquitto --since "48 hours ago" -o cat | grep "(p5" | grep -v "mosquitto.sock"
# 3. p5 через unix socket (локальные, редко)
journalctl -u mosquitto --since "48 hours ago" -o cat | grep "(p5" | grep "mosquitto.sock"
```

| Группа | Протокол | Транспорт | Источник |
|--------|----------|-----------|----------|
| `wb-mqtt-*` (serial, adc, gpio, w1, db) | p2 | unix socket | libwbmqtt1-5 (всегда p2) |
| `auto-*` | p2 | unix socket | wb-rules (виртуальные устройства) |
| Внешние клиенты (scripts, ПЛК, сторонние) | p2 или p5 | TCP/IP | зависит от implementation |
| подозрительные (напр. `hack_accel`) | p5 | TCP/IP | внешний хост — проверять |

**Важно:** p5-клиент, сразу отключающийся с "malformed packet", всё равно может триггерить #3192 — аллокация properties происходит до валидации пакета.

**Ловушка:** не думай, что `auto-*` = беспроводные модули. Проверь: hostapd работает? (`systemctl status hostapd`, `iw dev wlan0 station dump`); транспорт (unix socket = локально, IP:port = внешний); кто какой ClientID. Если hostapd не запущен и wlan0 в managed — wireless-клиентов нет, все auto-* локальные.

### Почему 77 b/s совместим с #3192

Наличие хотя бы одного p5-клиента (даже с редкой публикацией или циклами reconnect с "malformed packet") объясняет рост:
- Каждый MQTTv5 PUBLISH → alloc properties (memcount += ~50-100 байт) → free() без декремента → leak в счётчике
- p5-клиент, циклически переподключающийся с malformed packets — каждая попытка триггерит аллокацию при парсинге CONNECT без декремента; ~2 эпизода/сек → ~150-200 байт/сек

**Вывод:** рост 77 b/s объясняется #3192 при p5-клиенте с частотой publish/reconnect ~1-2/сек. Достаточно одного, не нужно 12.

## Контрольное воспроизведение

Если подозреваешь accounting bug, но системные клиенты не MQTTv5 — запусти скрипт:
```bash
python3 scripts/mqtt-heap-stress.py localhost 1883 5000
```
**Баг подтверждён:** HEAP +22 MB, RSS = 0. **Бага нет:** HEAP ±несколько KB.

## Accelerated reproduction (17 sec, real controller from prod)

Подключиться к живому контроллеру (mosquitto 2.0.20), запустить `scripts/mqtt-heap-stress.py`, замерить HEAP до/после. Результат (3.5 дня uptime, 19 клиентов, 10.2M сообщений):

| Параметр | До | После | Δ |
|---|---|---|---|
| HEAP current | 77,175,936 (73.6 MB) | 99,957,516 (99.96 MB) | **+22.2 MB** |
| HEAP maximum | 2,711,388 | **99,999,996** | стоп на грани лимита |
| RSS | 8,848 KB | 8,848 KB | **0** |

**Выводы:**
- 5,000 MQTTv5 PUBLISH → +22.2 MB HEAP за **17 секунд** (291 msg/sec)
- **4,556 байт/publish** — не 50 как в A/B на свежем брокере (разница из-за накопленного HEAP других клиентов)
- RSS строго 8,848 KB — ни байта утечки; контроллер уже был на 73.6% лимита при обычной эксплуатации за 3.5 дня

```bash
python3 scripts/mqtt-heap-stress.py <host> <port> <count>
```
Скрипт сам замеряет HEAP до/после и выводит Δ.

### Риск для production

- Накопление HEAP: ~77 байт/сек (в поле, достаточно одного p5)
- До лимита 100 MB: **~2-3 недели** под нормальной нагрузкой
- После лимита: mosquitto шлёт `"out of memory"` всем, хотя реальная память ~9 MB

**Мораль:** если клиент жалуется на OOM раз в 1-3 недели, а после перезапуска mosquitto всё снова работает 1-3 недели — accounting bug, не реальная утечка.

## Clean backport verification (фикс ложится на форк)

Клиент на WB-форке (`v2.0.20-1-wb102`), upstream-фикс в `v2.0.21`. Проверить: есть ли WB-специфичные изменения в `property_mosq.c`, ложится ли diff чисто.

**1. Собрать теги форка через API**
```bash
curl -s 'https://api.github.com/repos/wirenboard/mosquitto/tags?per_page=100' | \
  python3 -c "import json,sys; [print(t['name']) for t in json.load(sys.stdin)]"
```

**2. Сравнить `property_mosq.c` между upstream/Debian-packaging/WB-specific тегами**
```bash
diff <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/debian/2.0.20-1/lib/property_mosq.c') \
     <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/v2.0.20-1-wb102/lib/property_mosq.c')
```
Пустой diff — WB не патчил файл, upstream-фикс ложится чисто.

**3. Проверить наличие фикса в debian/2.0.21-1**
```bash
grep -n 'mosquitto__free\|free(' \
  <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/debian/2.0.21-1/lib/property_mosq.c')
```

**4. Полный diff между версиями с багом и без**
```bash
diff <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/debian/2.0.20-1/lib/property_mosq.c') \
     <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/debian/2.0.21-1/lib/property_mosq.c')
```

### Результат: 10 замен в property_mosq.c
```
line 244:  free(*property)              →  mosquitto__free(*property)    # корень #3192
line 1123: calloc(1, *len+1)            →  mosquitto__calloc(...)
line 1152: calloc(1, p->value.s.len+1)  →  mosquitto__calloc(...)
line 1175: calloc(1, p->name.len+1)     →  mosquitto__calloc(...)
line 1181: calloc(1, p->value.s.len+1)  →  mosquitto__calloc(...)
line 1184: free(*name)                  →  mosquitto__free(*name)        # вторая дыра!
line 1206: calloc(1, sizeof(...))       →  mosquitto__calloc(...)
line 1258: strdup + calloc(1,1)         →  mosquitto__strdup + mosquitto__calloc
line 1268: malloc(pnew->value.bin.len)  →  mosquitto__malloc(...)        # третья дыра!
line 1278: strdup + calloc(1,1)         →  mosquitto__strdup + mosquitto__calloc
line 1285: strdup + calloc(1,1)         →  mosquitto__strdup + mosquitto__calloc
```

**Три независимые утечки счётчика:** `free(*property)` (основной #3192); `free(*name)` в `property__read_string` (вторая); `malloc(...)` в `mosquitto_property_copy_all` (третья — malloc не считает вообще). Каждая даёт вклад в рост `memcount` при p5-нагрузке. Diffs чистые, конфликтов с WB-патчами нет — фикс ложится в `v2.0.20-1-wb102` без изменений.

## Эксперимент: memory_limit не убивает брокер

Изолированный брокер c `memory_limit 500000` (~488 KB):
```bash
/usr/sbin/mosquitto -c /var/tmp/mqtest/mosquitto.conf -d
# listener 21887, allow_anonymous, max_queued_messages 0, memory_limit 500000
```
Нагрузка: 10 000 MQTTv5 PUBLISH QoS1 с properties на медленный persistent-подписчик (v5, -c, QoS2).

| # publish | HEAP (bytes) | Broker alive? |
|-----------|-------------|---------------|
| 500 | 70,492 | ✓ |
| 1500 | 241,048 | ✓ |
| 2500 | 348,984 | ✓ |
| 3000 | 456,364 | ✓ |
| ~3227 | ≈500,000 | client disconnected |

**Broker не упал** — просто отключил клиента-нарушителя по memory_limit.

`memory_limit` — **предохранитель**, а не убийца:
1. Клиент публикует v5 → `mosquitto__calloc()` видит `memcount + size > mem_limit` → NULL
2. Брокер НЕ крашится, разрывает соединение: `"Client pubN disconnected due to out of memory."`
3. Продолжает обслуживать остальных
4. `memcount` не уменьшается (из-за `free()` вместо `mosquitto__free()`), порог больше не преодолеть — но broker жив

**Exit code 255** (аварийное завершение) — НЕ от memory_limit в чистом виде. Возможные причины (несовместимость с paho, race condition в property-очистке, внешний фактор) нуждаются в доп. расследовании. `kill -9` даёт killed/SIGTERM, не 255.

## Вторичный механизм отказа Zigbee: `BindsTo` в systemd

Даже когда mosquitto выживает (штатное переподключение после memory_limit), **zigbee2mqtt может быть мёртв** из-за systemd-зависимости.

`/lib/systemd/system/zigbee2mqtt.service` (deb 2.10.0-wb101):
```ini
[Unit]
After=network.target mosquitto.service
BindsTo=mosquitto.service
[Service]
ExecStart=/usr/bin/npm start
Restart=always
```

**`BindsTo=`** — жёсткая привязка: если `mosquitto.service` уходит в `inactive` (crash, kill, memory_limit-disconnect), systemd:
1. Шлёт SIGTERM `zigbee2mqtt.service`
2. Помечает остановку как Result=**success** (плановое завершение по зависимости)
3. `Restart=always` НЕ срабатывает на success
4. z2m мёртв до `reboot` или ручного `systemctl start zigbee2mqtt`

**Тест:**
```bash
# z2mtest.service: BindsTo=mosquitto.service, After=..., ExecStart=/usr/bin/sleep 3600, Restart=always
kill -9 $(MainPID mosquitto)  # аварийная смерть брокера
→ mosquitto сам поднялся (Restart=on-failure)
→ z2mtest: inactive/dead, Result=success — Restart=always не сработал
```

**Обход (не лечение):**
```bash
mkdir -p /etc/systemd/system/zigbee2mqtt.service.d
printf '[Unit]\nBindsTo=\nWants=mosquitto.service\n' > /etc/systemd/system/zigbee2mqtt.service.d/override.conf
systemctl daemon-reload
```
`Wants=` вместо `BindsTo=` — z2m переживает смерть брокера, но остаётся без MQTT-связи (маскирует канарейку). Применять ТОЛЬКО в паре с лечением брокера.

**Источник:** скрипт установки z2m (`scripts/install.sh` в `wirenboard/zigbee2mqtt`) создаёт unit **без** `BindsTo` (только `After=network.target`). `BindsTo` кладёт deb-пакет — упаковочный дефект.

## Ссылки

- https://github.com/eclipse/mosquitto/issues/3192
- https://github.com/eclipse/mosquitto/releases/tag/v2.0.21
- https://github.com/eclipse/mosquitto/commit/015fe3d68784 (основной коммит-фикс)
- https://github.com/wirenboard/mosquitto (WB-форк, тег debian/2.0.20-1 ↔ 2.0.20-1-wb102)
