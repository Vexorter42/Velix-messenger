"""Большие вложения: приём кусками, раздача кусками, пределы."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("uploadsandbox")
URI = "ws://localhost:8797"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8797", VELIX_OPEN_REGISTRATION="1", VELIX_ADMIN="gosha")
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
time.sleep(2.2)

# Видео на 9 МБ: три куска по четыре и остаток
ВИДЕО = bytes(range(256)) * (9 * 1024 * 1024 // 256)


async def read_until(ws, kind, timeout=30, **fields):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = await asyncio.wait_for(ws.recv(), timeout=max(left, 0.1))
        if isinstance(frame, (bytes, bytearray)):
            continue
        decoded = protocol.decode(frame)
        if decoded is None or decoded.get("type") != kind:
            continue
        if all(decoded.get(key) == value for key, value in fields.items()):
            return decoded


async def sign_up(login, name):
    ws = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message(login, "parol12345", name))
    welcome = await read_until(ws, "welcome")
    return ws, welcome


async def отправить(ws, name, data, conversation):
    """Шлёт вложение так, как это делает клиент: заявка, куски, конец."""
    await ws.send(protocol.upload_request(name, len(data), conversation,
                                          local="l1"))
    ready = await read_until(ws, "upload_ready")
    ticket, кусок = ready["ticket"], ready["chunk"]

    шагов = 0
    for начало in range(0, len(data), кусок):
        await ws.send(protocol.chunk_header(ticket))
        await ws.send(data[начало:начало + кусок])
        шагов += 1
    return ticket, шагов


async def main():
    boss, welcome = await sign_up("gosha", "Гоша")
    mate, _ = await sign_up("lena", "Лена")

    пределы = welcome.get("limits") or {}
    check("upload-limits-told", пределы.get("video") == protocol.DEFAULT_VIDEO_LIMIT
          and пределы.get("file") == protocol.DEFAULT_FILE_LIMIT, пределы)
    check("upload-chunk-told", пределы.get("chunk") == protocol.CHUNK_SIZE, пределы)

    await boss.send(protocol.group_request("Поход",
                                           [(await read_until(boss, "people"))
                                            and 2]))
    room = (await read_until(boss, "conversation"))["item"]["id"]
    await read_until(mate, "conversation", timeout=10)

    # ------------------------------------------------ большое видео
    начало = time.perf_counter()
    ticket, шагов = await отправить(boss, "поход.mp4", ВИДЕО, room)
    ack = await read_until(boss, "ack")
    ушло = time.perf_counter() - начало

    check("upload-chunks-used", шагов >= 3, шагов)
    check("upload-ack-has-media", bool(ack.get("media")), ack)
    print(f"      девять мегабайт ушли за {ушло:.2f} с, кусками: {шагов}")

    пришло = await read_until(mate, "media", timeout=20)
    check("upload-seen-by-mate", пришло.get("size") == len(ВИДЕО)
          and пришло.get("kind") == "video", пришло)

    # ------------------------------------------------ раздача кусками
    await mate.send(protocol.fetch_request(пришло["media"]))
    header = await read_until(mate, "blob")
    check("upload-blob-parts", header.get("parts") == шагов, header)

    собрано = b""
    while len(собрано) < header["size"]:
        кадр = await asyncio.wait_for(mate.recv(), timeout=30)
        if isinstance(кадр, (bytes, bytearray)):
            собрано += кадр
    check("upload-download-whole", собрано == ВИДЕО,
          f"скачано {len(собрано)} из {len(ВИДЕО)}")

    # ------------------------------------------------ предел не обойти
    await boss.send(protocol.upload_request("огромное.mp4",
                                            protocol.DEFAULT_VIDEO_LIMIT + 1, room))
    отказ = await read_until(boss, "error")
    check("upload-too-big-refused", отказ.get("code") == "file_too_big", отказ)

    # Заявили меньше, чем шлём — сервер должен прервать
    await boss.send(protocol.upload_request("хитрое.bin", 1024, room))
    ready = await read_until(boss, "upload_ready")
    await boss.send(protocol.chunk_header(ready["ticket"]))
    await boss.send(b"x" * 4096)
    отказ = await read_until(boss, "error")
    check("upload-liar-refused", отказ.get("code") == "file_too_big", отказ)

    # ------------------------------------------------ пределы из панели
    await boss.send(protocol.admin_request("limits", file=100 * 1024 * 1024,
                                           video=300 * 1024 * 1024))
    сводка = await read_until(boss, "admin")
    новые = (сводка.get("stats") or {}).get("limits") or {}
    check("upload-limits-changed", новые.get("file") == 100 * 1024 * 1024
          and новые.get("video") == 300 * 1024 * 1024, новые)

    await boss.send(protocol.upload_request("среднее.mp4", 400 * 1024 * 1024, room))
    отказ = await read_until(boss, "error")
    check("upload-new-limit-works", отказ.get("code") == "file_too_big", отказ)

    # Не хозяину чата менять пределы нельзя
    await mate.send(protocol.admin_request("limits", file=1024 * 1024 * 1024))
    отказ = await read_until(mate, "error")
    check("upload-limits-admin-only", отказ.get("code") == "not_admin", отказ)

    await boss.close()
    await mate.close()


try:
    asyncio.run(main())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
