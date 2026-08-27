#!/bin/sh
# Выкладывает свежую сборку для кнопки «Обновить» в клиентах.
#
# Кладёт файл и отметку версии в каталог, который раздаёт сервер. Базу и
# переписку не трогает — только эти два файла. Что именно выкладывается,
# скрипт понимает по расширению: exe — для окна, apk — для телефона.
#
# Запуск:  ~/velix/publish-update.sh /путь/к/Velix.exe 0.2.7.0
#          ~/velix/publish-update.sh /путь/к/Velix.apk 0.2.7.0

set -e

BUILD="$1"
VERSION="$2"
UPDATES="${VELIX_UPDATES:-$HOME/velix/updates}"

if [ -z "$BUILD" ] || [ -z "$VERSION" ]; then
    echo "нужно: publish-update.sh <путь к Velix.exe или Velix.apk> <версия>" >&2
    exit 1
fi

if [ ! -f "$BUILD" ]; then
    echo "файл не найден: $BUILD" >&2
    exit 1
fi

case "$BUILD" in
    *.apk) NAME="Velix.apk"; MARKER="apk-version.txt"; WHAT="приложение" ;;
    *.exe) NAME="Velix.exe"; MARKER="version.txt";     WHAT="сборка" ;;
    *) echo "не пойму, что это: ждал .exe или .apk" >&2; exit 1 ;;
esac

mkdir -p "$UPDATES"
cp "$BUILD" "$UPDATES/$NAME.tmp"
mv "$UPDATES/$NAME.tmp" "$UPDATES/$NAME"
printf '%s\n' "$VERSION" > "$UPDATES/$MARKER"

echo "выложена версия $VERSION — $WHAT ($(du -h "$UPDATES/$NAME" | cut -f1))"
echo "перезапустите сервер, чтобы он это заметил:  sudo systemctl restart velix"
