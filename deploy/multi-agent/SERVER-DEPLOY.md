# Развёртывание multi-agent Hermes на сервере (агент «Gpio»)

Инструкция для Claude, работающего **на сервере**. Разворачиваем наш форк Hermes в
режиме multi-agent — изолированный агент-персона **Gpio** (инженерный ассистент
Wiren Board) со своим Telegram-ботом и памятью в отдельном контейнере. Всё
кастомное живёт в `deploy/multi-agent/`, upstream-файлы мы не трогаем (`git pull`
остаётся без конфликтов).

Полный контекст архитектуры/изоляции — в `README.md` этой папки. Ниже —
исполняемый рунбук.

## Правила
- **Секреты не выдумывай.** Все ключи/токены (OPENAI, TELEGRAM, GITHUB, YOUTRACK,
  SSH, BW) даёт человек. Нет ключа — **остановись и попроси**, не
  подставляй заглушки и не коммить `.env`.
- Ничего не коммить и не пушить без явной просьбы.
- Действуй по шагам; после каждого блока показывай вывод и жди, если что-то не так.

## 0. Подготовка репозитория
Multi-agent давно влит в `main`; сервер тянет `main` из нашего форка
(`origin` НА СЕРВЕРЕ = `aadegtyarev/hermes-agent`). Отдельной ветки деплоя больше нет.
```bash
# репо уже склонировано? если нет — склонируй наш форк (aadegtyarev/hermes-agent)
git fetch --all
git checkout main
git pull --ff-only origin main
```
Проверь, что есть `docker`, `docker compose` и `python3`.

## 1. Собрать два образа
```bash
cd <repo-root>
docker build --network=host -t hermes-agent:base .                          # upstream base (тяжёлый: Node+Chromium); тег ДОЛЖЕН быть hermes-agent:base
docker build --network=host -t hermes-multiagent:latest deploy/multi-agent  # overlay: gh/ssh/sshpass/unar/bw + py-deps
```
`--network=host` **обязателен**: без него apt в buildkit-bridge не достаёт зеркала
Debian (:80), сборка падает `Unable to locate package …` (см. «Вероятные проблемы»).

## 2. Гейт: слаги моделей (зависит от КЛЮЧЕЙ DeepSeek + OpenAI)
- **Болталка — DeepSeek** `deepseek-v4-flash`, `reasoning_effort: medium` (ключ `DEEPSEEK_API_KEY`).
- **Делегация (сабагент) — DeepSeek** `deepseek-v4-pro`, `reasoning_effort: high` — эскалация на модель сильнее болталки (ключ `DEEPSEEK_API_KEY`).
- **Vision-фолбек (`auxiliary.vision`) + генерация картинок (`image_gen`) — OpenAI**
  `gpt-5.4-nano` / `gpt-image-2` (ключ `OPENAI_API_KEY`). DeepSeek текстовый — картинки не через него.

Сверяй вместе с шагом 3: `deepseek-v4-flash` и `deepseek-v4-pro` доступны ключу DeepSeek, а
`gpt-5.4-nano`/`gpt-image-2` — ключу OpenAI. Если нет — поправь `defaults.model.default`
в `agents.yaml` и `base/config.base.yaml` (`model` → flash, `delegation` → pro;
`auxiliary.vision`/`image_gen` → OpenAI).

## 3. Рендер + секреты
```bash
cd deploy/multi-agent
python render.py                                   # agents.yaml -> docker-compose.generated.yml + curated dirs
cp instances/gpio/.env.example        instances/gpio/.env
```
Заполни `instances/gpio/.env` (значения — от человека):
- `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USERS=` — **оставить пустым** (гейтинг делает плагин telegram-context)
- `TELEGRAM_ADMIN_USERS=` — **user id операторов бота** (кто может прописывать чаты
  командами). Задаётся один раз.
- `TELEGRAM_WORK_CHATS=` / `TELEGRAM_READONLY_CHATS=` — **можно оставить пустыми**:
  чаты добавляются в рантайме командами в самой телеге (см. ниже), файлы править не
  нужно. Если что-то хочется зафиксировать намертво — впиши сюда (env ∪ рантайм-стор).
- `GITHUB_TOKEN` (+ `GITHUB_ALLOWED_REPOS`), `YOUTRACK_URL` / `YOUTRACK_TOKEN`
- `SSH_*` (опционально; пустой allowlist = любой хост), `BW_*` (bitwarden)

Google (опционально, интерактивный OAuth — запускает **человек** у себя):
```bash
python base/plugins/authorize-google.py client_secret.json instances/gpio/secrets/google-token.json
```

### Серверные оверрайды конфига: `instances/<name>/config.local.yaml` (gitignored)
Если нужно поменять что-то в `config.yaml` **только на сервере, вне репы, и чтобы это
переживало re-render** — положи `instances/<name>/config.local.yaml`. `render.py` при
генерации **wholesale-заменяет** каждую верхнеуровневую секцию из этого файла в итоговом
`config.yaml` (это не deep-merge: секция берётся целиком, поэтому унаследованные ключи
вроде `api_key: ${DEEPSEEK_API_KEY}` не залипают). Файл в `.gitignore`, значения в репу
не попадают.

Пример — переключить провайдеров на openai-codex (OAuth через `hermes auth add openai-codex`,
креды в `/opt/data/auth.json`):
```yaml
# instances/gpio/config.local.yaml
model:        { provider: openai-codex, default: gpt-5.4 }
delegation:   { provider: openai-codex, model: gpt-5.4, reasoning_effort: high }
auxiliary:
  vision:     { provider: openai-codex, model: gpt-5.4 }
# image_gen НЕ трогаем — codex картинки не генерит, остаётся openai (нужен OPENAI_API_KEY).
# -codex-модели (gpt-5.3-codex) с ChatGPT-аккаунтом НЕ работают (400); доступны
# gpt-5.4/5.5/5.4-mini/gpt-5.3-codex-spark.
```
Куратор/самообучение/compression и пр. — `provider: auto` → едут на основную модель, отдельно не задаём.

## 4. Запуск + проверка
```bash
docker compose -f docker-compose.generated.yml up -d
docker logs hermes-gpio          # gateway поднялся? загружены ТОЛЬКО granted-плагины?
loginctl enable-linger "$(whoami)"   # ОБЯЗАТЕЛЬНО в rootless: иначе контейнер умрёт при выходе из SSH (см. «Вероятные проблемы»)
```
Чек-лист первого старта:
- [ ] `config.yaml` смонтирован `:ro`; chmod в entrypoint падает **не фатально**
      (в логе warning, не краш).
- [ ] Память: у агента создалась своя `instances/gpio/data/memory_store.db` (holographic, локальный SQLite), `fact_store` пишет/читает.
- [ ] Telegram-гейтинг: из work-чата → отвечает; из чужого чата → игнор;
      DM от участника work-чата → отвечает, от постороннего → игнор.
- [ ] Динамический список чатов: оператор (из `TELEGRAM_ADMIN_USERS`) добавляет бота
      в чат и пишет `/hermes_here` → бот подтверждает «✅ …» и начинает отвечать;
      `/hermes_readonly` → read-only; `/hermes_forget` → убрать; `/hermes_chats` →
      список. От не-оператора — «⛔». (Команды доходят даже при включённом privacy
      mode; для полного ингеста истории privacy mode всё равно выключи в @BotFather.)
- [ ] Пара тулов живые: `web_search`, `ssh_run` (если задан хост), `vault_get`,
      `telegram_recent`.
- [ ] mDNS: `docker exec hermes-gpio getent hosts wirenboard-<SN>.local` резолвит IP,
      и `docker exec hermes-gpio avahi-browse _workstation._tcp -tpr` выдаёт контроллеры
      (`wirenboard-<SN>.local <IP>`, issue #13). Пусто → проверь `network_mode: host`
      и что борта в том же LAN-сегменте.
- [ ] Чат чистый — нет мусора (вызовы тулов / reasoning / статусы не сыпятся).

## Вероятные проблемы (временные воркэраунды — потом сделать нормально)
- **Сборка падает `Unable to locate package …`** — buildkit-bridge не достаёт apt-зеркала Debian по :80. Обязательно `docker build --network=host …` (шаг 1). TODO: настроить buildkit (DNS/зеркало), а не сеть хоста для сборки.
- **Сеть агента = `network_mode: host`** (уже в compose) — агенту нужен интернет (OpenAI/GitHub/web) и локальная сеть (SSH к контроллерам, mDNS-обнаружение). TODO: позже сузить (macvlan / явные маршруты) вместо полного host.
- **mDNS: `.local` резолвится прозрачно + avahi-browse для дискавери** (issue #13). Overlay ставит `avahi-daemon avahi-utils libnss-mdns dbus` и супервизит `dbus`+`avahi-daemon` (s6, root). Ключевой момент — `avahi-daemon.conf` с **`disable-publishing=yes`**: контейнерный avahi только *резолвит/браузит*, но НЕ анонсирует своё имя, поэтому спокойно сосуществует с хостовым avahi на 5353 (проверено: поднимается при живом хостовом). `libnss-mdns` вписан в NSS → `.local` резолвится нативно **любым тулом**: `getent hosts wirenboard-<SN>.local` / `ssh wirenboard-<SN>.local` (проверено на сервере). Дискавери — `avahi-browse _workstation._tcp`. Нужен `network_mode: host` (без него мультикаст не дойдёт). `ping`/`nmap -sn` в контейнере не работают (нет `cap_net_raw`) — это норма, ходи по TCP (`nmap -sT`) или по имени. Дашборд наружу не торчит: off по умолчанию.
  (Предыдущий заход был через python-`zeroconf`/`wb-discover` — откатили: спец-CLI, который модель должна помнить дёрнуть, — костыль; нативный резолв через NSS правильнее.)
- **Основная модель + делегация = нативный провайдер `deepseek`.** Болталка `default: deepseek-v4-flash` (medium), делегация `model: deepseek-v4-pro` (high — сильнее болталки), ключ ← `DEEPSEEK_API_KEY`. `base_url` НЕ задавать — явный `base_url` ломает chat_completions-модели (deepseek/glm), см. `model_switch.py`. DeepSeek ТЕКСТОВЫЙ: `supports_vision` не ставим, инлайновые картинки уходят на `auxiliary.vision` (OpenAI) через text-пайплайн (`image_routing.py`).
- **OpenAI (vision-фолбек + image_gen) = `custom` + `base_url`** — в hermes НЕТ провайдера `openai`; прямой api.openai.com настроен через `provider: custom` + `base_url: https://api.openai.com/v1` (ключ ← `OPENAI_API_KEY`), в `auxiliary.vision` и `image_gen`. НЕ менять на `openai` — иначе `Unknown provider 'openai'`. `auxiliary.vision.api_key` задан ЯВНО (`${OPENAI_API_KEY}`), т.к. основная модель — DeepSeek и ключ наследовать неоткуда.
- **Telegram-адаптер грузится только если платформа в bundled-каталоге.** Под мульти-агентом `HERMES_BUNDLED_PLUGINS=/opt/allowed/plugins` подменяет каталог плагинов, и загрузчик сканит платформы из `/opt/allowed/plugins/platforms/`. Поэтому `platforms/telegram` добавлен в `bundled_plugins` агента (`render.py` копирует его в `render/gpio/plugins/platforms/telegram`). Без этого — `No adapter available for telegram`, gateway живёт только для cron. `kind: platform` — auto-load, в `plugins.enabled` его НЕ вносим.
- **Прокси на выход (если хост ходит в интернет через proxy).** Ни Telegram, ни OpenAI не поднимутся без прокси, а прокси-env хоста в контейнер сам не попадает. `render.py` пробрасывает `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy/ALL_PROXY` из **хостового окружения на момент `docker compose up`** (пусто на хостах без прокси → напрямую). Значит `up -d` запускай в шелле, где proxy-переменные экспортированы. `NO_PROXY` зашит жёстко и держит `localhost`, `.local` и приватные сети (`10/8`, `172.16/12`, `192.168/16`) **мимо прокси** — SSH к контроллерам, mDNS и WB-веб идут напрямую. Telegram-адаптер сам умеет прокси: `TELEGRAM_PROXY` → иначе `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY`.
- **`config.yaml :ro` → миграция схемы падает** (`Read-only file system: config.yaml`, v0→v33) — **не фатально**, варнинг в логе, агент работает. TODO: генерить конфиг сразу в актуальной версии схемы.
- **Telegram**: `gateway run` требует валидный `TELEGRAM_BOT_TOKEN` — без него gateway не поднимется. Проверить без телеги — one-shot: `docker run --rm --network=host --env-file instances/gpio/.env -v "$PWD/instances/gpio/data:/opt/data" -v "$PWD/instances/gpio/config.yaml:/opt/data/config.yaml:ro" -v "$PWD/render/gpio/plugins:/opt/allowed/plugins:ro" -v "$PWD/render/gpio/bundled-skills:/opt/allowed/skills:ro" -e HERMES_BUNDLED_PLUGINS=/opt/allowed/plugins -e HERMES_BUNDLED_SKILLS=/opt/allowed/skills hermes-multiagent:latest hermes -z "17*23?"`.
- **Память локальная** (holographic): `instances/<agent>/data/memory_store.db` (SQLite, per-agent). Сайдкара памяти, `memory.env`, гейта hindsight-команды больше НЕТ.
- **Rootless без linger → бот умирает при выходе из сессии.** Rootless-демон и контейнер (`restart: unless-stopped`) живут под user-systemd. При `Linger=no` выход из SSH/логина сносит `/run/user/1001`, гасит user-сервис `docker` и **все контейнеры** — `unless-stopped` не спасает, умирает сам демон. Симптом: бот перестаёт отвечать И в личке, И на команды через какое-то время после деплой-сессии; повторный SSH-заход **незаметно оживляет** его (логин заново поднимает демон) — это маскирует причину. Диагностика: `loginctl show-user <user> | grep Linger`; сразу после твоего логина `docker ps` показывает контейнер `Up Less than a second`. Фикс разово: `loginctl enable-linger <user>` (без root, обратимо `disable-linger`) → демон+контейнер стартуют при загрузке и переживают логаут. **Это НЕ от правок кода/конфига** — латентная дыра, которую вскрывает первый логаут. Для `docker`-команд по не-login SSH экспортируй `DOCKER_HOST=unix:///run/user/1001/docker.sock` + `XDG_RUNTIME_DIR=/run/user/1001`.
- **Rootless: `instances/*/data/` принадлежит sub-UID, а не тебе** (issue #12). Под rootless Docker внутренний `HERMES_UID` (по умолч. 10001) мапится в host sub-UID (`/etc/subuid`, напр. `1001` → `~166535`), поэтому с хоста файлы `data/` **не редактируются** (`Permission denied`) — это норма rootless, не баг. Смотреть/править их — через контейнер: `docker run --rm -v "$PWD/instances/gpio/data":/d busybox ls -la /d`. `.env`/секреты правь **до** старта (они гейтятся отдельно и лежат рядом, не под sub-UID). НЕ пытайся «чинить» это через `HERMES_UID=$(id -u)` — под rootless это делает только хуже.
- **`config.yaml` теперь монтируется rw** (чтобы `/verbose` и прочие рантайм-тумблеры дисплея сохранялись; секретов в нём нет). Под rootless агент (`hermes`, uid 10001) сможет писать его, только если файл принадлежит sub-UID. Поэтому при деплое **chown'ь и `config.yaml`** вместе с `data/`: перед `render.py` — в `0:0` (чтобы хост перезаписал), после — в `10001:10001` (чтобы агент писал):
  ```bash
  docker run --rm -v "$PWD/instances/gpio/data":/d -v "$PWD/instances/gpio/config.yaml":/c \
    --entrypoint chown hermes-multiagent:latest -R 0:0 /d /c        # перед render.py
  # ... python render.py ...
  docker run --rm -v "$PWD/instances/gpio/data":/d -v "$PWD/instances/gpio/config.yaml":/c \
    --entrypoint chown hermes-multiagent:latest -R 10001:10001 /d /c # перед up
  ```
  Re-render перегенерит `config.yaml` → рантайм-правки агента сбросятся к дефолту (это ок, тумблер разовый).
- **ДВА разных «админа» в Telegram — не путать.** (1) `TELEGRAM_ADMIN_USERS` (env, плагин `telegram-context`) — кто может enroll'ить чаты командами `/hermes_*`. (2) `platforms.telegram.{allow_admin_from, group_allow_admin_from}` (config, ядро `gateway/slash_access.py`) — кто может запускать **слэш-команды** ядра. Без второго список **пуст → гейтинг ВЫКЛЮЧЕН** (backward-compat «open»): `/whoami` показывает `Tier: unrestricted` и **любой участник группы может запустить любую слэш-команду**. Всегда задавай оператора для обоих скоупов (DM `allow_admin_from` + группа `group_allow_admin_from`), иначе в группах дырка. Не-админ тогда получает только пол `/help,/whoami`; расширяется через `group_user_allowed_commands` / `user_allowed_commands`. Ключи мостятся в `extra` штатно (`gateway/config.py`), кладём прямо под `platforms.telegram`.
- **`/hermes_*` в группе с `require_mention: true` — только с суффиксом `@botname`.** Команды `/hermes_*` обрабатываются в hook'е `pre_gateway_dispatch` (срабатывает ДО auth), но hook видит сообщение, только если оно вообще дошло до диспетчера. При `require_mention: true` голый `/hermes_here` в группе — «неупомянутое» сообщение и **отбрасывается адаптером до hook'а** (голый `bot_command` без `@botname` не считается обращением к боту — adapter.py). Форма `/hermes_here@gpio_engineer_bot` — это `bot_command` с суффиксом, проходит гейт. Признак симптома: в ингест-сторе (`data/telegram.db`, таблица `messages`) **нет ни одного группового сообщения**, только DM.
- **Меню команд в группах чистит плагин, не адаптер.** Штатного конфига «отключить `/`-меню в группе» нет; адаптер (upstream) регистрирует полное меню сразу в 3 скоупа (`Default`+`AllPrivateChats`+`AllGroupChats`, adapter.py) и **не скоупит по правам** (`ChatAdministrators`/`ChatMember` не используются — меню одинаковое всем). Чтобы не трогать upstream, `telegram-context` сам чистит скоуп `AllGroupChats` через прямой Bot API `setMyCommands(commands=[], scope=all_group_chats)` — троттлинг раз/10 мин, best-effort. При реконнекте адаптер может ненадолго вернуть полное меню → плагин обнулит при следующем сообщении. Безопасность держится на локе слэш-tier (см. выше), меню — косметика.
- **Отключение скилла = уходит и его `/`-команда.** Bundled-скиллы, включённые для платформы, автоматически становятся командами в меню Telegram (`telegram_menu_commands` → `_collect_gateway_skill_entries`). Убрал скилл из `agents.yaml` (`skills:`) → он исчезает и из меню. Отдельно команду гасить не надо.

## Откат / пересборка
- Пересобрать инстанс с нуля: останови compose, удали `instances/gpio/data/`
  (стейт/память/копии скиллов) и `render/`, затем снова `python render.py`.
  ⚠️ **ЭТО ПОЛНЫЙ ВАЙП, НЕ рутинный шаг** — сотрёт и накопленные агентские скиллы (см. ниже).
- Секреты (`.env`, `secrets/`, `data/`) — gitignored, `git pull` их не трогает.

### ⚠️ `data/skills` — накопленные агентские скиллы, НЕ удалять
`instances/gpio/data/skills/` (= `/opt/data/skills`, writable, gitignored) — агент через
`skill_manage` копит там **свои** скиллы, которых нет в репе (напр. `arxiv`,
`neighbour-chat-story`, `systematic-debugging`). Это важные данные, терять нельзя.
- **`render.py` их сохраняет:** `seed_skills` сеет base-скиллы **copy-IF-ABSENT**
  (`_seed`: `if dst.exists(): return`) — существующие не трогает, всю папку не rmtree'ит.
  Значит рутинный деплой (chown → `render.py` → restart/`up`) для накопленных скиллов
  БЕЗОПАСЕН.
- **НИКОГДА** не делай `rm -rf …/data/skills` целиком и не удаляй `instances/gpio/data/`
  под ноль в рутинном деплое — это единственное, что стирает агентские скиллы.
- **Обновить base-скилл** (copy-if-absent не подтянет правку в уже сидённый скилл):
  удаляй ТОЧЕЧНО только его подпапку → `rm -rf instances/gpio/data/skills/<имя>` →
  `python render.py` пере-сеет только его; накопленное остаётся нетронутым.

Когда закончишь — короткий отчёт: какие образы собрал, что с гейтом моделей,
что показали логи, результат чек-листа из шага 4.
