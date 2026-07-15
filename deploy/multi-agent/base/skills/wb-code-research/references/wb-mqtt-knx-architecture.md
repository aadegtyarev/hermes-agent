# wb-mqtt-knx: архитектура

Проверено 26.06.2026 по 4 реальным кейсам support.wirenboard.com + README на GitHub.

## Компоненты

- **knxd** — демон KNX-сети (FT1.2 через UART, либо IP-туннель). Отвечает за физический слой: отправку/приём телеграмм с KNX-шины.
- **wb-mqtt-knx** — C++ bridge, форвардит телеграммы между knxd и MQTT.

## Два режима работы wb-mqtt-knx

### 1. Legacy-режим (`enableLegacyKnxDevice: true`)
- Создаёт устройство `/devices/knx/` с контролом `data`
- **Отправка команды:** `mosquitto_pub -t '/devices/knx/controls/data/on' -m "g:1/1/65 GroupValueWrite 0x01"`
- **Приём с шины:** подписка на `/devices/knx/controls/data` — приходит `i:1/1/22 g:1/1/65 GroupValueWrite 0x01`
- **Важно:** отправленные bridge команды НЕ форвардятся обратно в MQTT-топик `data`. Они видны только в serial-логе knxd. Это норма, не баг. Подтверждено кейсом #31307.

### 2. Режим групповых объектов (Group Objects) — дефолтный
- В `/etc/wb-mqtt-knx.conf` описываются `devices[]` с `controls[]`, каждый с `groupAddress` + опционально `feedbackGroupAddress`
- Bridge сам создаёт `/devices/{deviceId}/controls/{controlId}` в MQTT
- При записи в `/devices/{deviceId}/controls/{controlId}/on` bridge отправляет GroupValueWrite на групповой адрес
- При приходе телеграммы с шины bridge обновляет значение в контроле
- `feedbackGroupAddress` — если status-обратная связь идёт на другой групповой адрес
- `readPollInterval` — bridge сам отправляет GroupValueRead и ждёт ответ

## KNX DPT (Data Point Types)
- Определяют как значение упаковывается в байты
- Для каждого DPT bridge создаёт MQTT-контрол с соответствующим типом (`switch`, `value`, `text`)
- Описаны в `datapointformat.md` в репозитории

## Типичные проблемы (из кейсов)

| Симптом | Причина | Решение |
|---------|---------|---------|
| Команды в MQTT есть, на шину не уходят | Баг прошивки bridge | Обновление до testing-ветки (#31307) |
| `router: setup router: failed` в логах knxd | Конфликт физических адресов — пул туннельных клиентов накладывается на адреса устройств на шине | Сместить пул в свободный диапазон (`1.1.200..1.1.220`) (#35917) |
| knxd не стартует после импорта из ETS | Импорт сформировал нестандартный конфиг (вложенные устройства) | Вернуть дефолтный конфиг, повторить импорт (#36162) |
| Адреса сбрасываются после перезагрузки | WB6, старые версии | Factory reset (#7627) |
| Команды видны в `data/on`, устройство не реагирует | Неверный DPT, неверный групповой адрес, проблема совместимости | Проверить ETS-конфигурацию устройства |

## Диагностика

1. `systemctl status knxd` — жив ли knxd?
2. `journalctl -u knxd -n 50 --no-pager` — ошибки knxd
3. `systemctl status wb-mqtt-knx` — жив ли bridge?
4. `journalctl -u wb-mqtt-knx -n 50 --no-pager` — ошибки bridge
5. `mosquitto_sub -t '/devices/knx/#' -v` — что публикуется в MQTT (legacy)
6. `mosquitto_sub -t '/devices/*/controls/#' -v` — если Group Objects
7. `wb-knx-ets-tool` — конвертация ETS XML → конфиг bridge
8. `/etc/wb-mqtt-knx.conf` — проверить deviceId, controlId, groupAddress, DPT

## Ссылки

- README: https://github.com/wirenboard/wb-mqtt-knx/blob/master/README.md
- Конфиг: https://github.com/wirenboard/wb-mqtt-knx/blob/master/wb-mqtt-knx.conf
- DPT: https://github.com/wirenboard/wb-mqtt-knx/blob/master/datapointformat.md
- JSON DPT: https://github.com/wirenboard/wb-mqtt-knx/blob/master/jsondatapoint.md
