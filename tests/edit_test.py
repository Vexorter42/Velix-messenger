"""Сообщение можно поправить — и только своё."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("editsandbox")
URI = "ws://localhost:8829"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8829", VELIX_OPEN_REGISTRATION="1")
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
time.sleep(2.4)


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
    лена, привет2 = await войти("lena", "Лена")

    await гоша.send(protocol.direct_request(привет2["user"]["id"]))
    беседа = None
    while беседа is None:
        список = await read_until(гоша, "conversations")
        беседа = next((one["id"] for one in список["items"]
                       if one.get("kind") == "direct"), None)

    await гоша.send(protocol.text_message("Гоша", "привет", беседа, local="l1"))
    ack = await read_until(гоша, "ack")
    номер = ack["id"]
    await read_until(лена, "text")

    # --- своё правится, и правку видят оба
    await гоша.send(protocol.edit_request(номер, "привет, как дела"))
    моя = await read_until(гоша, "edited")
    чужая = await read_until(лена, "edited")
    check("edit-author-told", моя.get("text") == "привет, как дела", моя)
    check("edit-others-told", чужая.get("id") == номер
          and чужая.get("text") == "привет, как дела", чужая)
    check("edit-stamped", bool(моя.get("edited")), моя)
    check("edit-in-right-conversation", моя.get("conversation") == беседа, моя)

    # --- в истории новый текст и пометка
    await лена.send(protocol.open_request(беседа))
    история = await read_until(лена, "history")
    запись = next(one for one in история["items"] if one.get("id") == номер)
    check("edit-history-text", запись.get("text") == "привет, как дела", запись)
    check("edit-history-marked", bool(запись.get("edited")), запись)

    # --- чужое поправить нельзя
    await лена.send(protocol.edit_request(номер, "я тут всё переписала"))
    отказ = await read_until(лена, "error")
    check("edit-only-own", отказ.get("code") == "cannot_edit", отказ)

    await лена.send(protocol.open_request(беседа))
    снова = await read_until(лена, "history")
    цело = next(one for one in снова["items"] if one.get("id") == номер)
    check("edit-others-cannot-change", цело.get("text") == "привет, как дела", цело)

    # --- пустую правку не принимаем
    await гоша.send(protocol.edit_request(номер, "   "))
    пусто = await read_until(гоша, "error")
    check("edit-refuses-empty", пусто.get("code") == "empty_edit", пусто)

    # --- вложение править нечего
    await гоша.send(protocol.media_header("Гоша", "image", "кот.png", 5, беседа))
    await гоша.send(b"\x89PNG\n")
    свежее = await read_until(лена, "media")
    await гоша.send(protocol.edit_request(свежее["id"], "подпись"))
    нельзя = await read_until(гоша, "error")
    check("edit-media-refused", нельзя.get("code") == "cannot_edit", нельзя)

    await гоша.close()
    await лена.close()


try:
    asyncio.run(проверки())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
