#!/bin/sh
# Выкладывает свежую сборку для кнопки «Обновить» в клиентах.
#
# Кладёт Velix.exe и version.txt в каталог, который раздаёт сервер. Базу и
# переписку не трогает — только эти два файла.
#
# Запуск:  ~/velix/publish-update.sh /путь/к/Velix.exe 1.4.0

set -e

BUILD="$1"
VERSION="$2"
UPDATES="${VELIX_UPDATES:-$HOME/velix/updates}"

if [ -z "$BUILD" ] || [ -z "$VERSION" ]; then
    echo "нужно: publish-update.sh <путь к Velix.exe> <версия>" >&2
    exit 1
fi

if [ ! -f "$BUILD" ]; then
    echo "файл не найден: $BUILD" >&2
    exit 1
fi

mkdir -p "$UPDATES"
cp "$BUILD" "$UPDATES/Velix.exe.tmp"
mv "$UPDATES/Velix.exe.tmp" "$UPDATES/Velix.exe"
printf '%s\n' "$VERSION" > "$UPDATES/version.txt"

echo "выложена версия $VERSION ($(du -h "$UPDATES/Velix.exe" | cut -f1))"
echo "перезапустите сервер, чтобы он это заметил:  sudo systemctl restart velix"
