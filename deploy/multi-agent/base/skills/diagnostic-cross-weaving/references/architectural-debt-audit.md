# Architectural Debt Audit — probe patterns

Систематическая проверка кодовой базы на «где свернули не туда».
Не верификация spec → code (см. раздел code-probe в `diagnostic-cross-weaving`), а **детекция классов архитектурного долга**:

- facade — есть/работает, но никто/ничто не потребляет
- dead signal — событие/метод/флаг есть, subscriber/consumer отсутствует
- stamped confidence — hardcoded literal там, где обещано derivation
- scope mismatch — имя шире, чем реально делает
- observability hole — орган не оставляет следов жизни
- generic fallback — последняя resort не вырезана

## Data collection probes

Для каждого класса — свой grep:

### 1. Facade detection

```
grep -rn '"event_name"' --include='*.py' . | grep -v test | grep -v __pycache__
# Пример: creative_output_generated — publisher в creativity_engine.py, subscriber — только tests/test_creativity.py → facade
```

**Критерий:** событие publicуется → ни один production-код не подписан → facade.

### 2. Dead signal / unused path

```
grep -rn 'Throttle\b\|throttle_state\|set_throttled' --include='*.py' core/ | head -20
# Если 0 — код упоминает, но реализации нет
```

**Критерий:** в коде есть импорт/упоминание, но grep по реализации даёт 0 строк.

### 3. Stamped confidence (C1 violations)

```
grep -n 'confidence=0\.[0-9]' --include='*.py' organs/sleep_engine.py | grep -v 'confidence_model\|derive\|NEUTRAL\|CONF_FLOOR'
# Каждый результат — нарушение C1: "confidence is DERIVED from grounding, never stamped"
```

**Критерий:** `confidence=0.6` или `confidence=0.7` без вызова `confidence_model.derive()`.
Проверить контекст — может быть conversation summary (отдельный path, забыли перевести на derive).

### 4. Scope mismatch

```
# Что обещает имя класса/модуля vs что делает
grep -n 'class\|def \|"""' organs/emergent_observer.py | head -20
# Если docstring говорит "NOT a general X detector" — имя шире сути
```

**Критерий:** имя `EmergenceObserver`, docstring: «NOT a general emergence detector — watches one pattern» → mismatch.

### 5. Observability hole

```
# Как орган оставляет след — event_log, bus event, metric
grep -n 'log_event\|event_log\|bus.publish\|metric' organs/<organ>.py | head -10
# Если 0 или только bus.publish без consumer — observability hole
```

**Критерий:** орган работает, но не пишет в event_log и не оставляет читаемого следа.

### 6. Generic fallback

```
# Generic question / catch-all / default query, который срабатывает когда grounded пуст
grep -n 'GENERIC\|fallback\|default_question\|INVITATION\|generic' organs/curiosity_engine.py | head -15
```

**Критерий:** есть `_GENERIC_QUESTIONS` или `fallback_question`, который является последней resort и не был вырезан.

## Output format

Для каждого класса — отдельный блок:

```
### 🔴 audit-N: <название>
<1-2 строки что нашли>
- факт 1: [source: file:line]
- факт 2: [source: file:line]
→ **Проблема:** одна фраза
```

Итоговая таблица:

| Аудит | Вердикт | Проблема |
|-------|---------|----------|
| Emergence Observer | 🟡 | Имя шире сути в 10 раз |
| Creativity engine | 🔴 | Пишет, никто не читает (dead event) |
| Curiosity engine | 🟡 | Generic fallback не добит |
| Confidence C1 | 🔴 | 2 hardcoded stampa остались |
| Observability | 🔴 | Organs не логируют |
| Facades | 🔴 | Throttle мёртв, creativity-event dead |

## Когда применять

- Пользователь спрашивает «где мы облажались», «аудит кода», «проверь что не так»
- После feature freeze — systematic check перед релизом
- При код-ревью новой архитектуры — baseline существующих проблем

## Связь с другими методиками

- `diagnostic-cross-weaving` (раздел code-probe) — проверка spec → code для конкретного бага, не архитектурного аудита
- 4-типовая таблица (BUILT/DESIGN/SURPRISE/ERASURE) из code-probe раздела cross-weaving — для позлементной трассировки данных, а не архитектурного долга
