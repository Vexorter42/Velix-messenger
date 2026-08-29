"""Короткая весточка в Telegram, когда с сервером что-то не так.

Своего бота Velix не заводит. На малине уже живёт тот, что шлёт погоду, и
дорога до Telegram у него проложена вместе с обходом — мы просто читаем его
настройки по месту, ничего никуда не копируя: секрет остаётся лежать там, где
лежал. Настроек нет — молчим, и это не беда: сторож всё равно сделает свою
работу, просто без весточки.

    python notify.py "Velix не отвечал, перезапустил"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("VELIX_TG_CONFIG")
              or Path.home() / "weather-mesh-bridge" / "config.json")
TARGET = Path(os.environ.get("VELIX_TG_TARGET")
              or Path.home() / "weather-report" / "target.txt")
PROXY = os.environ.get("VELIX_TG_PROXY", "socks5://127.0.0.1:10808")


def _найти(где, ключ):
    """Достаёт значение из настроек, как бы глубоко оно ни лежало."""
    if isinstance(где, dict):
        for имя, значение in где.items():
            if имя == ключ and isinstance(значение, str) and значение.strip():
                return значение.strip()
            найдено = _найти(значение, ключ)
            if найдено:
                return найдено
    elif isinstance(где, list):
        for значение in где:
            найдено = _найти(значение, ключ)
            if найдено:
                return найдено
    return None


def настройки():
    """Возвращает (токен, кому писать) или (None, None), если писать некому."""
    токен = os.environ.get("VELIX_TG_TOKEN")
    кому = os.environ.get("VELIX_TG_CHAT")

    if not (токен and кому):
        try:
            записка = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            записка = {}
        токен = токен or _найти(записка, "bot_token")
        if not кому and TARGET.exists():
            try:
                кому = TARGET.read_text(encoding="utf-8").strip()
            except OSError:
                кому = None
        кому = кому or _найти(записка, "chat_id")

    return (токен, кому) if токен and кому else (None, None)


def _в_кавычках(строка):
    """Готовит строку для настроек curl: кавычки внутри всё портят."""
    return str(строка).replace("\\", "/").replace('"', "'").replace("\n", " ")


def say(text):
    """Отправляет строку в Telegram. True, если ушла."""
    токен, кому = настройки()
    if not токен:
        return False

    адрес = PROXY.split("://", 1)[-1]
    # Токен уходит в curl через stdin, а не строкой запуска: иначе он светился
    # бы в списке процессов любому, кто наберёт ps
    указания = (f'url = "https://api.telegram.org/bot{токен}/sendMessage"\n'
                f'socks5-hostname = "{адрес}"\n'
                f'data-urlencode = "chat_id={_в_кавычках(кому)}"\n'
                f'data-urlencode = "text={_в_кавычках(text)}"\n'
                'silent\n'
                'max-time = 20\n')
    try:
        готово = subprocess.run(["curl", "-K", "-"], input=указания, text=True,
                                capture_output=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        return False
    return готово.returncode == 0 and '"ok":true' in (готово.stdout or "")


if __name__ == "__main__":
    слово = " ".join(sys.argv[1:]).strip()
    if not слово:
        print("что сказать-то?")
        sys.exit(2)
    if say(слово):
        print("отправлено")
    else:
        print("отправить не вышло: некому или не дошло")
        sys.exit(1)
