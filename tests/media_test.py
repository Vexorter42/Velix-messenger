"""Сервер и протокол: текст, вложения, история, лимиты."""

import asyncio
import io
from contextlib import asynccontextmanager
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("mediasandbox")
URI = "ws://localhost:8765"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402
import storage  # noqa: E402

storage_limit = storage.HISTORY_LIMIT

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


def fresh_sandbox():
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir()
    for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py"):
        shutil.copy(REPO / name, SANDBOX / name)


def png_bytes(color, size=(64, 48)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def gif_bytes(frames=3):
    images = [Image.new("RGB", (32, 32), (i * 60, 40, 200)) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, "GIF", save_all=True, append_images=images[1:], duration=80, loop=0)
    return buffer.getvalue()


_users = set()

# Общего чата больше нет: все проверки идут в одной группе. Её заводит
# «хозяин», он же зовёт туда каждого нового участника.
OWNER = "keeper"
_room = {"id": None}


async def read_until(websocket, kind, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(websocket.recv(),
                                                       timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def enter(websocket, login):
    """Вход или регистрация. Возвращает номер человека."""
    if login in _users:
        await websocket.send(protocol.login_message(login, "пароль123"))
    else:
        await websocket.send(protocol.register_message(login, "пароль123", login))
        _users.add(login)
    welcome = await read_until(websocket, "welcome")
    return welcome["user"]["id"]


async def invite(user_ids):
    """Зовёт людей в общую группу, заводя её при первом вызове."""
    websocket = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    try:
        owner_id = await enter(websocket, OWNER)
        if _room["id"] is None:
            await websocket.send(protocol.group_request("Общая", list(user_ids)))
            _room["id"] = (await read_until(websocket, "conversation"))["item"]["id"]
        else:
            await websocket.send(protocol.members_request(_room["id"], list(user_ids)))
            await asyncio.sleep(0.6)
        return owner_id
    finally:
        await websocket.close()


@asynccontextmanager
async def session(login):
    """Подключение с выполненным входом и местом в общей группе.

    Отдаёт (сокет, история группы).
    """
    websocket = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    try:
        user_id = await enter(websocket, login)
        await read_until(websocket, "people")

        await invite([user_id])
        await websocket.send(protocol.open_request(_room["id"]))
        history = await read_until(websocket, "history")
        yield websocket, history
    finally:
        await websocket.close()


async def collect(websocket, timeout=1.0):
    frames = []
    try:
        while True:
            frames.append(await asyncio.wait_for(websocket.recv(), timeout=timeout))
    except asyncio.TimeoutError:
        pass
    return frames


async def scenario():
    picture = png_bytes((200, 60, 60))
    animation = gif_bytes()

    async with session("gosha") as (alice, history):
        check("media-history-frame", history["type"] == "history" and history["items"] == [],
              history)

        async with session("bob") as (bob, _):
            await collect(bob, 0.6)

            # --- текст доходит
            await alice.send(protocol.text_message("Гоша", "привет", _room["id"]))
            frame = await read_until(bob, "text", 5)
            check("media-text-broadcast",
                  frame["type"] == "text" and frame["nick"] == "gosha"
                  and frame["text"] == "привет" and "at" in frame, frame)

            # --- картинка: описание рассылается, содержимое по запросу
            await alice.send(protocol.media_header("Гоша", "image", "кот.png", len(picture), _room["id"]))
            await alice.send(picture)

            frame = await read_until(bob, "media", 6)
            check("media-image-header",
                  frame["type"] == "media" and frame["kind"] == "image"
                  and frame["name"] == "кот.png" and frame["size"] == len(picture)
                  and frame.get("media"), frame)
            media_id = frame["media"]

            check("media-no-bytes-pushed", not (await collect(bob, 0.6)),
                  "содержимое прилетело без запроса")

            await bob.send(protocol.fetch_request(media_id))
            header = await read_until(bob, "blob", 6)
            payload = await asyncio.wait_for(bob.recv(), timeout=5)
            check("media-fetch-header",
                  header["type"] == "blob" and header["id"] == media_id
                  and header["name"] == "кот.png", header)
            check("media-fetch-bytes", payload == picture,
                  f"пришло {len(payload)} байт вместо {len(picture)}")

            # --- гифка распознаётся по расширению
            await alice.send(protocol.media_header("Гоша", "image", "пляска.gif", len(animation), _room["id"]))
            await alice.send(animation)
            frame = await read_until(bob, "media", 6)
            check("media-gif-kind", frame["kind"] == "gif", frame)

            # --- вид вложения сервер определяет сам, а не верит клиенту
            await alice.send(protocol.media_header("Гоша", "video", "обман.png", len(picture), _room["id"]))
            await alice.send(picture)
            frame = await read_until(bob, "media", 6)
            check("media-kind-not-trusted", frame["kind"] == "image", frame)

            # --- путь из имени файла вырезается
            await alice.send(protocol.media_header("Гоша", "image",
                                                   r"C:\Windows\System32\секрет.png",
                                                   len(picture), _room["id"]))
            await alice.send(picture)
            frame = await read_until(bob, "media", 6)
            check("media-filename-sanitised", frame["name"] == "секрет.png", frame)

            # --- запрос несуществующего вложения
            await bob.send(protocol.fetch_request("нет-такого"))
            frame = await read_until(bob, "error", 5)
            check("media-fetch-missing", frame["type"] == "error", frame)

            # --- клиент прошлой версии: проверяем на этапе входа, где он
            # и упрётся, ведь про аккаунты он ничего не знает
            async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as old_client:
                await old_client.send("[Гоша]: я из прошлой версии")
                frame = protocol.decode(await asyncio.wait_for(old_client.recv(), timeout=5))
                check("media-old-client-told",
                      frame["type"] == "error" and "устарел" in frame["text"], frame)

                await old_client.send(protocol.text_message("Гоша", "а я из версии 2"))
                frame = protocol.decode(await asyncio.wait_for(old_client.recv(), timeout=5))
                check("media-v2-client-told",
                      frame["type"] == "authfail" and "обновите" in frame["text"], frame)

    # --- история отдаёт и текст, и вложения
    async with session("carol") as (carol, history):
        items = history["items"]
        kinds = [item["kind"] for item in items]
        check("media-history-text", items[0]["kind"] == "text" and items[0]["text"] == "привет",
              items[0])
        check("media-history-kinds", kinds == ["text", "image", "gif", "image", "image"], kinds)
        check("media-history-has-id", all(item.get("media") for item in items if item["kind"] != "text"),
              items)

        # содержимое из истории тоже забирается
        first_media = next(item for item in items if item["kind"] == "image")
        await carol.send(protocol.fetch_request(first_media["media"]))
        header = await read_until(carol, "blob", 6)
        payload = await asyncio.wait_for(carol.recv(), timeout=5)
        check("media-history-fetch", header["type"] == "blob" and payload == picture,
              header)


async def oversize_scenario():
    """Файл больше лимита сервер отклоняет, но связь не рвёт."""
    async with session("big_sender") as (ws, _):
        big = b"\x00" * (protocol.MAX_MEDIA_SIZE + 1024)
        await ws.send(protocol.media_header("Гоша", "image", "огромный.png", len(big)))
        await ws.send(big)
        frame = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=15))
        check("media-oversize-rejected", frame["type"] == "error" and "больше" in frame["text"],
              frame)

        await ws.send(protocol.text_message("Гоша", "связь жива", _room["id"]))
        await asyncio.sleep(0.4)
        check("media-alive-after-oversize", ws.state.name == "OPEN", ws.state)


async def limits_scenario():
    """Лимит истории, порядок и устойчивость рассылки."""
    async with session("spammer") as (sender, _):
        for index in range(60):
            await sender.send(protocol.text_message("Спамер", f"строка {index}", _room["id"]))
        await asyncio.sleep(2.0)

    async with session("reader") as (reader, history):
        items = history["items"]
        check("media-history-limit", len(items) == storage_limit,
              f"пришло {len(items)} записей")
        check("media-history-order", items[-1].get("text") == "строка 59",
              items[-1])

    # мёртвый получатель не ломает рассылку живому
    victim_cm = session("victim")
    victim, _ = await victim_cm.__aenter__()
    async with session("gosha") as (sender, _), session("survivor") as (survivor, _):
        await victim_cm.__aexit__(None, None, None)
        await sender.send(protocol.text_message("Гоша", "после обрыва", _room["id"]))
        # Обрыв соседа сам по себе шлёт кадр присутствия — ждём именно сообщение
        frame = {}
        while frame.get("type") != "text":
            frame = protocol.decode(await asyncio.wait_for(survivor.recv(), timeout=5))
        check("media-dead-peer", frame.get("text") == "после обрыва", frame)

    # подключения и отключения прямо во время рассылки
    async with session("gosha") as (sender, _), session("watcher") as (watcher, _):

        async def churn():
            for churn_index in range(30):
                churn_cm = session(f"churn{churn_index}")
                ws, _history = await churn_cm.__aenter__()
                await asyncio.sleep(0.005)
                await churn_cm.__aexit__(None, None, None)

        async def spam():
            for index in range(40):
                await sender.send(protocol.text_message("Гоша", f"msg{index}", _room["id"]))
                await asyncio.sleep(0.005)

        await asyncio.gather(churn(), spam())
        # Считаем только сами сообщения: приходы и уходы дают ещё и присутствие
        received = sum(1 for frame in await collect(watcher, 1.2)
                       if isinstance(frame, str)
                       and protocol.decode(frame).get("type") == "text")
        check("media-churn", received == 40, f"получено {received} из 40")


fresh_sandbox()
server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.8)
try:
    asyncio.run(scenario())
    asyncio.run(oversize_scenario())
    asyncio.run(limits_scenario())

    # перезапускаем сервер: история должна остаться на месте
    server.terminate()
    server.wait(timeout=5)
    time.sleep(0.5)
    server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)

    async def after_restart():
        async with session("gosha") as (ws, history):
            kinds = [item["kind"] for item in history["items"]]
            check("media-survives-restart", len(history["items"]) == storage_limit
                  and "text" in kinds, kinds[:5])

    asyncio.run(after_restart())

    # Считаем только вложения: рядом с ними сервер держит папку для
    # недокачанного, и она в счёт не идёт
    files = [one for one in (SANDBOX / "media").glob("*") if one.is_file()]
    check("media-files-on-disk", len(files) == 4, [f.name for f in files])
    check("media-db-exists", (SANDBOX / "velix.db").exists())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
