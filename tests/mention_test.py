"""Упоминание @username: позванный узнаёт об этом."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import harness

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("mentionsandbox")
URI = "ws://localhost:8835"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8835", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
harness.дождаться(8835)


async def read_until(ws, kind, timeout=15):
    предел = asyncio.get_running_loop().time() + timeout
    while True:
        осталось = предел - asyncio.get_running_loop().time()
        кадр = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                      timeout=max(осталось, 0.1)))
        if кадр and кадр.get("type") == kind:
            return кадр


async def войти(login, name):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message(login, "parol12345", name))
    return ws, await read_until(ws, "welcome")


async def проверки():
    гоша, привет = await войти("gosha", "Гоша")
    лена, привет_л = await войти("lena", "Лена")
    руслан, привет_р = await войти("ruslan", "Руслан")

    await гоша.send(protocol.group_request("Поход", [привет_л["user"]["id"],
                                                     привет_р["user"]["id"]]))
    группа = (await read_until(гоша, "conversation"))["item"]["id"]

    # --- позвали одного
    await гоша.send(protocol.text_message("Гоша", "@lena, ты идёшь?", группа))
    у_лены = await read_until(лена, "text")
    check("mention-marked", у_лены.get("mentions") == [привет_л["user"]["id"]],
          у_лены.get("mentions"))

    у_руслана = await read_until(руслан, "text")
    check("mention-same-frame-for-all",
          у_руслана.get("mentions") == [привет_л["user"]["id"]],
          у_руслана.get("mentions"))

    # --- регистр не важен, и можно позвать двоих
    await гоша.send(protocol.text_message("Гоша", "@Lena и @RUSLAN, подъём", группа))
    оба = await read_until(лена, "text")
    check("mention-case-insensitive",
          sorted(оба.get("mentions") or []) == sorted([привет_л["user"]["id"],
                                                       привет_р["user"]["id"]]),
          оба.get("mentions"))

    # --- себя не зовут, и незнакомцев тоже
    await гоша.send(protocol.text_message("Гоша", "@gosha сам себе @nikto", группа))
    пусто = await read_until(лена, "text")
    check("mention-not-self-not-strangers", not пусто.get("mentions"),
          пусто.get("mentions"))

    # --- обычное сообщение живёт без пометки
    await гоша.send(protocol.text_message("Гоша", "просто адрес a@b.ru", группа))
    обычное = await read_until(лена, "text")
    check("mention-plain-text-clean", not обычное.get("mentions"),
          обычное.get("mentions"))

    for ws in (гоша, лена, руслан):
        await ws.close()


try:
    asyncio.run(проверки())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
