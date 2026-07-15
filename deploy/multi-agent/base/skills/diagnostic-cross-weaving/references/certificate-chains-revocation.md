# TLS Certificate Chains & Revocation Mechanics (2026)

## Let's Encrypt Gen-Y Chain (current as of June 2026)

```
EE ← YR2 ← Root YR ← ISRG Root X1
```

- **3 intermediate CA levels** (YR2, Root YR cross-signed by ISRG Root X1)
- nginx default `proxy_ssl_verify_depth = 1` → не может верифицировать эту цепочку
- Фикс: `proxy_ssl_verify_depth 5;` (или хотя бы 4)
- Аналогично для всех reverse proxy (haproxy, envoy, etc.) — проверять дефолтную глубину

### Gen-Y инциденты (май-июнь 2026)

| Дата | Событие |
|------|---------|
| 8 мая | LE остановила выпуск на 2.5ч из-за отсутствия serverAuth EKU на кросс-сертификатах Gen-Y. Откатились на Gen X. |
| 13 мая | Отозвали и перевыпустили кросс-серты X2/YR и YE с правильными EKU. |
| 29 мая | Пользователи увидели revoked Root YR в цепочке — на самом деле ACME клиенты не сохраняли второй промежуточный. |

Источник: [LE Chains of Trust](https://letsencrypt.org/certificates/), [community thread](https://community.letsencrypt.org/t/today-re-issued-certificates-from-issuer-yr2-have-a-revoked-cert-in-chain/247612)

## nginx verification depth

- `proxy_ssl_verify_depth` — по умолчанию **1** (только сертификат сервера + 1 промежуточный)
- Для LE Gen-Y нужно **≥4** (EE + 3 intermediate = 4 уровня)
- `ssl_verify_depth` — аналогично для прямого HTTPS (не proxy)
- `ssl_stapling` — если OCSP отключён (LE отключил OCSP в августе 2025), CRL stapling не так распространён

## Два механизма прекращения действия сертификатов

### 1. Revocation (отзыв) — GlobalSign

- Серийный номер вносится в **CRL**, браузеры проверяют при каждом соединении
- Сертификат перестаёт работать ДО истечения срока
- GlobalSign начал отзывать уже выданные сертификаты российским подсанкционным компаниям с 13 июня 2026
- Письмо гендиректора GMO GlobalSign Russia Дмитрия Рыжикова партнёрам (цит. по [РБК 13.06](https://www.rbc.ru/technology_and_media/13/06/2026/6a2d12da9a7947f7d5334aa0))
- Вторая волна отзыва — 18 июня 2026 ([РБК 18.06](https://www.rbc.ru/technology_and_media/18/06/2026/6a32f5e79a79470dc51040c5))
- Масштаб: ~310 доменов второго уровня от 44 компаний (Роснефть, Газпромбанк, и т.д.)
- Под риском: до 15-20 тыс. доменов, ~13.4% всех .ru (данные [IPinfo](https://community.ipinfo.io/t/globalsign-is-revoking-ssl-certificates-for-russian-websites-here-is-what-the-data-shows/7381))

### 2. Отказ в продлении — Let's Encrypt

- Ранее выданные сертификаты доживают до expiry
- Новые не выдаются и не продляются для подсанкционных лиц
- LE-SA v1.7 от 4 июня 2026: запрет использования для entities из стран под comprehensive US sanctions ([PDF](https://letsencrypt.org/documents/LE-SA-v1.7-June-04-2026.pdf))
- Пример: отказ max.ru 11-12 июня, старые живут до сентября

## CA/B Forum — ключевое правило

**§3.2.2.12.2** EV Guidelines v2.0.2 от 4 мая 2026:
> CAs MUST check applicants against OFAC SDN List, BIS Denied Persons List, and EU-equivalent sanctions lists at time of issuance.

Пункт существовал с 2019 (v1.7.0), но стал обязательным к исполнению в v2.0.2.
[EV Guidelines](https://cabforum.org/working-groups/server/extended-validation/guidelines/)

## Диагностические команды

```bash
# Проверить цепочку сертификата
openssl s_client -connect host:port -showcerts

# Посмотреть количество уровней
openssl s_client -connect host:port -showcerts 2>&1 | grep -c "^\-\-\-\-\-BEGIN CERTIFICATE"

# Проверить глубину верификации nginx
nginx -T 2>&1 | grep -i verify_depth

# CRL проверка (если сертификат был отозван)
curl -v https://host:port/ 2>&1 | grep -i "CRL\|revocation\|certificate revoked"
```

## Что делать если сайт не открывается из-за revoked сертификата

1. Определить УЦ: `openssl s_client -connect host:port 2>&1 | grep "issuer"`
2. Если GlobalSign — получить новый сертификат от:
   - ТЦИ (Технический центр Интернет) — российский УЦ, кратный рост заявок
   - Минцифры / НУЦ — бесплатно через Госуслуги, но не работает в зарубежных браузерах
   - Let's Encrypt через зарубежный прокси — рискованно, но бесплатно
   - HARICA (греческий, не под санкциями) — ВТБ перешёл на них 11 июня
3. Если LE — подождать автоматического продления при снятии ограничений, или сменить УЦ
