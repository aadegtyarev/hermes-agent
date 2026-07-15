# YouTrack API Notes (Wiren Board Cloud)

Инстанс: `${YOUTRACK_URL}/api` (базовый URL — из env `YOUTRACK_URL`, в репозитории не хранится).

## Теги

### Поиск тега по имени
```
GET /api/tags?query=ai_auto&fields=id,name
```
- Возвращает массив, пустой если не найден
- **Не листать все теги** — `$top` выдаёт первые N по порядку создания, без сортировки
- В YouTrack Cloud ~250+ тегов, наш `ai_auto` (id `7-680`) выпадает за $top=200

### Создание тега
```
POST /api/tags
Body: {"name": "ai_auto"}
Params: ?fields=id,name
```
- Возвращает 400 `"Property Tag.name is invalid"` / `"у пользователя уже есть тег с именем X"` если тег уже существует
- **`POST /api/admin/tags` — НЕ РАБОТАЕТ** (404 Not Found на облачном инстансе)

### Применение тега к задаче
```
POST /api/issues/{issueId}/tags
Body: {"id": "7-680"}
Params: ?fields=id
```
- ❌ `{"tag": {"id": "7-680"}}` → 400 Bad Request "укажите ID"
- ✅ `{"id": "7-680"}` → 200
- PUT `/api/issues/{issueId}/tags/{tagId}` → 404 (эндпоинт не существует)

## Задачи

### Создание
```
POST /api/issues
Params: ?fields=idReadable,summary
Body: {
  "summary": "...",
  "project": {"id": "0-1"},
  "description": "опционально"
}
```
- `project.id` — это UUID/hex-id, не shortName
- Получить `id` по `shortName`: `GET /api/admin/projects?fields=id,shortName&$top=200`
- Возвращает `idReadable` (например `SOFT-5678`) и `summary`

### customFields при создании (Assignee / Type)
Формат зависит от `$type` поля в конкретном инстансе — брать из `GET /api/issues/{id}?fields=customFields(name,$type,value(name))` реального тикета, не угадывать.
```json
"customFields": [
  {"name": "Assignee", "$type": "MultiUserIssueCustomField", "value": []},
  {"name": "Type",     "$type": "SingleEnumIssueCustomField", "value": {"name": "Идея"}}
]
```
- **Assignee здесь `MultiUserIssueCustomField`** (не Single!) → «без исполнителя» = пустой список `[]`, не `null`. Неверный `$type` → 400.
- **Type** — `SingleEnumIssueCustomField`, значение `{"name": "<точное имя>"}`. Плагин отдаёт три типа: `Идея, Bug, Task` (бандл «Типы» в инстансе шире — есть ещё `Epic, Работа` — но тула ограничена тремя через `enum`). Дефолт типа настраивается в самом YouTrack (на стороне проекта) — не форсим в коде, задаём только если параметр `type` передан.

### Просмотр тегов задачи
```
GET /api/issues/{issueId}?fields=idReadable,tags(id,name)
```

## Комментарии

### Добавление
```
POST /api/issues/{issueId}/comments
Params: ?fields=id,text
Body: {"text": "Комментарий\n\n#ai-auto"}
```
- issueId — читаемый ID (SOFT-5678), не UUID
- Маркер `#ai-auto` дописывается в конец для идентификации AI-комментариев

### Получение
```
GET /api/issues/{issueId}/comments
Params: ?fields=text,author(fullName,login),created,updated&$top=50
```

## Структура плагина Hermes для YouTrack

`/opt/data/.hermes/plugins/youtrack/`

```
youtrack/
├── plugin.yaml         # metadata + provides_tools
├── __init__.py         # imports + _TOOLS tuple registration
└── youtrack.py         # schemas + handlers + HTTP helpers
```

**Правило трёх файлов:** инструмент должен быть перечислен ВО ВСЕХ ТРЁХ:
1. `plugin.yaml` → `provides_tools: [..., yt_create_issue]`
2. `__init__.py` → импорт хендлера + схема + строка в `_TOOLS`
3. `youtrack.py` → функция-хендлер + схема JSON

Если расхождение в любом из трёх — **ноль инструментов загружается**, плагин виден как `enabled` но не даёт ни одной тулзы.
