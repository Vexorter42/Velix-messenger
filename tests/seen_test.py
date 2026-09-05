"""Сервер помнит, когда человек был в сети, и рассказывает об этом."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("seensandbox")
URI = "ws://localhost:8797"
BASE = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
            VELIX_PORT="8797", VELIX_OPEN_REGISTRATION="1")
BASE.pop("VELIX_ALLOWED_HOSTS", None)

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)


def start():
    process = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX,
                               env=BASE,
                               stdout=open(SANDBOX / "log.txt", "w", encoding="utf-8"),
                               stderr=subprocess.STDOUT)
    time.sleep(2.2)
    return process


async def read_until(ws, kind, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                       timeout=max(left, 0.1)))
        if frame and frame.get("type") == kind:
            return frame


async def drain(ws, pause=0.6):
    """Съедает всё, что уже пришло: иначе ответ спутается с приветствием."""
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=pause)
        except (asyncio.TimeoutError, TimeoutError):
            return


async def sign_up(login, name):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message(login, "parol12345", name))
    return ws, await read_until(ws, "welcome")


async def проверки():
    первый, hello = await sign_up("gosha", "Гоша")
    второй, hello2 = await sign_up("ruslan", "Руслан")
    номер = hello2["user"]["id"]

    # Пока второй в сети, первый видит его среди присутствующих
    await drain(первый)
    await первый.send(protocol.encode({"type": "people"}))
    люди = await read_until(первый, "people")
    check("seen-online-listed", номер in (люди.get("online") or []), люди.get("online"))
    check("seen-field-present",
          all("seen" in one for one in люди["items"]), люди["items"])

    # Второй уходит — первому приходит отметка времени
    await второй.close()
    прощание = await read_until(первый, "presence")
    check("seen-presence-offline", прощание.get("online") is False, прощание)
    check("seen-presence-carries-stamp", bool(прощание.get("seen")), прощание)

    отметка = datetime.fromisoformat(прощание["seen"])
    свежесть = abs(datetime.now(отметка.tzinfo) - отметка)
    check("seen-stamp-is-now", свежесть < timedelta(minutes=2), свежесть)

    # И в списке участников она тоже видна
    await drain(первый)
    await первый.send(protocol.encode({"type": "people"}))
    снова = await read_until(первый, "people")
    ушедший = next(one for one in снова["items"] if one["id"] == номер)
    check("seen-people-updated", ушедший.get("seen") == прощание["seen"], ушедший)
    check("seen-online-cleared", номер not in (снова.get("online") or []),
          снова.get("online"))

    # «Печатает» доходит до собеседника
    третий, hello3 = await sign_up("lena", "Лена")
    await drain(первый)
    await первый.send(protocol.direct_request(hello3["user"]["id"]))
    список = await read_until(первый, "conversations")
    беседа = next(one for one in список["items"]
                  if one.get("kind") == "direct" and one.get("user") == hello3["user"]["id"])
    await третий.send(protocol.typing_message(беседа["id"]))
    печать = await read_until(первый, "typing")
    check("seen-typing-relayed", печать.get("nick") == "Лена", печать)

    await первый.close()
    await третий.close()


server = start()
try:
    asyncio.run(проверки())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
