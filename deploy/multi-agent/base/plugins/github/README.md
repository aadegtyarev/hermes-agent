# GitHub Plugin

Плагин для Hermes Agent — управление GitHub-репозиториями через агента.

## Возможности

| Инструмент | Описание |
|------------|----------|
| `github_issue_list` | Список issue с фильтрацией по статусу, меткам, исполнителю, поиску |
| `github_issue_view` | Просмотр issue с комментариями |
| `github_issue_create` | Создание нового issue |
| `github_pr_list` | Список PR с фильтрацией по статусу, автору, меткам |
| `github_pr_view` | Детальный просмотр PR: дифф, ревью, статус CI, возможность мёрджа |
| `github_pr_merge` | Мёрдж PR (merge/squash/rebase) с опциональным удалением ветки |
| `github_repo_search` | Поиск по коду, issue и PR в репозитории |

## Установка

### Требования

1. **`gh` CLI** — [установка](https://cli.github.com), залогинен (`gh auth login`)
2. **`GITHUB_TOKEN`** — опционально, для работы через REST API (если `gh` недоступен). Создать: https://github.com/settings/tokens (нужен scope `repo`)
3. **`GITHUB_ALLOWED_REPOS`** — опционально, список разрешённых репозиториев через запятую (`aadegtyarev/hermes-plugins,owner/repo2`). Если не задан — доступны все репозитории.

### Установка плагина в Hermes

```bash
# Скопируй плагин в директорию Hermes
cp -r plugins/github ~/.hermes/plugins/github

# Или через symlink (если репо лежит рядом)
ln -s "$(pwd)/plugins/github" ~/.hermes/plugins/github

# Включи плагин
hermes plugins enable github

# Перезапусти Hermes
```

## Контракт

Плагин использует `gh` CLI как основной бэкенд. Все вызовы идут через `subprocess` к `gh` с флагом `--json` для структурированного вывода.

**Альтернативный бэкенд:** если `GITHUB_TOKEN` задан в `.env`, а `gh` недоступен — тулы `issue_list`, `issue_view`, `issue_create` переключаются на REST API (через stdlib `urllib`).

**Безопасность:**
- `pr_merge` требует явного подтверждения пользователя (через approval-систему Hermes)
- `GITHUB_ALLOWED_REPOS` — allowlist репозиториев: если задан, тулы работают только с перечисленными репо
- Каждая тула в описании (`schemas.py`) отмечает: для приватных/allowlisted репо нужен `gh` auth или `GITHUB_TOKEN`; если тула не сработала и репо публичный, годится fallback через `curl`/`http_fetch` — это не жёсткий блок, а совет модели
- `pre_tool_call` хук (`_log_terminal_github_use`) только логирует прямые обращения к GitHub через `terminal` (gh, git clone github, curl api.github.com) для аудита — не блокирует: если github_* недоступны (например, `gh` не залогинен), agent должен иметь возможность дойти до данных другим путём
- Все вызовы логируются через хук `post_tool_call`
- Токен берётся из env, не хранится в коде

## Примеры использования

Агент понимает естественный язык:

> «Посмотри открытые issues в aadegtyarev/hermes-plugins с меткой bug»

> «Покажи PR #3, прошли ли там тесты?»

> «Создай issue: 'Добавить плагин для YouTrack' в aadegtyarev/hermes-plugins»

> «Что ищет PR #5 в коде? Можно ли его вливать?»
