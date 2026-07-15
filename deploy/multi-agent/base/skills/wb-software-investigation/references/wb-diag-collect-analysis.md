# Анализ wb-diag-collect (диагностический архив клиента)

## Когда применять

Клиент прислал `diag_output_XXXXXX_*.zip` из `wb-diag-collect`. Нужно подтвердить/опровергнуть гипотезу о поведении системы без доступа к контроллеру.

## Процесс

### 1. Извлечь и просмотреть структуру

```bash
unzip -l diag_output_*.zip | head -40
```

Ключевые файлы для MQTT/mosquitto-анализа:

| Файл | Что даёт |
|------|----------|
| `mosquitto-sys.log` | HEAP, uptime, версия, клиенты, retained |
| `mosquitto-perms.log` | Права на каталог mosquitto |
| `last_logs.log` | journalctl текущей загрузки — клиенты, ошибки, остановки |
| `last_logs.previous-boot.log` | journalctl предыдущей загрузки |
| `dmesg.log` / `dmesg.previous-boot.log` | Ring buffer ядра — OOM-killer, аппаратные ошибки |
| `dpkg_l.log` | Версии всех пакетов |
| `free.log` | Память (total/used/free) |
| `ps_aux.log` | Процессы с %MEM, %CPU, PID |
| `wb-release.log` | Версия прошивки (часто пуст — см. ниже) |

### 2. Первичный скрининг

```bash
cat mosquitto-sys.log | grep -E 'version|uptime|heap|clients' | head -20
cat last_logs.log | grep -iE 'mosquitto|p5|out of memory|terminat|signal|kill'
grep -iE 'mosquitto|zigbee|z2m|wb-mqtt-metrics' dpkg_l.log
cat free.log | head -5
```

### 3. Скорость роста HEAP и прогноз отказа

```bash
# mosquitto-sys.log: heap/current → 52934624 (52.9 MB); uptime → 79343 (≈22 ч)
# Стартовый HEAP свежего брокера ≈ 1.5-2 MB
# Рост = (52.9 - 2.0) MB / 22 ч ≈ 2.3 MB/ч
# До memory_limit 100 MB: (100 - 52.9) / 2.3 ≈ 20 часов до отказа
```

Позволяет сказать: «Ваш брокер упадёт через ~20 часов без перезагрузки».

### 4. Проверить trigger #3192 — MQTTv5 клиенты

```bash
cat last_logs.log | grep -oP 'as \S+ \(p\d' | sort | uniq -c | sort -rn  # по протоколу
cat last_logs.log | grep '(p5,' | grep -v 'mosquitto.sock'              # p5 внешние (триггерят)
cat last_logs.log | grep '(p5,' | grep 'mosquitto.sock'                 # p5 локальные (редко)
```

`p5` = MQTTv5 → может триггерить #3192; `p2` = v3.1.1 → не триггерит; unix socket = локальный процесс; IP:port = внешний.

### 5. Подтвердить цепочку отказа

| Симптом | Где | Что искать |
|---------|-----|------------|
| HEAP растёт | mosquitto-sys.log → `heap/current` | > 2 MB и растёт |
| Есть p5-клиенты | last_logs.log → `(p5,` | Хотя бы один = триггер |
| memory_limit активен | .../30limits.conf | `memory_limit 100000000` |
| Памяти достаточно | free.log | >500 MB free → не OOM-killer |
| dmesg без OOM | dmesg.log + previous-boot | Нет `killed process` / `oom_kill` |
| z2m установлен | dpkg_l.log → `zigbee2mqtt` | `2.10.0-wb101` с BindsTo |
| z2m мёртв | ps_aux.log → `z2m` | Нет процесса |

### 6. Архитектура arm64 vs armhf

`.deb` архитектурно-зависимые. Если клиент на arm64 — armhf-пакет со стенда не поставить.
```bash
grep 'mosquitto' dpkg_l.log | awk '{print $4}'   # arm64 → собирать на arm64; armhf → пойдёт со стенда
```

### 7. Проверка на OOM-killer

```bash
cat dmesg.log | grep -iE 'oom|kill|out of memory'
cat dmesg.previous-boot.log | grep -iE 'oom|kill|out of memory'
```
Пусто = OOM-killer не срабатывал, брокер не убит ядром.

### 8. Свести в таблицу

| Утверждение | Статус | Источник |
|---|---|---|
| HEAP = 52.9 MB (растёт) | 🟢 | mosquitto-sys.log → `heap/current: 52934624` |
| Uptime брокера = 22 ч | 🟢 | mosquitto-sys.log → `uptime: 79343` |
| 3 p5-клиента | 🟢 | last_logs.log → `as 2kW5j... (p5, c1, k60)` |
| z2m установлен, не запущен | 🟢 | dpkg_l.log + ps_aux.log (пусто) |
| dmesg без OOM | 🟢 | dmesg.log: 0 matches |
| Памяти >1 GB | 🟢 | free.log → `free: 1333` |

## Питфоллы

- **wb-release.log часто пуст** — это не файл версии, а журнал systemd (`journalctl -u wb-release`), пуст если сервис не запускался в сеансе. Версию смотри по `dpkg_l.log`.
- **dmesg чистится при загрузке** — ранние OOM вытеснены; смотри `dmesg.previous-boot.log`.
- **last_logs.log фрагментирован** — ~последние 1500 строк; более ранняя проблема может отсутствовать.
- **free.log — snapshot** — мгновенный замер; если сборка диага 10+ секунд, процессы могут завершиться между замерами.
- **Не путай `memory_limit` с `max_queued_messages`** — первое счётчик кучи, второе размер очереди per-client. При #3192 страдает memory_limit.
