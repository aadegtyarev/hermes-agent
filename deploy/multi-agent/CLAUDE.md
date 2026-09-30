# Правила деплоя (agent: gpio) — ЧИТАТЬ ПЕРЕД ЛЮБЫМ ДЕПЛОЕМ

Полный рунбук — `SERVER-DEPLOY.md` (в этой папке). Здесь — короткий чеклист
операционных правил, которые легко забыть. Архитектура/изоляция — `README.md`.

## Где что
- **Сервер:** `ssh <user>@<server-ip>` (hostname задаётся оператором), rootless docker,
  без sudo. Репо: `~/docker/hermes-agent`. Всё кастомное — в `deploy/multi-agent/`.
- **Compose-сервис = `hermes-gpio`** (и это же `container_name`), НЕ `gpio`.
  Команды: `docker compose -f docker-compose.generated.yml up -d hermes-gpio`.
- **Git remotes:** `origin` = upstream `NousResearch/hermes-agent` (нет прав на push).
  Наш форк = `fork` = `aadegtyarev/hermes-agent`. **Сервер тянет из форка**
  (`origin` НА СЕРВЕРЕ = `https://github.com/aadegtyarev/hermes-agent.git`, ветка `main`).

## Поток изменений (НИКОГДА не редактировать трекаемые файлы на сервере)
1. Правки локально → коммит на ветку (не в main).
2. `git push fork <branch>` → PR в `aadegtyarev/hermes-agent` (base `main`) → squash-merge.
3. На сервере: `git pull --ff-only origin main`.
4. Re-render + пересоздание (см. ниже).

## Redeploy — используй `bin/redeploy.sh`, не пляши вручную
```bash
deploy/multi-agent/bin/redeploy.sh gpio   # агент по умолчанию — gpio
```
Делает весь безопасный порядок разом: пересборка обоих образов (no-op, если
код не менялся — кэш слоёв) → chown `data/`+`config.yaml` на `0:0` →
`render.py` → chown обратно на `10001:10001` → `up -d` + безусловный
`docker restart` (иначе content-only правка `config.yaml` не подхватится).
Идемпотентен, можно гонять после **любого** изменения: код ядра, плагина,
скилла, `agents.yaml`, `config.local.yaml` — один и тот же вызов.

**Когда действительно нужен re-render** (не только agents.yaml/config.base.yaml!):
любое изменение кода в `base/plugins/<name>/` или контента `base/skills/<name>/`
— эти файлы копируются в `render/gpio/plugins/` и `instances/gpio/data/skills/`
именно рендером, обычный `docker build` + рестарт их не обновит.

**Обновление уже посеянного скилла** (`base/skills/<name>/` изменился, но
скилл уже был засеян раньше — copy-IF-ABSENT его не тронет):
```bash
deploy/multi-agent/bin/refresh-skill.sh gpio telegram-guide research
```

Пляска chown никуда не девается физически (у `render.py` два семейства
файлов с разным владением под rootless — хостовые `docker-compose.generated.yml`/
`render/` и sub-UID `data/`/`config.yaml`; свести к одному UID нельзя не
ослабив изоляцию — см. пункт про `HERMES_UID=$(id -u)` ниже), но теперь она
живёт внутри скрипта, а не в голове оператора. Точные команды — по-прежнему
в `SERVER-DEPLOY.md` (issue #12 / rw-config.yaml) — читать, если сам скрипт
падает и нужно разбираться руками.

## НЕ сноси `data/skills` — там накопленные агентские скиллы
`instances/gpio/data/skills/` (`/opt/data/skills`, writable, gitignored) хранит
**важные** скиллы, которые агент накопил через `skill_manage` (их нет в репе).
**НИКОГДА не `rm -rf data/skills` целиком.** Стандартный `render.py` их сохраняет
(copy-if-absent). Детали и как обновлять base-скилл точечно — `SERVER-DEPLOY.md`,
раздел «Откат / пересборка».

## Перечитать config.yaml
`bin/redeploy.sh` уже делает безусловный `docker restart` после `up -d` именно
по этой причине: `config.yaml` — бинд-маунт, и если менялся ТОЛЬКО он (например
слаги моделей через `config.local.yaml`), `up -d` сам по себе покажет «Running»
и НЕ пересоздаст контейнер. Руками так же: **`docker restart hermes-gpio`**
(сохраняет proxy-env от прошлого `up`).

## Секреты (OPENAI_API_KEY и пр.)
`.env` gitignored — реальные ключи живут ТОЛЬКО на сервере (`instances/gpio/.env`),
в репозиторий не коммитятся. Менять ключ → править `.env` на сервере, затем
**`up -d`** (пересоздаёт контейнер, перечитывает `env_file`) — `restart` env_file НЕ
перечитывает. `render.py` для смены ключа не нужен (в config.yaml ключ не пишется:
там `${OPENAI_API_KEY}`, hermes разворачивает в рантайме).

## Прокси
Сервер ходит наружу через корпоративный HTTP-прокси (`<proxy-host>:<port>`). `render.py` пробрасывает
proxy-env в контейнер на момент `up`. Запускай `up -d` в шелле, где
`HTTP_PROXY/HTTPS_PROXY` экспортированы (в SSH-профиле уже есть).

## config.local.yaml может разойтись с реально работающей моделью
`render.py` берёт `model`/`delegation`/`auxiliary` ЦЕЛИКОМ из
`config.local.yaml` (полная замена секции, не merge) — если модель когда-то
поменяли не через этот файл (руками в `config.yaml`, другим способом), то
`bin/redeploy.sh`/`render.py` **тихо откатит** её обратно к тому, что записано
в `config.local.yaml`. Перед редеплоем, если не уверен — сверь `model`/
`delegation` в живом `/opt/data/config.yaml` (`docker exec hermes-gpio cat
/opt/data/config.yaml`) с `instances/gpio/config.local.yaml`, прежде чем
рендерить. Так уже случалось (30.09.2026) — модель откатилась с `gpt-6-luna`/
`gpt-5.6-sol` на `gpt-5.4` при обычном редеплое; пришлось восстанавливать
вручную.

## Диагностика падений
`docker logs --tail 80 hermes-gpio`. Частые причины:
- `insufficient_quota` / `You exceeded your current quota` → **биллинг OpenAI**,
  а НЕ модель/ключ (бьёт по всем моделям сразу; ключ при этом валиден). Чинится
  только пополнением на platform.openai.com — деплоем не лечится.
- `401 invalid_api_key` → ключ не тот / не перечитался (нужен `up -d`, не `restart`).
- `model_not_found` → слаг недоступен ключу (проверь `curl /v1/models/<slug>`).
