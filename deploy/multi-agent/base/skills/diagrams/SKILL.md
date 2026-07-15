---
name: diagrams
description: Рисовать блок-схемы, флоучарты и небольшие электрические принципиалки КОДОМ (graphviz / schemdraw / matplotlib) и отдавать картинкой в чат. Использовать, когда просят «нарисуй схему / диаграмму / блок-схему / принципиалку».
---

# Диаграммы и схемы — кодом, не картинко-генератором

**НЕ используй `image_gen` для схем и диаграмм.** Это растровая диффузия: она рисует «похоже на схему» — размытые УГО, нечитаемые номиналы, не редактируется. Годится для иллюстраций/мемов, не для инженерии. Схемы рисуй **кодом** через `code_execution`.

## Что чем
- **Блок-схемы / флоучарты / топология сети/MQTT** → `graphviz` (Python-обёртка; бинарь `dot` в образе есть).
- **Электрические принципиалки** (небольшие: делители, подтяжки, обвязка ИС, RS-485) → `schemdraw` (УГО поверх matplotlib).
- **Графики/таблицы данных** (телеметрия, логи) → `matplotlib`.

## Доставка в чат
Сохрани картинку в **абсолютный** путь (напр. `/tmp/x.png`) и в своём ответе поставь строку:

```
MEDIA:/tmp/x.png
```

Гейтвей вырежет её и пришлёт файл в чат. PNG — превьюшкой в Telegram; SVG (редактируемый) — как документ (`MEDIA:/tmp/x.svg`), но Telegram его не превьюит, так что для просмотра всегда давай PNG, SVG — по запросу «нужен исходник».

## Примеры

**Блок-схема (graphviz):**
```python
from graphviz import Digraph
g = Digraph(format="png"); g.attr(rankdir="LR")
g.node("wb", "WB controller"); g.node("mqtt", "MQTT broker"); g.node("ha", "Home Assistant")
g.edge("wb", "mqtt"); g.edge("mqtt", "ha")
g.render("/tmp/topo", cleanup=True)   # -> /tmp/topo.png
```
затем в ответе: `MEDIA:/tmp/topo.png`

**Электрическая принципиалка (schemdraw):**
```python
import schemdraw, schemdraw.elements as e
d = schemdraw.Drawing()
d += e.SourceV().up().label("24V")
d += e.Resistor().right().label("R1 10k")
d += e.Capacitor().down().label("C1 100n")
d += e.Line().left()
d.save("/tmp/schem.png")              # PNG для превью
# d.save("/tmp/schem.svg")            # SVG — если попросят исходник
```
затем в ответе: `MEDIA:/tmp/schem.png`

**График (matplotlib):** обычный `savefig("/tmp/plot.png", dpi=120)`, затем `MEDIA:/tmp/plot.png`.

## Правила
- Держи схему **небольшой и читаемой** — если узлов десятки, дай список/таблицу текстом, а картинкой только ключевой фрагмент.
- Подписи — латиницей/цифрами (кириллица в schemdraw/matplotlib может не отрисоваться без шрифта); в graphviz-нодах кириллица ок.
- Не выдумывай номиналы/пины — бери из datasheet (`http_fetch`) или конфига, иначе рисуй с явной пометкой «пример».
