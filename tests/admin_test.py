"""Панель управления, фото группы, удаление и галочки в группе."""

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
SANDBOX = Path(__file__).with_name("adminsandbox")
URI = "ws://localhost:8789"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8789")
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
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)

picture = io.BytesIO()
Image.new("RGB", (200, 200), (120, 80, 200)).save(picture, "PNG")
PHOTO = picture.getvalue()


async def read_until(ws, kind, timeout=20):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                       timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def connect():
    return await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)


async def sign_in(ws, login):
    await ws.send(protocol.register_message(login, "пароль123", login.title()))
    welcome = await read_until(ws, "welcome")
    await read_until(ws, "people")
    return welcome["user"]["id"]


async def scenario():
    async with await connect() as boss, await connect() as lena, \
            await connect() as dima:
        boss_id = await sign_in(boss, "gosha")     # первый — хозяин чата
        lena_id = await sign_in(lena, "lena")
        dima_id = await sign_in(dima, "dima")

        # --- группа на троих
        await boss.send(protocol.group_request("Поход", [lena_id, dima_id]))
        group = (await read_until(boss, "conversation"))["item"]["id"]
        await read_until(lena, "conversation")
        await read_until(dima, "conversation")
        check("admin-group-owner",
              (await read_until(boss, "history")) is not None, "истории нет")

        # --- галочки: хватает одного прочитавшего, а не всех
        await boss.send(protocol.text_message("Гоша", "выходим в семь", group,
                                              None, "l1"))
        message_id = (await read_until(boss, "ack"))["id"]
        got = await read_until(lena, "text")
        await read_until(dima, "text")

        state = None
        for _ in range(6):
            receipts = await read_until(boss, "receipts")
            state = receipts["items"].get(str(message_id), state)
            if state == "delivered":
                break
        check("ticks-delivered-in-group", state == "delivered", state)

        await lena.send(protocol.read_request(group, [got["id"]]))
        state = None
        for _ in range(6):
            receipts = await read_until(boss, "receipts")
            state = receipts["items"].get(str(message_id), state)
            if state == "read":
                break
        check("ticks-read-from-one-member", state == "read",
              f"{state}: Дима ещё не читал, но галочки должны посинеть")

        # --- фото группы
        await boss.send(protocol.group_avatar_header(group, "поход.png", len(PHOTO)))
        await boss.send(PHOTO)
        mine = await read_until(boss, "conversation")
        hers = await read_until(lena, "conversation")
        check("group-photo-set", bool(mine["item"].get("avatar")), mine)
        check("group-photo-shared", hers["item"].get("avatar")
              == mine["item"].get("avatar"), hers)

        # --- фото личной переписки не ставится
        await boss.send(protocol.direct_request(lena_id))
        listing = await read_until(boss, "conversations")
        direct = [one for one in listing["items"] if one["kind"] == "direct"][0]["id"]
        await boss.send(protocol.group_avatar_header(direct, "нет.png", len(PHOTO)))
        await boss.send(PHOTO)
        answer = await read_until(boss, "error")
        check("group-photo-direct-refused",
              answer.get("code") == "group_only_photo", answer)

        # --- сводка панели
        await boss.send(protocol.admin_request("stats"))
        stats = (await read_until(boss, "admin"))["stats"]
        check("admin-stats-users", len(stats["users"]) == 3, stats["users"])
        check("admin-stats-counts", stats["messages"] >= 1
              and stats["media_files"] >= 1, stats)
        check("admin-stats-disk", stats["disk_total"] > 0
              and stats["disk_free"] > 0, stats)
        check("admin-stats-rooms",
              any(room["title"] == "Поход" and room["members"] == 3
                  for room in stats["rooms"]), stats["rooms"])

        # --- чужому панель недоступна
        await lena.send(protocol.admin_request("stats"))
        answer = await read_until(lena, "error")
        check("admin-only-for-owner", answer.get("code") == "not_admin", answer)

        # --- удалить группу может тот, кто завёл
        await lena.send(protocol.delete_group_request(group))
        answer = await read_until(lena, "error")
        check("group-delete-needs-owner",
              answer.get("code") == "not_group_owner", answer)

        await boss.send(protocol.delete_group_request(group))
        listing = await read_until(boss, "conversations")
        check("group-deleted",
              all(one["id"] != group for one in listing["items"]), listing)
        listing = await read_until(lena, "conversations")
        check("group-deleted-for-members",
              all(one["id"] != group for one in listing["items"]), listing)

        # --- удаление человека панелью
        await boss.send(protocol.admin_request("drop_user", user=dima_id))
        stats = (await read_until(boss, "admin"))["stats"]
        check("admin-user-dropped",
              all(person["id"] != dima_id for person in stats["users"]),
              stats["users"])

        await boss.send(protocol.admin_request("drop_user", user=boss_id))
        answer = await read_until(boss, "error")
        check("admin-cannot-drop-self", answer.get("code") == "admin_self", answer)

    # --- удалённый войти не может
    async with await connect() as ghost:
        await ghost.send(protocol.login_message("dima", "пароль123"))
        answer = await read_until(ghost, "authfail")
        check("dropped-user-cannot-return",
              answer.get("code") == "bad_credentials", answer)


try:
    asyncio.run(scenario())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
