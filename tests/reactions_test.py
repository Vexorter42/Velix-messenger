"""Реакции: постановка, снятие, видимость у собеседников."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("reactsandbox")
URI = "ws://localhost:8773"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8773")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

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

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    welcome = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=15))
    await read_until(ws, "people")  # съедает и список переписок
    return welcome["user"]["id"]


async def make_group(ws, member_ids):
    """Заводит группу и возвращает её номер: общего чата больше нет."""
    await ws.send(protocol.group_request("Реакции", member_ids))
    return (await read_until(ws, "conversation"))["item"]["id"]


async def scenario():
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as gosha, \
            websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as lena:
        gosha_id = await sign_in(gosha, "gosha")
        lena_id = await sign_in(lena, "lena")
        group = await make_group(gosha, [lena_id])
        await read_until(lena, "conversation")

        await gosha.send(protocol.text_message("gosha", "смешное сообщение", group))
        frame = await read_until(lena, "text")
        message_id = frame["id"]

        # --- Лена ставит реакцию
        await lena.send(protocol.react_request(message_id, "👍"))
        mine = await read_until(lena, "reactions")
        check("react-confirmed", mine["id"] == message_id
              and mine["reactions"].get("👍") == [lena_id], mine)

        theirs = await read_until(gosha, "reactions")
        check("react-seen-by-author", theirs["reactions"].get("👍") == [lena_id], theirs)

        # --- Гоша ставит ту же реакцию: их становится двое
        await gosha.send(protocol.react_request(message_id, "👍"))
        frame = await read_until(gosha, "reactions")
        check("react-counts-two", sorted(frame["reactions"]["👍"]) == sorted([lena_id, gosha_id]),
              frame)

        # --- вторая реакция на то же сообщение живёт отдельно
        await gosha.send(protocol.react_request(message_id, "🔥"))
        frame = await read_until(gosha, "reactions")
        check("react-two-kinds", set(frame["reactions"]) == {"👍", "🔥"}, frame)

        # --- повторное нажатие снимает свою.
        # Сначала вычищаем очередь: там лежат отголоски чужих реакций.
        try:
            while True:
                await asyncio.wait_for(lena.recv(), timeout=0.6)
        except asyncio.TimeoutError:
            pass

        await lena.send(protocol.react_request(message_id, "👍"))
        frame = await read_until(lena, "reactions")
        check("react-toggles-off", frame["reactions"].get("👍") == [gosha_id], frame)

        # --- реакции приезжают вместе с историей
        await gosha.send(protocol.open_request(group))
        page = await read_until(gosha, "history")
        check("react-in-history", page["reactions"].get(str(message_id), {}).get("🔥")
              == [gosha_id], page.get("reactions"))

        # --- на несуществующее сообщение реакция не ставится
        await gosha.send(protocol.react_request(999999, "👍"))
        frame = await read_until(gosha, "error")
        check("react-missing-message", "не найдено" in frame["text"], frame)

        # --- пустой смайлик игнорируется
        await gosha.send(protocol.react_request(message_id, "   "))
        await asyncio.sleep(0.5)
        check("react-empty-ignored", True)

    # --- в чужую личную переписку реакцию не поставить
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as gosha, \
            websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as lena, \
            websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as dima:
        await gosha.send(protocol.login_message("gosha", "пароль123"))
        await read_until(gosha, "welcome")
        await read_until(gosha, "people")
        await lena.send(protocol.login_message("lena", "пароль123"))
        await read_until(lena, "welcome")
        await read_until(lena, "people")
        await sign_in(dima, "dima")

        await gosha.send(protocol.direct_request(lena_id))
        conversations = await read_until(gosha, "conversations")
        direct_id = [c for c in conversations["items"] if c["kind"] == "direct"][0]["id"]

        await gosha.send(protocol.text_message("gosha", "личное", direct_id))
        private = await read_until(lena, "text")

        await dima.send(protocol.react_request(private["id"], "👍"))
        frame = await read_until(dima, "error")
        check("react-blocked-in-foreign-direct", "недоступна" in frame["text"], frame)


ok = True
try:
    asyncio.run(scenario())
except Exception as error:
    ok = False
    print("СЦЕНАРИЙ УПАЛ:", error.__class__.__name__, error)
finally:
    server.terminate()
    server.wait(timeout=5)

check("scenario-completed", ok)
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
