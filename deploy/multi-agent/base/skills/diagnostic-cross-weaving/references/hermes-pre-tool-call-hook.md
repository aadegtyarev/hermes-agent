# Hermes pre_tool_call Hook — механическое принуждение

## Проблема

Я систематически пропускаю шаг загрузки скиллов перед началом работы, потому что задача выглядит знакомой. «Буду стараться» не работает.

## Решение: pre_tool_call plugin hook

Регистрируется в плагине, запускается **до** выполнения любого tool call.
Может вернуть `{"action": "block", "message": "..."}` — вызов блокируется.

## Структура плагина

```
~/.hermes/plugins/skill-enforcer/
├── plugin.yaml      # manifest
└── __init__.py      # register() + pre_tool_call callback
```

### plugin.yaml

```yaml
name: skill-enforcer
version: 1.0.0
description: Blocks first work tool call until skills are loaded
provides_hooks:
  - pre_tool_call
```

### __init__.py

```python
"""Blocks ssh_run/web_search/read_file until skill_view was called this turn."""

import json

# Tools that count as "work" — blocked until skills loaded
WORK_TOOLS = {
    "ssh_run", "web_search", "web_extract", "read_file",
    "search_files", "write_file", "patch", "execute_code",
    "delegate_task", "channel_post", "channel_edit_text",
    "telegram", "image_generate",
}

# Tools that ARE the skill-loading mechanism — never block
ALLOWED_TOOLS = {"skill_view", "skills_list", "skill_manage", "clarify", "todo"}

_skills_loaded_this_turn = False

def on_pre_tool_call(tool_name: str, params: dict, **kwargs):
    global _skills_loaded_this_turn
    
    # Track skill loading
    if tool_name in ("skill_view", "skills_list"):
        _skills_loaded_this_turn = True
        return  # allow
    
    # If skills already loaded or tool is not "work" — allow
    if _skills_loaded_this_turn or tool_name in ALLOWED_TOOLS:
        return
    
    # First work tool call and skills NOT loaded → block
    if tool_name in WORK_TOOLS:
        return {
            "action": "block",
            "message": (
                "⛔ Стоп. Ты делаешь первый рабочий tool call, "
                "но не загрузил скиллы. "
                "Остановись и проверь список доступных скиллов в system prompt. "
                "Если подходящий есть — загрузи через skill_view(). "
                "Потом повтори этот вызов."
            ),
        }

def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
```

## Нюансы

- `on_pre_tool_call` получает `tool_name` (строка) и `params` (dict)
- Может вернуть `None` (пропустить) или `dict` с `action: "block"` и `message`
- `_skills_loaded_this_turn` надо сбрасывать при `/new` или новой сессии — можно через `agent:start` hook
- Для gateway-сессий (Telegram) тоже работает: плагины живут в CLI + Gateway
- Плагины надо включить в `plugins.enabled` в `~/.hermes/config.yaml`

## Альтернативы

- **Shell hook** — bash-скрипт в `config.yaml`, слабее (не знает контекст: tool_name)
- **Gateway event hook** — `HOOK.yaml` + `handler.py` в `~/.hermes/hooks/` — работает только в gateway, не в CLI
