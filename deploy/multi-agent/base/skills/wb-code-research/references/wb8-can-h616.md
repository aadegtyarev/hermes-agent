# WB8 CAN — H616 не имеет встроенного CAN-контроллера

## Факт

**Allwinner H616 SoC не имеет CAN-контроллера.** Ни sun4i-can, ни sun8i-can, ни M_CAN.

## Как проверить

### Device Tree SoC-уровня

В `sun50i-h616.dtsi` (upstream Linux v6.8) нет ни одного `can`-узла. В отличие от A10/A20/R40/D1 — у H616 CAN отсутствует:

```
curl -sL https://raw.githubusercontent.com/torvalds/linux/v6.8/arch/arm64/boot/dts/allwinner/sun50i-h616.dtsi | grep -i can
→ (пусто)
```

### Allwinner CAN driver support

`sun4i_can.c` (драйвер CAN на Allwinner) поддерживает:
- allwinner,sun4i-a10-can (A10)
- allwinner,sun7i-a20-can (A20)
- allwinner,sun8i-r40-can (R40)
- allwinner,sun20i-d1-can (D1)

H616/H618/H313 отсутствуют.

### Ядро на WB8

```bash
# Доступные CAN-модули — все для внешних трансиверов
ls /lib/modules/6.8.0-wb159/kernel/drivers/net/can/
# m_can, flexcan, mcp251xfd, gs_usb, slcan, ht42b416

# Ни один не привязан к платформе WB8:
cat /proc/iomem | grep -i can
→ (пусто)
```

### Device Tree WB8

В `/sys/firmware/devicetree/base/soc/` нет `can`-узлов. Только:
- i2c, mmc, pinctrl, pwm, rtc, spi, usb

### Что означают CAN-строки в DT

В DTB есть `gpio-line-names`:
```
"CAN TXRX ON"  — питание CAN-трансивера (GPIO)
"CAN RX"       — RX от трансивера (GPIO)
"CAN TX"       — TX к трансиверу (GPIO)
```

Это **GPIO-пины**, не CAN-контроллер. CAN на WB8 реализован через GPIO + внешний трансивер (вероятно, software bit-bang или UART в LIN-режиме), не аппаратный CAN-модуль SoC.

## Ключевой урок

При диагностике CAN на WB8 — не ищи CAN-контроллер в SoC. H616 его не имеет. CAN на WB8 — board-level решение через внешний трансивер + GPIO. Для диагностики смотри аппаратную документацию WB8, а не спецификацию H616.
