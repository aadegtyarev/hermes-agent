---
name: archive-guide
description: Как работать с архивами через archive_* тулы — список содержимого, распаковка (zip/rar/7z/tar/…), упаковка. Без терминала.
version: 1.0.0
author: multi-agent
platforms: [linux]
metadata:
  hermes:
    tags: [archive, zip, rar, 7z, tar, extract]
    category: devops
---

# Работа с архивами (плагин `archive`)

Terminal-тула нет — с архивами работай через `archive_*` тулы (под капотом
`unar`/`lsar` + stdlib, покрывают zip, rar, RAR5, 7z, tar, gz, bz2, xz).

## Тулы
- `archive_list(path)` — показать содержимое **без** распаковки. Делай это
  первым, чтобы понять структуру и не распаковывать вслепую.
- `archive_extract(path, dest=…)` — распаковать. `dest` по умолчанию
  `<архив>_extracted`. Вернёт список извлечённых файлов.
- `archive_create(out_path, paths)` — упаковать. Формат по расширению
  `out_path`: `.zip`, `.tar.gz`/`.tgz`, `.tar.bz2`, `.tar.xz`, `.tar`.

## Пути
Все пути — абсолютные, в контейнере. Файлы, присланные в чат, лежат в
`/opt/data/cache/documents/`. Результаты клади в рабочий том (`/opt/data/...`).

## Паттерн
1. `archive_list(path)` — осмотреть.
2. `archive_extract(path)` — распаковать в отдельную папку.
3. Дальше читай файлы обычными file-тулами / `search_files`.
4. Нужно отдать наружу — `archive_create(out, [...])` и пришли файл.

## Когда не сюда
Если формат экзотический или нужна тонкая логика — можно через `code_execution`
(Python: `zipfile`/`tarfile`/`py7zr`/`rarfile` уже в образе). Но для обычных
zip/rar/7z/tar проще и надёжнее `archive_*`.
