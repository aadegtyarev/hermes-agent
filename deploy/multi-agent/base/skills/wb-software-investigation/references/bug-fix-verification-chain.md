# Bug Fix Verification Chain (Ironclad Certainty)

## Когда применять

Перед передачей выводов о баге и фиксе команде разработки. Каждый шаг — только после подтверждения предыдущего.

## Цепочка: 7 шагов

### Шаг 1: Подтвердить баг в коде (форк)

Найти конкретные строки с багом в исходниках **форка** (что стоит на устройстве), а не только в upstream.

```bash
# 1. Какая версия на устройстве?
dpkg -l mosquitto  # → 2.0.20-1-wb102

# 2. Проверить, что баг есть в этой версии
curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/v2.0.20-1-wb102/lib/property_mosq.c' \
  | grep -n 'free(\*property)\|calloc(\|malloc('
# Ожидается: строка 244: free(*property); — БАГ
```

**Критерий:** строка кода с багом найдена, указан line number — проверено на теге форка, не «этот файл обычно содержит баг».

### Шаг 2: Подтвердить, что upstream-фикс покрывает баг

```bash
# 1. Коммит существует?
curl -sL 'https://api.github.com/repos/eclipse/mosquitto/commits/015fe3d68784' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['commit']['message'][:200])"
# Ожидается: "Fix mismatched wrapped/unwrapped memory alloc/free in properties. Closes #3192."

# 2. Issue закрыт?
curl -sL 'https://api.github.com/repos/eclipse/mosquitto/issues/3192' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['state'], [l['name'] for l in d['labels']])"
# Ожидается: closed
```

**Критерий:** коммит-фикс существует в upstream, issue закрыт, changelog упоминает фикс.

### Шаг 3: Проверить, что патч ложится на форк чисто

```bash
# Сравнить property_mosq.c форка и upstream на одинаковой upstream-версии
diff <(curl -sL 'https://raw.githubusercontent.com/wirenboard/mosquitto/debian/2.0.20-1/lib/property_mosq.c') \
     <(curl -sL 'https://raw.githubusercontent.com/eclipse/mosquitto/v2.0.20/lib/property_mosq.c')
# Ожидается: пустой diff — WB не менял файл
```

**Критерий:** diff пустой или подтверждённые неконфликтующие изменения (только packaging).

### Шаг 4: Верифицировать собранный пакет

Два метода — **BuildID (основной)** и **strings (запасной)**. Начинай с BuildID.

#### 4a. BuildID — самый надёжный (основной)

BuildID — строгая контрольная сумма ELF-секций. Разный BuildID у установленной версии и собранного .deb доказывает, что бинарник физически другой (патч скомпилирован).

```bash
# 1. BuildID установленного бинарника
ssh root@<controller> 'readelf -n /usr/sbin/mosquitto 2>/dev/null | grep "Build ID"'
# → 2320d40e...  (wb102)

# 2. BuildID в собранном .deb
dpkg-deb -x packages/mosquitto_2.0.20-1-wb103_armhf.deb /tmp/deb-check
readelf -n /tmp/deb-check/usr/sbin/mosquitto 2>/dev/null | grep "Build ID"
# → 74383ac6...  (wb103, другой!)

# 3. То же для библиотеки (где property_mosq.o):
dpkg-deb -x packages/libmosquitto1_2.0.20-1-wb103_armhf.deb /tmp/deb-lib
readelf -n /tmp/deb-lib/usr/lib/arm-linux-gnueabihf/libmosquitto.so.2.0.20 2>/dev/null | grep "Build ID"
# → ed493ac9...  (wb103)
```

**Критерий:** BuildID установленной версии ≠ BuildID в .deb хотя бы для одного из mosquitto / libmosquitto1. Совпадают — патч не попал в бинарник.

#### 4b. Changelog — подтверждение свежести пакета

```bash
dpkg-deb -x packages/mosquitto_2.0.20-1-wb103_armhf.deb /tmp/deb-broker
zcat /tmp/deb-broker/usr/share/doc/mosquitto/changelog.Debian.gz | head -10
# Ожидается:
#   mosquitto (2.0.20-1-wb103) stable; urgency=medium
#     * Fix property__free() using free() instead of mosquitto__free()
#       - Bug #3192: incorrect heap accounting triggers false OOM
```

**Критерий:** запись о фиксе есть, дата сегодня/вчера, версия следующая за установленной (wb102 → wb103).

#### 4c. strings — запасной метод (когда BuildID недоступен)

```bash
dpkg-deb --fsys-tarfile packages/mosquitto_2.0.20-1-wb103_armhf.deb \
  | tar xO ./usr/sbin/mosquitto 2>/dev/null \
  | strings | grep -c "mosquitto__free"
# Много (десятки) — код использует обёрнутые функции
```

**Критерий:** >0 вхождений. **Важно:** может давать ложноположительный результат — `mosquitto__free` есть и в wb102 (для других функций). BuildID точнее.

### Шаг 5: Воспроизвести баг на стенде до фикса

A) **Ускоренное** (17 секунд):
```bash
python3 scripts/mqtt-heap-stress.py <host> <port> 5000
# Ожидается: HEAP + ~250KB, RSS +0
```

B) **Изолированный брокер**: минимальный конфиг `memory_limit 500000`, persistent подписчик (v5, -c, QoS2), v5-молоток. Ожидается: broker survives, client disconnected at ~3000-3200 publish.

C) **Измерение в поле** (медленно): замерить HEAP дважды с интервалом 15-60с. Ожидается: HEAP растёт ~77 байт/сек (при p5-клиенте).

**Критерий:** HEAP растёт, RSS не меняется.

### Шаг 6: Установить фикс и проверить, что HEAP перестал расти

```bash
dpkg -i packages/mosquitto_2.0.20-1-wb103_armhf.deb \
         packages/libmosquitto1_2.0.20-1-wb103_armhf.deb
systemctl restart mosquitto
python3 scripts/mqtt-heap-stress.py localhost 1883 5000
# Ожидается (свежий брокер): HEAP + ~2KB (разовая инициализация), RSS +0
```

**Критерий:** те же операции НЕ растят HEAP.

### Шаг 7: Оформить (два архива)

См. SKILL.md → «Оформление результата: два архива». Правила:
- URL — GitHub raw links, не guess-URLs
- line numbers — из read_file с конкретными файлами
- числа — из измерений (script output), не оценки
- Ложные следы — отдельно; A/B тест — обязательно с таблицей до/после

## Подводные камни

| Ошибка | Почему | Как избежать |
|--------|--------|-------------|
| Проверять upstream вместо форка | Форк мог изменить файл | Всегда проверять на теге форка |
| Не проверять, что memory_limit активен | Баг есть, но не проявляется (mem_limit=0) | `grep -r memory_limit /etc/mosquitto/` |
| Ставить фикс без бэкапа | Нужен rollback | `dpkg -i` сохраняет старый .deb |
| Верить `VmSize` вместо RSS | VmSize = адресное пространство, не физпамять | `VmRSS` из `/proc/pid/status` |
| Не чистить окружение между A и B | Остаточный HEAP от прошлого теста | `systemctl restart mosquitto` перед замером |
| Пропустить `libmosquitto1` | mosquitto не стартует (version mismatch) | Ставить оба deb |
| Доверять `strings\|grep` без BuildID | `mosquitto__free` есть и в багнутой версии | BuildID как основной метод |
| Сравнивать только mosquitto | Патч в libmosquitto1 | Проверять оба пакета |
