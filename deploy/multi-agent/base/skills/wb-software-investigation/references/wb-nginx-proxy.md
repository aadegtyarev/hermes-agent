# Nginx Proxy Locations на Wiren Board

## Проблема: статика не грузится через кастомный location

При добавлении кастомного proxy location (`/nr` для Node-RED, `/z2m` для Zigbee2mqtt, `/ha` для Home Assistant) статические JS/CSS возвращают 404, хотя HTML грузится.

### Диагностический чеклист

1. Браузер → консоль: искать `Загрузка <script>... не удалась` / `Loading failed for <script>`
2. curl с контроллера: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/nr/vendor/vendor.js`
3. Если curl через nginx (80) → 404, а напрямую на сервис (1880) → 200 — проблема в nginx
4. `tail -20 /var/log/nginx/error.log` — искать `open() "/var/www/... " failed`
5. Если находит — regex-локация `~* \\.(js|css)$` перехватила static-запрос до prefix-прокси

### Причина

```nginx
location ~* \.(js|jpg|jpeg|gif|png|svg|css|json)$ {
    add_header Cache-Control "max-age=31536000";
}
```

Regex-локации (`~*`) имеют **высший приоритет** над prefix-локациями без модификатора. Запрос `/nr/vendor/vendor.js` попадает в regex `\.js$` (только Cache-Control, без `proxy_pass`) → nginx пытается открыть файл из `root /var/www/...` → 404.

### Фикс

Модификатор `^~` даёт prefix-локации приоритет над regex:
```nginx
location ^~ /nr {
    proxy_pass http://127.0.0.1:1880;
    ...
}
```

## Типовой сценарий: Node-RED

### Docker (network=host, порт 1880)

Контейнер `nodered/node-red:latest`. **settings.js** (`/data/settings.js`): `httpAdminRoot: '/nr',`

**nginx:** `/etc/nginx/includes/default.wb.d/node-red.conf`
```nginx
location ^~ /nr {
    proxy_pass http://127.0.0.1:1880;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Применить:**
```bash
nginx -t && systemctl reload nginx
docker restart node-red
```

### Native (apt install node-red)

settings.js: `~/.node-red/settings.js` (обычно `/root/` или `/home/wb/`). Рестарт: `systemctl restart nodered`

### Cloud-доступ

После настройки локального /nr редактор доступен через WirenBoard Cloud: `https://XXXXXX.http.wirenboard.cloud/nr/`. Облачный прокси (frp-туннель → локальный nginx:80) прозрачен для любых location.

## Другие сервисы

Тот же паттерн:
```nginx
location ^~ /service {
    proxy_pass http://127.0.0.1:PORT;
    ...
}
```

- Zigbee2mqtt (8080) — `/z2m`; Home Assistant (8123) — `/ha`; Grafana (3000) — `/grafana`; любой Docker с веб-интерфейсом

**Важно:** некоторые приложения не поддерживают subpath (Home Assistant требует reverse proxy с доп. заголовками) — проверяй документацию приложения.
