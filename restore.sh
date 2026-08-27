#!/bin/sh
# Восстановление переписки Velix из резервной копии.
#
# Копия, из которой ни разу не восстанавливались, — это не копия, а
# надежда. Этот скрипт проверяет её и ставит на место, а прежнюю базу
# уводит в сторону, чтобы отступать было куда.
#
# Посмотреть, что есть:        ~/velix/restore.sh --list
# Проверить копию:             ~/velix/restore.sh --check 2026-08-27_07-17
# Примерка в песочницу:        ~/velix/restore.sh --into /tmp/proba 2026-08-27_07-17
# Восстановить по-настоящему:  ~/velix/restore.sh 2026-08-27_07-17
#
# Перед настоящим восстановлением сервер нужно остановить:
#   sudo systemctl stop velix && ~/velix/restore.sh <копия> && sudo systemctl start velix
#
# Имена здесь латиницей нарочно: в переменные и функции оболочка русских
# букв не пускает.

set -e

VELIX_DIR="${VELIX_DIR:-$HOME/velix}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/velix-backups}"
PYTHON="${PYTHON:-$VELIX_DIR/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

list_backups() {
    echo "Копии в $BACKUP_DIR:"
    for folder in "$BACKUP_DIR"/*/; do
        [ -f "$folder/velix.db" ] || continue
        name=$(basename "$folder")
        weight=$(du -sh "$folder" 2>/dev/null | cut -f1)
        count=$("$PYTHON" - "$folder/velix.db" <<'PYTHON' 2>/dev/null || echo "?"
import sqlite3
import sys

try:
    with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
        print(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
except Exception:
    print("?")
PYTHON
)
        printf '  %-20s %6s  сообщений: %s\n' "$name" "$weight" "$count"
    done
}

verify() {
    if [ ! -f "$1/velix.db" ]; then
        echo "Нет базы в $1"
        exit 1
    fi
    "$PYTHON" - "$1/velix.db" "$1" <<'PYTHON'
import sqlite3
import sys
from pathlib import Path

база, каталог = sys.argv[1], Path(sys.argv[2])
with sqlite3.connect(f"file:{база}?mode=ro", uri=True) as db:
    целость = db.execute("PRAGMA integrity_check").fetchone()[0]
    if целость != "ok":
        print(f"База побита: {целость}")
        raise SystemExit(1)

    люди = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    сообщений = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    переписок = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    последнее = db.execute(
        "SELECT created_at FROM messages ORDER BY id DESC LIMIT 1").fetchone()

    # Вложения должны лежать рядом: база без них — половина копии
    нужны = [строка[0] for строка in db.execute(
        "SELECT media_id FROM messages"
        " WHERE media_id IS NOT NULL AND media_id != ''")]

media = каталог / "media"
пропало = [номер for номер in нужны if not list(media.glob(номер + "*"))]

print(f"целость: ok, людей: {люди}, переписок: {переписок}, "
      f"сообщений: {сообщений}")
print(f"последнее сообщение: {последнее[0] if последнее else 'нет'}")
print(f"вложений в базе: {len(нужны)}, "
      + ("все на месте" if not пропало else f"ПРОПАЛО: {len(пропало)}"))
raise SystemExit(1 if пропало else 0)
PYTHON
}

# ------------------------------------------------------------------ разбор

WHAT="check"
WHERE="$VELIX_DIR"
case "$1" in
    --list|-l) list_backups; exit 0 ;;
    --check) WHAT="check"; shift ;;
    --into) WHAT="restore"; WHERE="$2"; shift 2 ;;
    "") echo "Укажите копию. Список: $0 --list"; exit 1 ;;
    *) WHAT="restore" ;;
esac

COPY="$BACKUP_DIR/$1"
[ -d "$COPY" ] || COPY="$1"
if [ ! -d "$COPY" ]; then
    echo "Не нашёл копию: $1"
    exit 1
fi

echo "Копия: $COPY"
verify "$COPY"

if [ "$WHAT" = "check" ]; then
    echo "Копия годная."
    exit 0
fi

# --------------------------------------------------------- восстановление

if [ "$WHERE" = "$VELIX_DIR" ] && pgrep -f "$VELIX_DIR/server.py" > /dev/null 2>&1; then
    echo "Сервер запущен. Остановите его: sudo systemctl stop velix"
    exit 1
fi

mkdir -p "$WHERE"
STAMP=$(date +%Y-%m-%d_%H-%M-%S)

if [ -f "$WHERE/velix.db" ]; then
    # Прежнюю базу не стираем: отступать должно быть куда
    mv "$WHERE/velix.db" "$WHERE/velix.db.before-$STAMP"
    rm -f "$WHERE/velix.db-wal" "$WHERE/velix.db-shm"
    echo "Прежняя база отложена: velix.db.before-$STAMP"
fi
if [ -d "$WHERE/media" ]; then
    mv "$WHERE/media" "$WHERE/media.before-$STAMP"
    echo "Прежние вложения отложены: media.before-$STAMP"
fi

cp "$COPY/velix.db" "$WHERE/velix.db"
if [ -d "$COPY/media" ]; then
    cp -a "$COPY/media" "$WHERE/media"
fi

echo "Готово: $WHERE"
echo "Проверьте и запустите сервер: sudo systemctl start velix"
