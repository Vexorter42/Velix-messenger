"""Вкладка «медиа»: сервер отдаёт все вложения переписки, окно их рисует."""

import asyncio
import io as bytes_io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

import websockets
from PIL import Image

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
SANDBOX = Path(__file__).with_name("galtabsandbox")
URI = "ws://localhost:8837"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8837", VELIX_OPEN_REGISTRATION="1")
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
harness.дождаться(8837)


def снимок(цвет):
    холст = bytes_io.BytesIO()
    Image.new("RGB", (120, 90), цвет).save(холст, "PNG")
    return холст.getvalue()


async def read_until(ws, kind, timeout=20):
    предел = asyncio.get_running_loop().time() + timeout
    while True:
        осталось = предел - asyncio.get_running_loop().time()
        кадр = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                      timeout=max(осталось, 0.1)))
        if кадр and кадр.get("type") == kind:
            return кадр


async def проверки():
    гоша = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await гоша.send(protocol.register_message("gosha", "parol12345", "Гоша"))
    привет = await read_until(гоша, "welcome")

    лена = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await лена.send(protocol.register_message("lena", "parol12345", "Лена"))
    привет_л = await read_until(лена, "welcome")

    await гоша.send(protocol.group_request("Поход", [привет_л["user"]["id"]]))
    группа = (await read_until(гоша, "conversation"))["item"]["id"]

    # Три вложения и текст между ними
    for номер, цвет in enumerate([(200, 90, 60), (60, 140, 200), (90, 190, 110)]):
        снимка = снимок(цвет)
        await гоша.send(protocol.media_header("Гоша", "image",
                                              f"кадр{номер}.png", len(снимка),
                                              группа))
        await гоша.send(снимка)
        await read_until(гоша, "ack")
        await гоша.send(protocol.text_message("Гоша", f"просто текст {номер}",
                                              группа))
        await read_until(гоша, "ack")

    # --- вкладка отдаёт только вложения, от свежих к старым
    await лена.send(protocol.gallery_request(группа))
    вложения = await read_until(лена, "gallery")
    виды = [one.get("kind") for one in вложения["items"]]
    имена = [one.get("name") for one in вложения["items"]]
    check("galtab-only-media", виды == ["image", "image", "image"], виды)
    check("galtab-newest-first",
          имена == ["кадр2.png", "кадр1.png", "кадр0.png"], имена)
    check("galtab-has-ids", all(one.get("media") for one in вложения["items"]),
          вложения["items"])
    check("galtab-right-conversation", вложения.get("conversation") == группа,
          вложения)

    # --- удалённое во вкладку не попадает
    await гоша.send(protocol.gallery_request(группа))
    свои = await read_until(гоша, "gallery")
    сотрём = свои["items"][0]["id"]
    await гоша.send(protocol.delete_request(сотрём))
    await read_until(гоша, "deleted")

    await лена.send(protocol.gallery_request(группа))
    осталось = await read_until(лена, "gallery")
    check("galtab-skips-deleted", len(осталось["items"]) == 2,
          [one.get("name") for one in осталось["items"]])

    # --- в чужую переписку не заглянуть
    посторонний = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await посторонний.send(protocol.register_message("pablo", "parol12345", "Pablo"))
    await read_until(посторонний, "welcome")
    await посторонний.send(protocol.gallery_request(группа))
    отказ = await read_until(посторонний, "error")
    check("galtab-guards-access", отказ.get("code") == "no_access", отказ)

    for ws in (гоша, лена, посторонний):
        await ws.close()


try:
    asyncio.run(проверки())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
