"""Сторож: проверяет, что сервер отвечает, а не просто «запущен».

Restart=on-failure поднимает упавший процесс. Но повисший процесс не падает:
служба остаётся active, порт слушается, а на приветствие никто не отвечает —
и узнаёшь об этом, только когда сам откроешь окно и увидишь «нет связи».

Поэтому раз в несколько минут сторож ведёт себя как обычный клиент: стучится,
здоровается заведомо негодным ключом и ждёт ответа «сессия больше не
действует». Такой ответ значит, что живы и цикл событий, и база, — а больше
ничего и не нужно. Ключ негодный нарочно: войти сторож не пытается и ничью
переписку не трогает.

    python watchdog.py           — проверить и, если надо, перезапустить
    python watchdog.py --once    — только проверить, ничего не трогать
"""

import asyncio
import json
import os
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import websockets

import notify
import protocol

ДОМ = Path(__file__).resolve().parent
ПОРТ = int(os.environ.get("VELIX_PORT") or 8765)
СЛУЖБА = os.environ.get("VELIX_UNIT", "velix")
ПАМЯТЬ = Path(os.environ.get("VELIX_WATCHDOG_STATE") or ДОМ / "watchdog-state.json")
ЖУРНАЛ_КОПИЙ = Path(os.environ.get("VELIX_PULL_LOG") or ДОМ / "backup-pull.log")

ЖДЁМ = float(os.environ.get("VELIX_WATCHDOG_TIMEOUT") or 15)
ПОПЫТОК = 2                 # одна осечка бывает и на ровном месте
ПЕРЕЗАПУСКОВ_В_ЧАС = 3      # дальше только зовём человека
КОПИЯ_УСТАРЕЛА = 30 * 3600  # копии снимаются раз в сутки, с запасом


def помним():
    try:
        return json.loads(ПАМЯТЬ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def запомнить(что):
    try:
        ПАМЯТЬ.write_text(json.dumps(что, ensure_ascii=False), encoding="utf-8")
    except OSError:
        # Не записалось — сторож всё равно отработает, просто без памяти
        pass


async def отзывается():
    """Стучится к серверу как обычный клиент. True, если ответил."""
    # Разговариваем сами с собой через loopback, так что проверять имя в
    # сертификате не у кого и незачем: он выписан на внешнее имя
    доверие = ssl.create_default_context()
    доверие.check_hostname = False
    доверие.verify_mode = ssl.CERT_NONE

    for где, как in ((f"wss://localhost:{ПОРТ}", доверие),
                     (f"ws://localhost:{ПОРТ}", None)):
        try:
            async with websockets.connect(где, ssl=как, open_timeout=ЖДЁМ,
                                          close_timeout=5) as связь:
                await связь.send(protocol.auth_message("сторож-стучится"))
                предел = time.monotonic() + ЖДЁМ
                while time.monotonic() < предел:
                    кадр = protocol.decode(await asyncio.wait_for(
                        связь.recv(), timeout=ЖДЁМ))
                    if кадр and кадр.get("type") in ("authfail", "error"):
                        return True
        except (OSError, ssl.SSLError, asyncio.TimeoutError,
                websockets.exceptions.WebSocketException):
            continue
    return False


def перезапустить():
    """Просит systemd поднять службу заново."""
    try:
        готово = subprocess.run(["sudo", "-n", "systemctl", "restart", СЛУЖБА],
                                capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as беда:
        return False, str(беда)
    return готово.returncode == 0, (готово.stderr or "").strip()


def копии_приходят(память):
    """Смотрит, забирает ли домашний сервер копии. Возвращает жалобу или None.

    Молчащая копия — самый привычный способ остаться без копий вообще:
    всё как будто работает, просто однажды перестало.
    """
    if not ЖУРНАЛ_КОПИЙ.exists():
        return None

    # Строчки такие: «2026-08-29 09:13 привезли 2026-08-29_04-30, сообщений 36»
    последняя = 0
    try:
        строки = ЖУРНАЛ_КОПИЙ.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for строка in строки:
        слова = строка.split()
        if len(слова) < 3 or "привезли" not in строка:
            continue
        try:
            когда = datetime.strptime(f"{слова[0]} {слова[1]}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        последняя = max(последняя, когда.timestamp())

    if not последняя:
        return None

    отстал = time.time() - последняя
    if отстал < КОПИЯ_УСТАРЕЛА:
        return None

    # Жалуемся раз в сутки, а не каждые пять минут
    if time.time() - память.get("ныл_о_копиях", 0) < 24 * 3600:
        return None
    память["ныл_о_копиях"] = time.time()
    return f"Домашний сервер не забирал копию Velix уже {отстал / 3600:.0f} ч."


async def main():
    только_посмотреть = "--once" in sys.argv
    память = помним()

    жив = False
    for попытка in range(ПОПЫТОК):
        жив = await отзывается()
        if жив:
            break
        if попытка + 1 < ПОПЫТОК:
            await asyncio.sleep(5)

    жалоба = копии_приходят(память)
    if жалоба:
        print(жалоба)
        notify.say(жалоба)

    if жив:
        память["жив"] = time.time()
        память.pop("молчит_с", None)
        запомнить(память)
        print("сервер отвечает")
        return 0

    print("сервер не отвечает")
    if только_посмотреть:
        запомнить(память)
        return 1

    # Перезапуск по кругу ничего не чинит, а журнал заваливает. Если за час
    # уже поднимали трижды — дальше только зовём человека
    недавние = [когда for когда in память.get("перезапуски", [])
                if time.time() - когда < 3600]
    if len(недавние) >= ПЕРЕЗАПУСКОВ_В_ЧАС:
        память["перезапуски"] = недавние
        запомнить(память)
        if time.time() - память.get("ныл_о_кругах", 0) > 3600:
            память["ныл_о_кругах"] = time.time()
            запомнить(память)
            notify.say(f"Velix не поднимается: {len(недавние)} перезапуска за"
                       " час и всё зря. Нужны руки.")
        print("перезапускать больше не буду, зову человека")
        return 1

    подняли, беда = перезапустить()
    недавние.append(time.time())
    память["перезапуски"] = недавние
    запомнить(память)

    if подняли:
        await asyncio.sleep(6)
        снова = await отзывается()
        notify.say("Velix не отвечал — перезапустил. "
                   + ("Отвечает." if снова else "Пока молчит."))
        print("перезапустил, отвечает" if снова else "перезапустил, молчит")
        return 0 if снова else 1

    notify.say(f"Velix не отвечает, и перезапустить не вышло: {беда[:200]}")
    print(f"перезапустить не вышло: {беда}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
