"""Хозяин чата, когда самый первый аккаунт удалён.

Ровно случай с боевого сервера: учётная запись под номером 1 когда-то
пропала, и панель управления не досталась никому.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("ownersandbox")
URI = "ws://localhost:8793"
BASE = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
            VELIX_PORT="8793", VELIX_OPEN_REGISTRATION="1")
BASE.pop("VELIX_ALLOWED_HOSTS", None)

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
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)


def start(**extra):
    env = dict(BASE, **extra)
    process = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX,
                               env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
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


async def sign_up(login, name):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message(login, "parol12345", name))
    welcome = await read_until(ws, "welcome")
    return ws, welcome


async def sign_in(login):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.login_message(login, "parol12345"))
    welcome = await read_until(ws, "welcome")
    return ws, welcome


async def stage_one():
    """Заводим троих и убираем самого первого."""
    first, hello = await sign_up("pervyj", "Первый")
    check("owner-first-is-one", hello["user"]["id"] == 1, hello["user"])

    second, hello2 = await sign_up("vtoroj", "Второй")
    third, _ = await sign_up("tretij", "Третий")

    # Хозяином на это время назначен второй — иначе удалить первого некому
    check("owner-named-by-env", hello2.get("admin") is True, hello2)

    await second.send(protocol.admin_request("drop_user", user=1))
    await asyncio.sleep(1.2)

    for ws in (first, second, third):
        await ws.close()


async def stage_two():
    """Теперь хозяином должен стать самый давний из оставшихся."""
    second, hello = await sign_in("vtoroj")
    check("owner-falls-to-earliest", hello.get("admin") is True, hello)

    await second.send(protocol.admin_request("stats"))
    stats = await read_until(second, "admin")
    logins = sorted(one["login"] for one in stats["stats"]["users"])
    check("owner-sees-stats", logins == ["tretij", "vtoroj"], logins)
    check("owner-first-gone", all(one["id"] != 1 for one in stats["stats"]["users"]),
          stats["stats"]["users"])

    third, hello3 = await sign_in("tretij")
    check("owner-others-not-admin", not hello3.get("admin"), hello3)

    await third.send(protocol.admin_request("stats"))
    refusal = await read_until(third, "error")
    check("owner-others-refused", refusal.get("code") == "not_admin", refusal)

    await second.close()
    await third.close()


server = start(VELIX_ADMIN="vtoroj")
try:
    asyncio.run(stage_one())
finally:
    server.terminate()
    server.wait(timeout=5)

time.sleep(1.0)
server = start()          # без VELIX_ADMIN — хозяина ищем по старшинству
try:
    asyncio.run(stage_two())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
