"""Строчка переписки не теряет последнее сообщение и имя собеседника."""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("previewsandbox")
URI = "ws://localhost:8794"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8794", VELIX_OPEN_REGISTRATION="1")
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
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)

picture = io.BytesIO()
Image.new("RGB", (240, 180), (90, 160, 210)).save(picture, "PNG")
PHOTO = picture.getvalue()


async def read_until(ws, kind, timeout=15, **fields):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                       timeout=max(left, 0.1)))
        if frame is None or frame.get("type") != kind:
            continue
        if all(frame.get(key) == value for key, value in fields.items()):
            return frame


async def sign_up(login, name):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message(login, "parol12345", name))
    welcome = await read_until(ws, "welcome")
    return ws, welcome["user"]


async def main():
    boss, boss_me = await sign_up("gosha", "Гоша")
    mate, mate_me = await sign_up("lena", "Лена")

    # ---------------------------------------------- группа с сообщениями
    await boss.send(protocol.group_request("Поход", [mate_me["id"]]))
    made = await read_until(boss, "conversation")
    room = made["item"]["id"]

    await boss.send(protocol.text_message("Гоша", "берём палатку", room))
    await read_until(boss, "ack")

    # ---------------------------------------------- ставим фото группе
    await boss.send(protocol.group_avatar_header(room, "поход.png", len(PHOTO)))
    await boss.send(PHOTO)

    fresh = await read_until(boss, "conversation")
    item = fresh["item"]
    check("preview-photo-set", bool(item.get("avatar")), item)
    check("preview-keeps-last", (item.get("last") or {}).get("text") == "берём палатку",
          item.get("last"))
    check("preview-keeps-title", item.get("title") == "Поход", item)

    # Первым к соседке пришла сама группа — ждём именно ту, что с фото
    theirs = {}
    while not theirs.get("avatar"):
        theirs = (await read_until(mate, "conversation", timeout=10))["item"]
    check("preview-mate-keeps-last",
          (theirs.get("last") or {}).get("text") == "берём палатку", theirs)

    # ------------------------------- личная переписка: имя и вложения
    await boss.send(protocol.direct_request(mate_me["id"]))
    talk = (await read_until(boss, "history"))["conversation"]

    await boss.send(protocol.media_header("Гоша", "image", "снимок.png",
                                          len(PHOTO), talk))
    await boss.send(PHOTO)
    await read_until(boss, "ack")

    await mate.send(protocol.media_header("Лена", "image", "ответ.png",
                                          len(PHOTO), talk))
    await mate.send(PHOTO)
    await read_until(mate, "ack")

    # Уходим в группу и возвращаемся — история должна прийти целиком
    await boss.send(protocol.open_request(room))
    await read_until(boss, "history", conversation=room)
    await boss.send(protocol.open_request(talk))
    back = await read_until(boss, "history", conversation=talk)

    kinds = [one.get("kind") for one in back.get("items", [])]
    check("preview-direct-history", kinds == ["image", "image"], kinds)

    # И вложение из личной переписки должно отдаваться по запросу
    picture_id = back["items"][0].get("media")
    await boss.send(protocol.fetch_request(picture_id))
    header = await read_until(boss, "blob")
    data = await asyncio.wait_for(boss.recv(), timeout=10)
    check("preview-direct-media", isinstance(data, (bytes, bytearray))
          and len(data) > 100, (header, type(data)))

    # Список переписок: у личной — имя собеседника и её последнее вложение
    await boss.send(protocol.sync_request())
    listing = await read_until(boss, "conversations")
    личная = next((one for one in listing["items"] if one["id"] == talk), None)
    check("preview-direct-title", (личная or {}).get("title") == "Лена", личная)
    check("preview-direct-last", (личная or {}).get("last", {}).get("kind") == "image",
          (личная or {}).get("last"))

    группа = next((one for one in listing["items"] if one["id"] == room), None)
    check("preview-group-last",
          (группа or {}).get("last", {}).get("text") == "берём палатку", группа)

    await boss.close()
    await mate.close()


try:
    asyncio.run(main())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
