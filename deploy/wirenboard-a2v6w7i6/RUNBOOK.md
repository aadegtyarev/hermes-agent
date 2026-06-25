# Hermes на wirenboard-a2v6w7i6 — runbook

Заметки по деплою и эксплуатации Hermes на контроллере `wirenboard-a2v6w7i6.local`.
Без секретов. Обновлено: 2026-06-25.

## Где что лежит

| Что | Путь на контроллере |
|-----|---------------------|
| Активный compose + `.env` | `/mnt/data/hermes/` (рабочая директория стека) |
| Данные Hermes (→ `/opt/data`) | `/mnt/data/hermes-data/.hermes/` |
| SOUL.md / AGENTS.md (→ `/opt/data/...`) | `/mnt/data/hermes-data/{SOUL,AGENTS}.md` |
| Пользовательские плагины | `/mnt/data/hermes-data/.hermes/plugins/` |
| ⚠ Мёртвая старая копия — НЕ использовать и не править | `/mnt/data/hermes-data/hermes/` |

Образ: `hermes-agent:local`. Контейнер: `hermes`. Compose без `env_file` — берёт
`./.env` из рабочей директории (`/mnt/data/hermes/.env`). Гейтвей внутри контейнера
после privilege-drop работает под uid **10000** (`hermes`).

```bash
docker restart hermes                  # ⚠ см. раздел «Права при рестарте» ниже
docker logs --tail 50 hermes
docker exec hermes hermes plugins list
```

## Генерация изображений (OpenRouter)

Бэкенд `openrouter` — мультимодальные модели с выводом картинок (Gemini / Flux /
GPT-5 Image). Это `kind: backend` для встроенного тула `image_generate`.

- Код на сервере: `~/.hermes/plugins/image_gen/openrouter/`
  (реестровый ключ — `image_gen/openrouter`; каталог `image_gen` — это категория,
  своего `plugin.yaml` у неё нет, поэтому ключ префиксуется).
- Исходник в репозитории: `plugins/image-gen/openrouter/`.
- Требует `OPENROUTER_API_KEY` в `/mnt/data/hermes/.env` (тот же ключ, что и для vision).
- Тулсет `image_gen` должен быть в `toolsets:` конфига (уже есть).

Включение:

```bash
docker exec hermes hermes plugins enable image_gen/openrouter
docker exec hermes hermes config set image_gen.provider openrouter
docker exec hermes hermes config set image_gen.openrouter.model google/gemini-2.5-flash-image
docker restart hermes      # ⚠ см. раздел «Права при рестарте»
```

Итог в `config.yaml`:

```yaml
plugins:
  enabled:
    - image_gen/openrouter
image_gen:
  provider: openrouter
  openrouter:
    model: google/gemini-2.5-flash-image
```

Проверка: попросить бота сгенерировать картинку — должен вернуть PNG.
Цепочка: `image_generate` → активный провайдер `openrouter` → модель Gemini.

## ⚠ Права при рестарте (HERMES_UID)

**Симптом.** После `docker restart` в логах сыплются
`PermissionError: [Errno 13] Permission denied: '/opt/data/...'`
(`.env`, `pairing/*`, `kanban.db.init.lock`, `matrix_threads.json`), CLI внутри
контейнера перестаёт читать конфиг, бот «залипает» / не отвечает.

**Причина.** В `.env` стоит `HERMES_UID=0`, но s6-overlay stage2-hook **отвергает
uid 0** (валидный диапазон 1–65534) и оставляет пользователя `hermes` на build-uid
**10000**. На части рестартов `/opt/data` остаётся `root:root 700` — гейтвей (uid
10000) не может войти в собственный каталог данных, и всё под ним недоступно.

**Восстановление (на хосте; повторный рестарт НЕ нужен — гейтвей сам поднимается
на retry-циклах за 1–2 мин, matrix/kanban/pairing переподключаются):**

```bash
chown -R 10000:10000 /mnt/data/hermes-data/.hermes
chown 10000:10000 /mnt/data/hermes-data/SOUL.md /mnt/data/hermes-data/AGENTS.md
```

**Чтобы не повторялось.** Держать дерево данных консистентно под uid 10000 и
поправить `HERMES_UID` в `/mnt/data/hermes/.env`: убрать строку либо выставить
`HERMES_UID=10000` (намерение = реальность). Тогда хук на старте видит совпадение
владельца `/opt/data` с uid `hermes` и не ломает права.

История: инцидент 2026-06-22 (см. заметку по matrix-simple) и повтор 2026-06-24
(после рестарта при включении image-gen).

---

# Конфигурация агента и инциденты (добавлено 2026-06-25)

## Где лежит config.yaml (легко теряется — был инцидент)

Gateway (`HERMES_HOME=/opt/data`) читает **`/opt/data/config.yaml`** = хост
**`/mnt/data/hermes-data/.hermes/config.yaml`**. Код агента — в образе под `/opt/hermes`
(volume только `/opt/data`).

- ⚠ Вложенный `/mnt/data/hermes-data/.hermes/.hermes/config.yaml` — **ЗАГЛУШКА, gateway его НЕ читает.** Не путать.
- `load_config()` мёржит дефолты (~67 ключей) поверх файла. Если файла **нет** — молча отдаёт
  чистые дефолты: пустая `model`, пустые `providers`, `approvals.mode=manual`. Бот при этом
  «работает», но ломается (см. ниже). Бэкапы: `config.yaml.bak-*` в той же папке.
- `config.yaml` перечитывается по **mtime** (без рестарта). НО: `cp -a` сохраняет старый mtime —
  после восстановления из бэкапа делать `touch config.yaml`, иначе кэш не обновится.

### Инцидент 2026-06-25: config.yaml был удалён целиком
Симптомы и причинно-следственная связь (всё от одного корня — пустой конфиг):
1. `HTTP 400 No models provided` от **openrouter** (в `logs/gateways/default/current`,
   `Provider: openrouter  Model:` пустой) — main-ходы валились, т.к. модель резолвилась пустой
   и уходила в openrouter-фолбэк без модели.
2. Вернулись approval-промпты (`python3 -c` и т.п.) — `approvals.mode` свалился в дефолтный `manual`.
3. Вторично: matrix-сессии логировали `context-overflow → compression exhaustion → auto-reset`.
   Это **НЕ настоящий overflow** (окно `deepseek-v4-flash` = 1 048 576, компрессор включается на
   100k). Это саммари-вызов компрессора ловил тот же `No models 400` → «exhaustion».

**Фикс:** восстановить из последнего полного бэкапа `config.yaml.bak-approvals-off-20260625` →
`chown 10000:10000` → `touch` (mtime). Проверка: `load_config()` отдаёт `model`/`providers`/`approvals`.

## Модель — два провайдера под разные цели
- **main** = `deepseek-v4-flash`, provider **deepseek** (`DEEPSEEK_API_KEY`).
- **auxiliary / vision** = `google/gemini-2.5-flash-lite`, provider **openrouter** (`OPENROUTER_API_KEY`).
- API-ключи берутся из **env** (`.env`), в конфиге `api_key: ''` (env wins). `deepseek-v4-flash`
  НЕ openrouter-модель — не путать.

## «Разрешить всё» (approvals)
- `approvals.mode: off` в config.yaml — снимает ВСЕ промпты, кроме hardline
  (`rm -rf /`, `mkfs`, `dd`→диск, reboot, fork bomb) и `sudo -S`. Применяется по mtime.
- `HERMES_YOLO_MODE=1` в `/mnt/data/hermes/.env` + passthrough в compose
  (`- HERMES_YOLO_MODE=${HERMES_YOLO_MODE:-0}`). Замораживается при старте процесса, бэкап-страховка:
  **переживает даже полное удаление config.yaml**. Требует пересоздания/рестарта контейнера.

## Matrix — видеть ВСЕ сообщения (ambient)
- Нужен **`matrix.require_mention: false` в config.yaml** (top-level `matrix:` блок —
  он подмешивается в `MatrixAdapter.config.extra`).
- ⚠ **GOTCHA:** env `MATRIX_REQUIRE_MENTION=false` САМ ПО СЕБЕ НЕ работает. Дефолт
  `matrix.require_mention=True` (`hermes_cli/config.py`) не-None, а env берётся только при None.
  → обязателен явный `false` в config.yaml.
- Адаптер читает `require_mention` **один раз в `__init__`** → нужен **рестарт** gateway
  (mtime-reload не помогает).
- `MATRIX_ALLOWED_USERS` (@alex/@makar/@claude) — отдельный гейт: ambient только от них, не от всех в комнате.
- Подавление чаттера тул-коллов в matrix: `display.platforms.matrix.tool_progress: "off"`
  (в кавычках! иначе YAML парсит `off`→`false`).

## Matrix reply-to патч (2026-06-25)
Правка `gateway/platforms/matrix.py`: тянет текст исходного сообщения через `get_event`
(`reply_to_text`, кап 500) → `run.py` добавляет префикс `[Replying to: "…"]`. В образе этого
не было, поэтому на Matrix reply-контекст не доходил до агента.
- Занесён в хостовый исходник `/mnt/data/hermes/gateway/platforms/matrix.py` (бэкап `.orig-20260625`)
  → попадёт в образ при следующей пересборке.
- Активирован сейчас **bind-mount'ом** в compose:
  `- /mnt/data/hermes/gateway/platforms/matrix.py:/opt/hermes/gateway/platforms/matrix.py:ro`.
- Standalone-копия + unified diff: `/mnt/data/hermes-data/_kern-patch-matrix-20260625/`.

## Диск — ограничение на пересборку
`/mnt/data` (`/dev/mmcblk0p6`, 12G) сидит на ~94% (≈710 МБ свободно). `docker compose build` на
изменение исходника пересоздаёт большие слои (`COPY . .` → `chown -R .venv` → `uv pip install
--no-cache-dir mautrix telethon…`) → на таком диске сборка рискует упасть. Поэтому matrix-патч
активирован mount'ом, а не пересборкой. Перед реальной сборкой: `docker image prune` (≈735 МБ
висячих образов); **build-cache не прунить** (иначе `uv sync --extra all` передачает все зависимости).
