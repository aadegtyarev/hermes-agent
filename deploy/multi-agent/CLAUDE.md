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

## Re-render (менялись agents.yaml / base/config.base.yaml)
Под rootless `data/` и `config.yaml` принадлежат sub-UID (10001) — host `render.py`
падает `PermissionError: .../data/skills`. Обязателен chown-танец (детали и точные
команды — `SERVER-DEPLOY.md`, разделы про issue #12 и rw-config.yaml):
```
chown data+config -> 0:0  (через контейнер)   # отдать хосту
python3 render.py
chown data+config -> 10001:10001              # вернуть агенту
docker compose ... up -d hermes-gpio
```

## Перечитать config.yaml
`config.yaml` — бинд-маунт. Если менялся ТОЛЬКО он (например слаги моделей), а
`docker-compose.generated.yml` — нет, то `up -d` покажет «Running» и НЕ пересоздаст
контейнер. Чтобы применить новый конфиг → **`docker restart hermes-gpio`**
(`restart` сохраняет proxy-env от прошлого `up`).

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

## Диагностика падений
`docker logs --tail 80 hermes-gpio`. Частые причины:
- `insufficient_quota` / `You exceeded your current quota` → **биллинг OpenAI**,
  а НЕ модель/ключ (бьёт по всем моделям сразу; ключ при этом валиден). Чинится
  только пополнением на platform.openai.com — деплоем не лечится.
- `401 invalid_api_key` → ключ не тот / не перечитался (нужен `up -d`, не `restart`).
- `model_not_found` → слаг недоступен ключу (проверь `curl /v1/models/<slug>`).
