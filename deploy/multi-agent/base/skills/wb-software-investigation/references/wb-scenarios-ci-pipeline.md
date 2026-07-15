# wb-scenarios CI Pipeline (Jenkins)

Jenkinsfile: `buildDebArchAll defaultRunLintian: true`

## Checks (in order)

| Check | Typical Failure | Likely Root Cause |
|---|---|---|
| Build package | Rarely | Actual build error |
| Python checks | Rarely | Syntax/test issue |
| Check version has bumped | **Common** | Missing bump in `debian/changelog` |
| Lintian | "Failed to build stage" | ⚠ Часто **каскад** — падает потому что упал version check, а не из-за реальных lintian-ошибок. Сначала чинить version bump |
| Setup deploy | "Failed to build stage" | Тот же каскад от version check |
| PR merge | ERROR | Блокировано выше |

## Common Fixes

### 1. Version bump (`debian/changelog`)
Добавить новую запись НАД текущей:
```
wb-scenarios (1.9.8) stable; urgency=medium

  * Describe your changes here

 -- Author Name <email@wirenboard.com>  Mon, 07 Jul 2026 18:00:00 +0300
```

### 2. ESLint
Flat config (`eslint.config.cjs`). Ключевое: `func-names: error` (только именованные функции), `indent: ['error', 2]`, `prettier/prettier: error`.

### 3. JSON Schema
- `options.dependencies` — **UI-level** (видимость полей), не schema-валидация
- `watch`/`watchValue` — механизм динамического UI

## Проверка CI-статуса через GitHub API
```bash
gh api /repos/wirenboard/wb-scenarios/commits/<SHA>/status --jq '.statuses[] | {context, state, description}'
gh pr view <PR#> --json statusCheckRollup -q '.statusCheckRollup[] | {context, state, description}'
```
