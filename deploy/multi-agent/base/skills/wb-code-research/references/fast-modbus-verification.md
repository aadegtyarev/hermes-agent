# Fast Modbus: проверка утверждений через код

**Дата:** 26 июня 2026  
**Версия master:** v2.257.2  
**Stable (wb-2606):** 2.248.1-wb101  
**Исходный вопрос:** «Можно ли отключить Fast Modbus на регистре/устройстве, и продолжает ли мастер слать broadcast?»

## Вопрос 1: можно ли отключить sporadic на регистре?

**Да.** `sporadic: false` (SporadicMode = DISABLED) — регистр не добавляется в EventsReader.

**Код:** `SetDevices()` в serial_client_events_reader.cpp (строка ~208):
```
Regs[slaveId] += reg; // только если reg.SporadicMode != DISABLED
```

Все регистры с `sporadic: false` → `Regs` для slaveId пуст.

## Вопрос 2: шлёт ли мастер broadcast, если отключено всё?

**Нет.** `HasDevicesWithEnabledEvents()` возвращает false → `ReadEvents()` не вызывается в OpenPortCycle.

**Код:** `serial_client_events_reader.h` (строка ~40):
```cpp
bool HasDevicesWithEnabledEvents() const { return !DevicesWithEnabledEvents.empty(); }
```

`serial_client.cpp` (строка ~428):
```cpp
if (HasDevicesWithEnabledEvents()) {
    ReadEvents(...); // broadcast
}
```

Период 50/100/200ms (`GetReadEventsPeriod()`) — потенциальный, а не активный. Вычисляется от скорости порта, но не используется, если нет устройств с событиями.

## Вопрос 3: глобальное отключение на порту/мастере?

**Нет.** В схеме конфига порта (`serial_config.cpp`) нет поля `fast_modbus_enabled` или аналога. Feature request #38548 на support.wirenboard.com — открыт, не реализован.

**Эквивалент:** `sporadic: false` на ВСЕХ регистрах ВСЕХ устройств порта → `EnableEvents()` не отправляет `ENABLE_EVENTS_COMMAND (0x18)` на старте → broadcast не запускается.

## Нюанс: лог «Skip enabling events for modbus:N»

Это про Modbus TCP gateway, не про `sporadic`. Когда шлюз не поддерживает Fast Modbus, `EnableEvents()` логирует skip и завершается. К sporadic-настройкам регистров отношения не имеет.

## Изменения между stable (2.248.1-wb101) и master (2.257.2)

Логика Fast Modbus (`EnableEvents`, `ReadEvents`, `HasDevicesWithEnabledEvents`) **не менялась**. Между этими версиями только:
- Новые шаблоны устройств (Carel µC2SE, WB-LED fw3, WB-MDM3)
- Debian 13 port
- Фикс race condition gethostbyname
- Фикс невалидного JSON в config-wb-ups-v3.jinja
- Фикс publish для устройств с only-sporadic каналами (2.242.6 — уже в stable)

## Исправление ошибки

Первоначальный ответ содержал неточность по п.2: «всё равно будет слать broadcast, если есть хоть одно устройство с Fast Modbus на порту». Правильно: **не будет слать**, если у этого единственного устройства все регистры `sporadic: false`.
