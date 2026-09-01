"""Переписка живёт в самой базе, а не только в журнале рядом с ней.

В режиме WAL записи копятся в velix.db-wal, а velix.db может оставаться
заготовкой в одну страницу: на боевой малине так и было — база от 20 августа
весила четыре килобайта, а весь разговор лежал в журнале. Копии от этого не
страдают, они читают и журнал. Страдает человек, скопировавший руками один
velix.db: он увезёт пустоту и будет уверен, что увёз переписку.
"""

import asyncio
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import harness

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import storage  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


песочница = Path(tempfile.mkdtemp(prefix="velix-wal-"))


def сколько(файл, таблица="messages"):
    """Открывает копию одного velix.db — без журнала — и считает строки."""
    отдельно = песочница / "одна-база"
    shutil.rmtree(отдельно, ignore_errors=True)
    отдельно.mkdir()
    shutil.copy(файл, отдельно / "velix.db")
    база = sqlite3.connect(отдельно / "velix.db")
    try:
        return база.execute(f"SELECT COUNT(*) FROM {таблица}").fetchone()[0]
    except sqlite3.OperationalError:
        # Пока журнал не слит, в базе нет даже таблиц — она пустая заготовка
        return None
    finally:
        база.close()


async def проверки():
    дом = песочница / "velix"
    дом.mkdir()
    await storage.init(дом / "velix.db", дом / "media")
    кто = await storage.create_user("gosha", "hash", "Гоша")
    номер = кто["id"] if isinstance(кто, dict) else кто
    await storage.save_message(номер, "Гоша", "привет из журнала")

    # --- пока журнал не слит, отдельно взятый velix.db пуст
    check("wal-holds-everything-at-first",
          сколько(дом / "velix.db") in (None, 0),
          "журнал уже слит сам — проверять нечего")

    слито = await storage.checkpoint()
    check("checkpoint-reports-done", слито is True, слито)
    check("checkpoint-moves-messages-into-base",
          сколько(дом / "velix.db") == 1)

    await storage.save_message(номер, "Гоша", "и второе")
    await storage.close()
    check("close-moves-messages-into-base",
          сколько(дом / "velix.db") == 2)

    журнал = дом / "velix.db-wal"
    check("close-leaves-no-fat-journal",
          not журнал.exists() or журнал.stat().st_size == 0,
          журнал.stat().st_size if журнал.exists() else "нет")


asyncio.run(проверки())

# ------------------------------------------------ остановка службы сигналом
#
# systemd останавливает сервер SIGTERM'ом. Раньше Python обрывал процесс на
# месте, finally не отрабатывал — и база оставалась с недослитым журналом.

if os.name == "nt":
    print("TEST sigterm-checkpoints-base: SKIP — в Windows нет SIGTERM")
else:
    ДОМ = песочница / "служба"
    ДОМ.mkdir()
    for name in ("server.py", "storage.py", "protocol.py", "media.py",
                 "accounts.py", "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
        shutil.copy(REPO / name, ДОМ / name)

    среда = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                 VELIX_PORT="8846", VELIX_OPEN_REGISTRATION="1")
    среда.pop("VELIX_ALLOWED_HOSTS", None)
    служба = subprocess.Popen([sys.executable, "server.py"], cwd=ДОМ, env=среда,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    harness.дождаться(8846)

    async def поговорить():
        """Заводит человека: этого хватит, чтобы в базе появилась строчка."""
        import websockets
        import protocol
        async with websockets.connect("ws://localhost:8846",
                                      max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("gosha", "parol12345", "Гоша"))
            предел = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < предел:
                кадр = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=20))
                if кадр and кадр.get("type") == "welcome":
                    break
            await asyncio.sleep(1.0)

    asyncio.run(поговорить())

    служба.send_signal(signal.SIGTERM)
    try:
        служба.wait(timeout=15)
        ушла = True
    except subprocess.TimeoutExpired:
        служба.kill()
        ушла = False

    check("sigterm-stops-service", ушла)
    осталось = сколько(ДОМ / "velix.db", "users")
    check("sigterm-checkpoints-base", осталось == 1, осталось)

    хвост = ДОМ / "velix.db-wal"
    check("sigterm-leaves-no-fat-journal",
          not хвост.exists() or хвост.stat().st_size == 0,
          хвост.stat().st_size if хвост.exists() else "нет")

shutil.rmtree(песочница, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
