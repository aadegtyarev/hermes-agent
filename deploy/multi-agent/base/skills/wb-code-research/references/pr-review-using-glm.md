# PR Review via GLM-5.2 (z.ai API)

Use when: нужно ревьювить PR в Wiren Board (или любой другой) репозиторий через GLM, а `claude-glm` CLI не работает (exit code 1, model_not_found).

## Почему claude-glm не годится

- `claude-glm` — Claude Code CLI с прокси на z.ai
- Ожидает интерактивную PTY-сессию
- Может не резолвить `glm-5.2` как имя модели (хотя модель жива на API)
- При пайпе diff через stdin + `-p` — exit code 1, пустой вывод
- Добавление `--model glm-5.2` → `model_not_found` (валидация на уровне Claude Code, не API)

## Рабочий вариант: прямой вызов API

```bash
diff=$(cat /tmp/diff.txt)
prompt="Review the following diff concisely.
What looks good? What's risky? What's questionable?
Do NOT leave GitHub comments. Return a brief structured report.

DIFF:
$diff"

payload=$(python3 -c "
import json
p = json.dumps({'role':'user','content':'''$prompt'''})
d = {'model':'glm-5.2','messages':[json.loads(p)],'max_tokens':4096}
print(json.dumps(d))
")
curl -s "https://api.z.ai/api/anthropic/v1/messages" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $(cat ~/.config/glm/token)" \
  -H "anthropic-version: 2023-06-01" \
  -d "$payload" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if 'content' in data:
    for c in data['content']:
        if c.get('type') == 'text':
            print(c['text'])
else:
    print(json.dumps(data, indent=2))
"
```

## System prompt (при необходимости)

Если нужен system prompt — добавлять `{'role':'system','content':'...'}` до user-сообщения:

```python
s = json.dumps({'role':'system','content':'Ты — senior code reviewer. Будь краток, техничен.'})
d = {'model':'glm-5.2','messages':[json.loads(s),json.loads(p)],'max_tokens':4096}
```

## Формат отчёта (проверено на этом проекте)

GLM-5.2 стабильно выдаёт трёхсекционный отчёт:
- **What looks good** — архитектурные плюсы, правильные решения
- **What's risky** — потенциальные баги, необработанные edge cases
- **What's questionable** — стиль, мёртвый код, хардкод, дублирование

Лимиты z.ai: ~5–6 запросов подряд → 429 rate limit на ~1 час. Для batch-ревью планировать паузы.
