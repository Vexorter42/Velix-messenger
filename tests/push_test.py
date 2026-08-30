"""Уведомления: ключи, подписка, отправка отсутствующим."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("pushsandbox")
URI = "ws://localhost:8774"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8774")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402
import push  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------------------ ключи

push.KEY_PATH = SANDBOX / "push-keys.json"
if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

check("push-library-available", push.available(), "pywebpush не установлен")

key = push.public_key()
check("push-key-generated", bool(key) and len(key) > 80, key)
check("push-key-file", push.KEY_PATH.exists(), push.KEY_PATH)
check("push-key-stable", push.public_key() == key, "ключ меняется между вызовами")

stored = json.loads(push.KEY_PATH.read_text(encoding="utf-8"))
check("push-key-has-private", "BEGIN" in stored["private"], stored["private"][:20])

# ------------------------------------------------------------------ сервер

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace")
time.sleep(2.2)


async def read_until(ws, kind, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def sign_in(ws, login):
    await ws.send(protocol.register_message(login, "пароль123", login))
    welcome = await read_until(ws, "welcome")
    await read_until(ws, "people")  # заодно съедает список переписок
    return welcome["user"]["id"]


async def log_in(ws, login):
    await ws.send(protocol.login_message(login, "пароль123"))
    welcome = await read_until(ws, "welcome")
    await read_until(ws, "people")
    return welcome["user"]["id"]


FAKE_SUBSCRIPTION = {
    "endpoint": "https://example.invalid/push/тестовая-подписка",
    "keys": {"p256dh": "BN" + "A" * 85, "auth": "B" * 22},
}


async def scenario():
    # Гоша регистрируется первым и заводит группу с Леной: общего чата больше
    # нет, поэтому уведомлению нужна переписка, где они оба состоят
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as gosha,                websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as lena:
        gosha_id = await sign_in(gosha, "gosha")
        lena_id = await sign_in(lena, "lena")

        await gosha.send(protocol.group_request("Наши", [lena_id]))
        group = (await read_until(gosha, "conversation"))["item"]["id"]

        # --- клиент спрашивает публичный ключ
        await lena.send(protocol.push_key_request())
        frame = await read_until(lena, "push_key")
        check("push-key-served", frame.get("key") == push.public_key(), frame)

        # --- и подписывается
        await lena.send(protocol.push_subscribe(FAKE_SUBSCRIPTION))
        await asyncio.sleep(0.8)

    # Лена ушла из сети, Гоша пишет в общую группу
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as gosha:
        await log_in(gosha, "gosha")
        await asyncio.sleep(0.3)
        await gosha.send(protocol.text_message("Gosha", "ты где?", group))
        await asyncio.sleep(2.5)
    return lena_id


lena_id = None
try:
    lena_id = asyncio.run(scenario())
finally:
    server.terminate()
    log = server.communicate(timeout=5)[0]

check("push-subscription-saved", "подписался на уведомления" in log, log[-400:])
check("push-attempted-for-absent", "Уведомление не ушло" in log,
      "серверу не пришлось слать уведомление отсутствующему")
check("push-enabled-on-start", "Уведомления на телефон: включены" in log, log[:400])

import sqlite3  # noqa: E402
connection = sqlite3.connect(SANDBOX / "velix.db")
rows = connection.execute("SELECT user_id, data FROM pushes").fetchall()
check("push-stored-in-db", len(rows) == 1 and rows[0][0] == lena_id, rows)
check("push-stored-endpoint",
      json.loads(rows[0][1])["endpoint"] == FAKE_SUBSCRIPTION["endpoint"], rows)
connection.close()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
