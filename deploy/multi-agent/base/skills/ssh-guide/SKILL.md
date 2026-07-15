---
name: ssh-guide
description: Как работать с хостами по SSH через ssh_* тулы — разовые команды, длинные/фоновые сессии, генерация и установка ключей, вход по паролю или ключу.
version: 1.0.0
author: multi-agent
platforms: [linux]
metadata:
  hermes:
    tags: [ssh, remote, ops, sessions, keys]
    category: devops
---

# Работа по SSH (плагин `ssh`)

Terminal-тула у тебя нет — на удалённые хосты ходи **только через `ssh_*` тулы**
(не через code_execution). По умолчанию доступен **любой хост**; ограничение
включается только если задан `SSH_ALLOWED_HOSTS` (список или `*`). Проверка
host-key отключена намеренно (перепрошитые устройства меняют ключ), так что
после сброса контроллера подключение не ломается.

## Разовые команды
- `ssh_run(host, command, timeout=15)` — выполнить и получить `exit_code/stdout/stderr`.
- `ssh_read_file(host, path)` — прочитать файл (cat).
- `ssh_list(host, path)` — `ls -la` (файлы, размеры, симлинки).

`host` — `10.0.0.5` или `user@10.0.0.5`. Пароль: аргумент `password` или env
`SSH_PASSWORD`; иначе вход по ключу.

## Длинные / фоновые сессии
Для того, что «висит» (тайл логов, сборка, демон в foreground):
1. `ssh_start(host, command)` → вернёт `session_id`, команда работает в фоне.
2. `ssh_poll(session_id)` → новые строки вывода + `running`/`exit_code`. Зови периодически.
3. `ssh_send(session_id, data)` → отправить строку в stdin (для интерактивных).
4. `ssh_stop(session_id)` → убить. `ssh_sessions()` → список активных.

Сессии живут в процессе gateway (переживают между тул-вызовами, но не рестарт gateway).

## Ключи: сгенерировать и установить
1. `ssh_keygen()` → создаст `~/.ssh/id_ed25519` (writable том) и вернёт **public key**.
2. `ssh_copy_id(host, password=…)` → допишет твой public key в `~/.ssh/authorized_keys`
   хоста (первый вход — по паролю). Дальше ходи по ключу без пароля.

Оставь `SSH_KEY` пустым — тогда генерация, установка и вход используют один и тот
же `~/.ssh/id_ed25519`. Если ключ примонтирован снаружи — укажи путь в `SSH_KEY`.

## Ошибки
Тулы возвращают понятные ошибки с примером вызова. Если хост не в allowlist —
это политика, не пытайся обойти через code_execution. «No active session» →
`ssh_sessions()` покажет живые id.
