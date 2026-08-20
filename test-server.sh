#!/bin/sh
# Тестовый сервер Velix.
#
# Свой порт, своя база, свой каталог вложений и обновлений — боевую переписку
# не трогает. Нужен для проверок: гонять их по живому чату нельзя.
#
# Запуск:  ~/velix/test-server.sh

cd "$(dirname "$0")" || exit 1

VELIX_PORT=8766 \
VELIX_DB="$HOME/velix-test/velix.db" \
VELIX_MEDIA="$HOME/velix-test/media" \
VELIX_UPDATES="$HOME/velix-test/updates" \
VELIX_ALLOWED_HOSTS= \
exec .venv/bin/python -u server.py
