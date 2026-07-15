# Native deb building on armhf WB controller

## Проблема

Билд-машина arm64 (aarch64), контроллер armhf (32-bit). `dpkg-buildpackage` на билд-машине собирает arm64 — несовместимо с armhf-контроллером. Кросс-компиляция (`-aarmhf`) возможна, но требует armhf-инструментарий + все `:armhf`-зависимости — громоздко.

## Решение: native build на контроллере

```bash
# 1. На билд-машине: source tarball из git
cd /path/to/repo
git archive --format=tar HEAD | gzip > /tmp/package-version.tar.gz

# 2. Скопировать на контроллер
scp /tmp/package-version.tar.gz root@<controller>:/tmp/

# 3. На контроллере: распаковать + собрать
ssh root@<controller>
cd /tmp && rm -rf build-dir && mkdir build-dir && cd build-dir
tar xzf ../package-version.tar.gz
dpkg-buildpackage -b -uc -us
```

## Build-deps

Если не установлены — dpkg-buildpackage упадёт:
```bash
grep -A20 '^Package:.*' /tmp/build-dir/debian/control | grep Build-Depends
apt-get build-dep .   # находясь в build-dir
```
Или вручную:
```bash
apt-get install -y debhelper cmake libcjson-dev libssl-dev libsystemd-dev libwebsockets-dev ...
```

## Проверка версии до/после установки

```bash
dpkg -l <package> | tail -1                                          # что стоит сейчас
dpkg --info /tmp/<package>_<version>_armhf.deb | grep -E 'Package|Version|Architecture'  # что в .deb
```

Установка:
```bash
systemctl stop mosquitto  # если брокер
dpkg -i <package>.deb <lib>.deb ...
systemctl start mosquitto
```

Верификация:
```bash
dpkg -s <package> | grep -E "^Version"
<binary> --version
```

## Применимость

- Любой deb-пакет, который нужно собрать с патчем и проверить на armhf-контроллере
- Работает для C/C++ (cmake/make), Rust (cargo), Python (setuptools) — лишь бы build-deps были
- Не требует cross-toolchain, qemu, chroot или контейнеров
