#!/bin/sh
# Резервная копия переписки Velix.
#
# Копирует базу через sqlite3 .backup — так снимок получается целостным,
# даже если сервер прямо сейчас пишет сообщение. Вложения копируются
# жёсткими ссылками, поэтому место они занимают один раз.
#
# Запуск вручную:   ~/velix/backup.sh
# Раз в сутки:      crontab -e  →  30 4 * * * $HOME/velix/backup.sh

set -e

VELIX_DIR="${VELIX_DIR:-$HOME/velix}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/velix-backups}"
KEEP="${KEEP:-14}"

STAMP=$(date +%Y-%m-%d_%H-%M)
TARGET="$BACKUP_DIR/$STAMP"

mkdir -p "$TARGET"

"$VELIX_DIR/.venv/bin/python" - "$VELIX_DIR/velix.db" "$TARGET/velix.db" <<'PYTHON'
import sqlite3
import sys

source, destination = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as origin, sqlite3.connect(destination) as copy:
    origin.backup(copy)
print(f"база скопирована в {destination}")
PYTHON

if [ -d "$VELIX_DIR/media" ]; then
    cp -al "$VELIX_DIR/media" "$TARGET/media" 2>/dev/null \
        || cp -a "$VELIX_DIR/media" "$TARGET/media"
fi

# Старые копии убираем, оставляя последние KEEP штук
ls -1d "$BACKUP_DIR"/*/ 2>/dev/null | head -n -"$KEEP" | xargs -r rm -rf

echo "готово: $TARGET"
