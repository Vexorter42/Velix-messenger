"""Сервер отличает голос и кружочек от обычного файла — но не на слово.

По расширению голосовое сообщение от присланной песни не отличить: и то и
другое .ogg. Поэтому вид объявляет тот, кто записывал, а сервер смотрит, не
спорит ли объявленное с расширением: назвать «кружочком» архив не выйдет.
"""

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
SANDBOX = Path(__file__).with_name("voicesandbox")
URI = "ws://localhost:8843"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8843", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# --------------------------------------------------- разбор без сервера

check("kind-voice-accepted",
      protocol.claimed_kind("voice-1.ogg", "voice") == "voice")
check("kind-circle-accepted",
      protocol.claimed_kind("circle-1.mp4", "circle") == "circle")
check("kind-voice-refused-for-a-film",
      protocol.claimed_kind("кино.mkv", "voice") == "video",
      protocol.claimed_kind("кино.mkv", "voice"))
check("kind-circle-refused-for-an-archive",
      protocol.claimed_kind("архив.zip", "circle") == "file",
      protocol.claimed_kind("архив.zip", "circle"))
check("kind-plain-song-stays-a-file",
      protocol.claimed_kind("песня.mp3", None) == "file")

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
harness.дождаться(8843)

ГОЛОС = b"OggS" + bytes(range(256)) * 20
КРУЖОК = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40


async def read_until(ws, kind, timeout=25):
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
    гоша, _ = await войти("gosha", "Гоша")
    лена, привет = await войти("lena", "Лена")
    try:
        await гоша.send(protocol.direct_request(привет["user"]["id"]))
        беседа = None
        while беседа is None:
            список = await read_until(гоша, "conversations")
            беседа = next((one["id"] for one in список["items"]
                           if one.get("kind") == "direct"), None)

        # ------------------------------------------------------- голос
        await гоша.send(protocol.media_header("Гоша", "voice", "voice-1.ogg",
                                              len(ГОЛОС), беседа, None, "l1", 7))
        await гоша.send(ГОЛОС)
        пришло = await read_until(лена, "media")
        check("voice-kind-kept", пришло.get("kind") == "voice", пришло)
        check("voice-seconds-kept", пришло.get("seconds") == 7, пришло)
        голос_id = пришло.get("media")

        # ------------------------------------------------------ кружочек
        await гоша.send(protocol.media_header("Гоша", "circle", "circle-1.mp4",
                                              len(КРУЖОК), беседа, None, "l2", 12))
        await гоша.send(КРУЖОК)
        кружок = await read_until(лена, "media")
        check("circle-kind-kept", кружок.get("kind") == "circle", кружок)
        check("circle-seconds-kept", кружок.get("seconds") == 12, кружок)

        # ------------------------------- на слово не верим и на сервере
        await гоша.send(protocol.media_header("Гоша", "circle", "письмо.txt",
                                              len(ГОЛОС), беседа, None, "l3"))
        await гоша.send(ГОЛОС)
        подделка = await read_until(лена, "media")
        check("server-refuses-a-fake-circle", подделка.get("kind") == "file",
              подделка)

        # --------------------------------------- в истории всё на месте
        await лена.send(protocol.open_request(беседа))
        история = await read_until(лена, "history")
        голосовые = [one for one in история["items"] if one.get("kind") == "voice"]
        кружочки = [one for one in история["items"] if one.get("kind") == "circle"]
        check("voice-in-history", len(голосовые) == 1, история["items"])
        check("voice-history-keeps-seconds",
              голосовые and голосовые[0].get("seconds") == 7, голосовые)
        check("circle-in-history", len(кружочки) == 1, кружочки)

        # ------------------------- в «медиа» кружочек есть, голоса нет
        await лена.send(protocol.gallery_request(беседа))
        полка = await read_until(лена, "gallery")
        виды = [one.get("kind") for one in полка.get("items", [])]
        check("gallery-has-the-circle", "circle" in виды, виды)
        check("gallery-has-no-voice", "voice" not in виды, виды)

        # --------------------------------- содержимое забирается как всегда
        await лена.send(protocol.fetch_request(голос_id))
        шапка = await read_until(лена, "blob")
        байты = await asyncio.wait_for(лена.recv(), timeout=25)
        check("voice-bytes-come-back",
              isinstance(байты, (bytes, bytearray)) and bytes(байты) == ГОЛОС,
              (шапка, len(байты) if байты else 0))
    finally:
        await гоша.close()
        await лена.close()


try:
    asyncio.run(проверки())
finally:
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(SANDBOX, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
