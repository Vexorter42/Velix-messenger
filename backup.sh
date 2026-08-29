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
PYTHON="${PYTHON:-$VELIX_DIR/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

STAMP=$(date +%Y-%m-%d_%H-%M)
TARGET="$BACKUP_DIR/$STAMP"

mkdir -p "$TARGET"

"$PYTHON" - "$VELIX_DIR/velix.db" "$TARGET/velix.db" <<'PYTHON'
import sqlite3
import sys

source, destination = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as origin, sqlite3.connect(destination) as copy:
    origin.backup(copy)
print(f"база скопирована в {destination}")
PYTHON

# Журнал сливаем в саму базу. В режиме WAL velix.db может месяцами
# оставаться заготовкой в одну страницу, пока вся переписка лежит рядом в
# velix.db-wal: копия выше читает и журнал, а вот человек, скопировавший
# руками один velix.db, увезёт пустоту и будет уверен, что увёз переписку.
"$PYTHON" - "$VELIX_DIR/velix.db" <<'PYTHON'
import sqlite3
import sys

база = sqlite3.connect(sys.argv[1])
try:
    занято, _, слито = база.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print(f"журнал слит, страниц: {слито}" if not занято
          else "журнал сейчас занят, сольётся в следующий раз")
except sqlite3.Error as беда:
    print(f"журнал слить не вышло: {беда}")
finally:
    база.close()
PYTHON

if [ -d "$VELIX_DIR/media" ]; then
    cp -al "$VELIX_DIR/media" "$TARGET/media" 2>/dev/null \
        || cp -a "$VELIX_DIR/media" "$TARGET/media"
fi

# Старые копии убираем, оставляя последние KEEP штук
ls -1d "$BACKUP_DIR"/*/ 2>/dev/null | head -n -"$KEEP" | xargs -r rm -rf

echo "готово: $TARGET"
