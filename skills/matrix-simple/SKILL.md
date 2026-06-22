---
name: matrix-simple
description: Матрица через простой HTTP-клиент. Комнаты, история, поиск, отправка.
version: 1.0.0
author: adsrv
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Matrix, messaging, simple-client]
  related_skills: [telegram-user-client]
prerequisites:
  env: [MATRIX_HOMESERVER, MATRIX_USERNAME, MATRIX_PASSWORD]
---

# Matrix (simple) — работа агенту

matrix-simple — плагин Hermes, подключающий лёгкого Matrix-клиента без E2EE. 
Агент Kern общается через него с пользователями, в частности с Макаром (WB, 
Wildberries). Платформа работает через gateway: входящие сообщения приходят 
как события сессии, исходящие — отправляются через `send` адаптера.

Для операций, не покрытых адаптером (список комнат, история, поиск), 
используй терминал и HTTP-запросы к Matrix API.

## Переменные окружения

| Переменная           | Назначение                                          |
|----------------------|-----------------------------------------------------|
| `MATRIX_HOMESERVER`  | URL сервера (напр. `https://matrix.adsrv.ru`)       |
| `MATRIX_USERNAME`    | Логин (напр. `kern`)                                |
| `MATRIX_PASSWORD`    | Пароль                                              |
| `MATRIX_ALLOWED_USERS` | Кому разрешено писать (через запятую)            |
| `MATRIX_HOME_ROOM`   | ID комнаты для cron/нотификаций (опционально)       |

## Получение токена

Все HTTP-запросы требуют `access_token`. Получи его однократно за сессию:

```bash
TOKEN=$(curl -sS -X POST "$MATRIX_HOMESERVER/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$MATRIX_USERNAME\"},\"password\":\"$MATRIX_PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

Токен живёт пока сессия. Если протух — получи заново.

---

## Команды

### 1. Список комнат

Показать все комнаты, в которых состоит Kern, с именами и ID:

```bash
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/joined_rooms?access_token=$TOKEN" \
  | python3 -c "
import sys, json
rooms = json.load(sys.stdin)['joined_rooms']
for rid in rooms:
    print(rid)
"
```

**Получить имена и последние сообщения комнат (одним запросом):**

```bash
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/sync?access_token=$TOKEN&timeout=0" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
join = data.get('rooms', {}).get('join', {})
for rid, rdata in join.items():
    name = rdata.get('state', {}).get('events', [])
    # Ищем m.room.name или m.room.canonical_alias
    display = rid
    for ev in name:
        if ev.get('type') == 'm.room.name':
            display = ev['content'].get('name', rid)
            break
        elif ev.get('type') == 'm.room.canonical_alias':
            display = ev['content'].get('alias', rid)
    last_msgs = rdata.get('timeline', {}).get('events', [])
    last = last_msgs[-1] if last_msgs else None
    sender = last.get('sender','?') if last else '—'
    body = (last.get('content',{}).get('body','')[:60]) if last else '—'
    print(f'{display}  →  {rid}')
    print(f'  Последнее: {sender}: {body}')
    print()
"
```

Сокращённый вариант — только ID и имена:

```bash
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/joined_rooms?access_token=$TOKEN" \
  | python3 -c "import sys,json;[print(r) for r in json.load(sys.stdin)['joined_rooms']]"
```

### 2. История чата

Получить последние N сообщений из комнаты:

```bash
ROOM='!abc123:matrix.adsrv.ru'
LIMIT=50
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/rooms/$ROOM/messages?access_token=$TOKEN&dir=b&limit=$LIMIT" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ev in reversed(data.get('chunk', [])):
    if ev.get('type') != 'm.room.message':
        continue
    sender = ev['sender'].split(':')[0].lstrip('@')
    body = ev['content'].get('body', '')
    ts = ev.get('origin_server_ts', 0)
    from datetime import datetime
    t = datetime.fromtimestamp(ts/1000).strftime('%H:%M')
    print(f'[{t}] {sender}: {body}')
"
```

**Пагинация (следующая страница истории):**

```bash
# Берём prev_batch из предыдущего ответа:
END_TOKEN='...'  # значение поля 'end' из первого запроса
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/rooms/$ROOM/messages?access_token=$TOKEN&dir=b&limit=$LIMIT&from=$END_TOKEN" | ...
```

### 3. Поиск по истории

Поиск по всем комнатам сразу:

```bash
QUERY='твой поисковый запрос'
curl -sS -X POST "$MATRIX_HOMESERVER/_matrix/client/v3/search?access_token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"search_categories\":{\"room_events\":{\"search_term\":\"$QUERY\",\"order_by\":\"recent\",\"event_context\":{\"before_limit\":1,\"after_limit\":1,\"include_profile\":true}}}}" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('search_categories',{}).get('room_events',{}).get('results',[])
for r in results:
    ev = r.get('result', {})
    sender = ev.get('sender','?').split(':')[0].lstrip('@')
    body = ev.get('content',{}).get('body','')[:100]
    rid = ev.get('room_id','')
    ts = ev.get('origin_server_ts', 0)
    from datetime import datetime
    t = datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M')
    print(f'[{t}] {sender} в {rid}:')
    print(f'  {body}')
    print()
"
```

Если сервер не поддерживает `/search` (Conduit), делай grep по сырой выдаче истории:

```bash
# Выгружаем историю комнаты и фильтруем клиентски
ROOM='!abc123:matrix.adsrv.ru'
SEARCH='ключевое слово'
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/rooms/$ROOM/messages?access_token=$TOKEN&dir=b&limit=200" \
  | python3 -c "
import sys, json, re
data = json.load(sys.stdin)
query = '$SEARCH'.lower()
for ev in reversed(data.get('chunk', [])):
    if ev.get('type') != 'm.room.message':
        continue
    body = ev['content'].get('body', '')
    if query in body.lower():
        sender = ev['sender'].split(':')[0].lstrip('@')
        print(f'{sender}: {body[:200]}')
        print('---')
"
```

### 4. Отправить сообщение

**Ответ в текущей сессии** — когда Kern уже в диалоге с пользователем через Matrix 
(сообщение пришло через gateway), агент отвечает **напрямую в чате**, без терминала. 
Gateway-сессия сама маршрутизирует ответ через адаптер `send()`.

**Отправить в конкретную комнату (явно, через API):**

```bash
ROOM='!abc123:matrix.adsrv.ru'
MESSAGE='Привет, это Kern.'
TXNID=$(uuidgen)
curl -sS -X PUT "$MATRIX_HOMESERVER/_matrix/client/v3/rooms/$ROOM/send/m.room.message/$TXNID?access_token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"msgtype\":\"m.text\",\"body\":\"$MESSAGE\"}"
```

### 5. Найти комнату по имени и написать

Сначала найти ID комнаты по имени (среди joined):

```bash
# Ищет комнату по имени (m.room.name) или каноническому алиасу
NAME='AI-Research'
curl -sS "$MATRIX_HOMESERVER/_matrix/client/v3/sync?access_token=$TOKEN&timeout=0" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
target = '$NAME'.lower()
found = None
for rid, rdata in data.get('rooms',{}).get('join',{}).items():
    for ev in rdata.get('state',{}).get('events',[]):
        if ev.get('type') == 'm.room.name':
            if target in ev['content'].get('name','').lower():
                found = rid
        elif ev.get('type') == 'm.room.canonical_alias':
            if target in ev['content'].get('alias','').lower():
                found = rid
    if found:
        break
if found:
    print(found)
else:
    print('NOT_FOUND')
"
```

Затем отправить сообщение в найденную комнату (см. пункт 4).

---

## Сценарий: Макар (WB, Wildberries)

Макар — пользователь Matrix, работает с маркетплейсом Wildberries. Контекст:
- Поставки, заказы, фулфилмент, возвраты
- Ценообразование, акции, ранжирование карточек
- Аналитика WB, работа с селлерами
- Технические интеграции (API WB, Excel, 1С)

При общении с Макаром:
- Отвечай технически точно, кратко
- Избегай маркетинговых фраз и лести
- Если нужны данные из интернета — используй web_search, не сочиняй
- Ссылки на источники — всегда

Макар пишет Kern в личку или в общие комнаты (AI-Research и др.).
Если Макар просит что-то сделать — подтверди понимание задачи одной фразой, 
затем выполняй.

### Правила ответа Макару

1. По делу, без воды. На вопрос «да/нет» — ответ одним словом
2. Технические детали — с примерами команд, где применимо
3. Если нужен доступ к серверу/API WB — запроси явно, не подставляй 
   вымышленные ключи/токены
4. Результаты аналитики — с конкретными цифрами и ссылками на источник

---

## Кросс-платформенная связь: Telegram ↔ Matrix

Сессии Telegram и matrix-simple **изолированы** (разные ключи `agent:main:{platform}:dm:{chat_id}`). Агент в телеграм-сессии **не видит** матрикс-сообщения автоматически. Используй инструменты Hermes для связи.

### Проверка матрикс-сессий из Telegram

Когда Александр (в Telegram) спрашивает «что там в матриксе» или «писал ли Макар»:

```
session_search  — поиск по всем сессиям, target="sessions"
```

Конкретно — найти сессии платформы `matrix-simple`:

```
session_search с запросом: "найди последние сессии matrix-simple и покажи непрочитанные сообщения"
```

Либо явно указать профиль:

```
session_search profile=default, поиск по ключевым словам: WB, Макар, поставка
```

### Форвард важного из Matrix в Telegram

Когда обнаружил важное сообщение от Макара, перешли его Александру:

```
send_message platform=telegram chat={TELEGRAM_HOME_CHANNEL} text="Макар в Matrix: ..."
```

Формат пересылки:

```
🟦 Matrix | {sender_name}
{текст сообщения}
— {комната}, {время}
```

### Ответ в Matrix из Telegram-сессии

Александр просит ответить Макару — используй `send_message` с platform=`matrix-simple`:

```
send_message platform=matrix-simple chat=!room_id:server text="ответ"
```

**Важно:** chat_id — полный Matrix room ID (`!abc123:matrix.adsrv.ru`). Найти его можно через список комнат (п.1) или через `session_search` (посмотреть `chat_id` в найденной сессии).

### Периодическая проверка

При старте каждой новой telegram-сессии с Александром — сделай `session_search` по платформе `matrix-simple` за последние 24 часа. Если есть новые сообщения от Макара — сообщи Александру кратко: кто, когда, суть.

Не спамь — если за последний час уже проверял и нового нет, не повторяй.

---

## Примечания

- Адаптер matrix-simple **не поддерживает E2EE** (end-to-end encryption). 
  Сообщения читаются только в незашифрованных комнатах.
- Реакции (emoji) передаются через адаптер и видны обеим сторонам.
- Если ответ не доходит — проверь `MATRIX_HOMESERVER` и токен (повтори логин).
- На Conduit-серверах `/search` может не работать — используй клиентский поиск 
  через выгрузку истории + grep.
