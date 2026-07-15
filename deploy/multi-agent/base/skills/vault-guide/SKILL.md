---
name: vault-guide
description: Как доставать секреты из Bitwarden через vault_* тулы — найти элемент, получить пароль/поле/TOTP. Не хранить секреты в открытую.
version: 1.0.0
author: multi-agent
platforms: [linux]
metadata:
  hermes:
    tags: [bitwarden, vault, secrets, passwords, totp]
    category: security
---

# Секреты из Bitwarden (плагин `vault`)

Пароли и токены не лежат у тебя в открытую — доставай их **из хранилища по
необходимости** через `vault_*`. Каждый доступ логируется. Ключи разблокировки
хранилища тебе недоступны (это env, вычищается из песочницы) — только эти тулы.

## Тулы
- `vault_list(search=…)` — найти элемент: имена + id (+ username). Начни отсюда,
  если не знаешь точного имени.
- `vault_get(name)` — логин элемента: username, password, uris.
- `vault_field(name, field)` — одно поле: `password`/`username`/`uri`/`notes`/
  `totp` или имя кастомного поля.
- `vault_totp(name)` — текущий TOTP-код (2FA).

`name` — подстрока имени (регистронезависимо) или id.

## Паттерн
1. Не знаешь точное имя → `vault_list(search="роутер")`.
2. Нужен пароль для входа → `vault_get(name)` или `vault_field(name, "password")`.
3. 2FA → `vault_totp(name)`.
4. Полученный секрет используй сразу (напр. как `password` в `ssh_run`/`ssh_copy_id`),
   не пересказывай и не логируй его в чат без надобности.

## Ошибки
Если хранилище не настроено или элемент не найден — тул скажет прямо, что
проверить (`vault_list` для точного имени; BW_* креды в .env). Не пытайся
обойти через code_execution — доступа к vault-кредам там нет.
