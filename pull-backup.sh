#!/bin/sh
# Забирает свежую копию переписки Velix с малины на домашний сервер.
#
# Копии лежали на той же карте, что и сама переписка: карта умирает — уходит
# и то и другое разом. Поэтому раз в сутки, уже после ночной копии, домашний
# сервер тянет свежую к себе.
#
# Тянем, а не толкаем: у малины ключей от сервера копий нет.
#
# Запуск вручную:  ~/velix-backup/pull-backup.sh
# Раз в сутки:     systemd --user, velix-backup.timer

set -e

FROM="${FROM:-vexorter@192.168.0.225}"
KEY="${KEY:-$HOME/.ssh/velix-backup}"
INTO="${INTO:-$HOME/backups/velix}"
KEEP="${KEEP:-14}"
# Подменяется в проверках: там вместо ssh стоит заглушка, которая зовёт
# serve-backup.sh на этой же машине
SSH="${SSH:-ssh}"

mkdir -p "$INTO"

# Прошлая копия пригодится дважды: сверить, что новая не пустее, и сложить
# одинаковые вложения в одно место
PREVIOUS=$(ls -1d "$INTO"/*/ 2>/dev/null | sed 's:/*$::' | sort | tail -1)

TMP=$(mktemp -d "$INTO/.pull-XXXXXX")
trap 'rm -rf "$TMP"' EXIT

"$SSH" -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$FROM" latest | tar -C "$TMP" -xf -

FRESH=$(ls -1d "$TMP"/*/ 2>/dev/null | sed 's:/*$::' | head -1)
[ -n "$FRESH" ] || { echo "с малины ничего не пришло" >&2; exit 1; }
NAME=$(basename "$FRESH")

# Копия, из которой нельзя восстановиться, — не копия, а надежда. Поэтому
# сразу открываем базу и смотрим, что она цела и в ней есть переписка
WHOLE=$(sqlite3 "$FRESH/velix.db" "PRAGMA integrity_check" 2>/dev/null || echo "не открылась")
[ "$WHOLE" = "ok" ] || { echo "копия $NAME не открылась: $WHOLE" >&2; exit 1; }
COUNT=$(sqlite3 "$FRESH/velix.db" "SELECT COUNT(*) FROM messages" 2>/dev/null || echo 0)
[ "$COUNT" -gt 0 ] 2>/dev/null || { echo "в копии $NAME нет ни одного сообщения" >&2; exit 1; }

if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$INTO/$NAME" ]; then
    BEFORE=$(sqlite3 "$PREVIOUS/velix.db" "SELECT COUNT(*) FROM messages" 2>/dev/null || echo 0)
    if [ "$COUNT" -lt "$BEFORE" ]; then
        # Не отказываемся — переписку могли и почистить, — но говорим вслух
        echo "внимание: было сообщений $BEFORE, стало $COUNT" >&2
    fi
fi

rm -rf "$INTO/$NAME"
mv "$FRESH" "$INTO/$NAME"

# Вложения между копиями одни и те же: имя файла и есть его содержимое.
# Одинаковые складываем в одно место жёсткими ссылками — место они займут
# один раз, а лежать будут в каждой копии
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$INTO/$NAME" ] \
   && [ -d "$PREVIOUS/media" ] && [ -d "$INTO/$NAME/media" ]; then
    for ONE in "$INTO/$NAME"/media/*; do
        [ -f "$ONE" ] || continue
        TWIN="$PREVIOUS/media/$(basename "$ONE")"
        [ -f "$TWIN" ] || continue
        [ "$(stat -c %s "$ONE")" = "$(stat -c %s "$TWIN")" ] || continue
        ln -f "$TWIN" "$ONE"
    done
fi

# Старые копии убираем, оставляя последние KEEP штук
ls -1d "$INTO"/*/ 2>/dev/null | sed 's:/*$::' | sort | head -n -"$KEEP" | xargs -r rm -rf

# Отмечаемся на малине: сторож там смотрит, не молчит ли домашний сервер
# неделю. Копия, о которой никто не спросил, — самый обычный способ однажды
# остаться без копий вообще
"$SSH" -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$FROM"     "report $NAME $COUNT" || echo "отметиться не вышло" >&2

echo "$(date '+%Y-%m-%d %H:%M') привезли $NAME, сообщений $COUNT"
