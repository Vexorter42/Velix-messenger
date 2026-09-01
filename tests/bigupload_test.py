"""Большое вложение доезжает и остаётся на сервере.

Та самая беда с малины: недокачанное копилось в /tmp, а /tmp там — это
оперативная память, отдельная файловая система. Готовый файл переезжал на
карту через Path.replace, тот падал с «invalid cross-device link», ошибка
рвала соединение — клиент видел «нет сети» на сотне процентов, а видео
пропадало.
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

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
SANDBOX = Path(__file__).with_name("bigsandbox")
URI = "ws://localhost:8827"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8827", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402
import storage  # noqa: E402

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

МЕДИА = SANDBOX / "media"
ENV["VELIX_MEDIA"] = str(МЕДИА)

# ------------------------------------------- переезд между файловыми системами

asyncio.run(storage.init(SANDBOX / "probe.db", МЕДИА))

check("upload-dir-near-media",
      storage.upload_dir().stat().st_dev == МЕДИА.stat().st_dev,
      "недокачанное лежит на другой файловой системе — переезд упадёт")

# Притворяемся, что переименование невозможно: ровно это и делает /tmp
временный = storage.upload_dir() / "velix-upload-proba"
временный.write_bytes(b"\x00" * 4096)
настоящий = Path.replace


def падает(self, куда):
    raise OSError(18, "Invalid cross-device link")


Path.replace = падает
try:
    номер, media_id, когда = storage._save_media_file_sync(
        1, "Гоша", "video", "proba.mp4", временный, 4096, 1, None)
    лежит = list(МЕДИА.glob(media_id + "*"))
    check("upload-survives-cross-device", bool(лежит) and лежит[0].stat().st_size == 4096,
          лежит)
except Exception as беда:
    check("upload-survives-cross-device", False, беда)
finally:
    Path.replace = настоящий

# ----------------------------------------------------- уборка огрызков

старый = storage.upload_dir() / "velix-upload-staryj"
старый.write_bytes(b"x")
os.utime(старый, (time.time() - 86400, time.time() - 86400))
свежий = storage.upload_dir() / "velix-upload-svezhij"
свежий.write_bytes(b"x")

убрано = storage.forget_stale_uploads()
check("upload-stale-swept", убрано >= 1 and not старый.exists(), убрано)
check("upload-fresh-kept", свежий.exists(), "свежую загрузку унесло вместе с мусором")
свежий.unlink()

# --------------------------------------------------- живая отправка кусками

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=open(SANDBOX / "log.txt", "w", encoding="utf-8"),
                          stderr=subprocess.STDOUT)
harness.дождаться(8827)

РОЛИК = bytes(range(256)) * 40000        # ~10 МБ, три куска по 4


async def read_until(ws, kind, timeout=60):
    предел = asyncio.get_running_loop().time() + timeout
    while True:
        осталось = предел - asyncio.get_running_loop().time()
        кадр = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                      timeout=max(осталось, 0.1)))
        if кадр and кадр.get("type") == kind:
            return кадр
        if кадр and кадр.get("type") in ("error", "authfail"):
            raise AssertionError(f"сервер отказал: {кадр}")


async def отправка():
    # Собеседник нужен, чтобы было куда слать: общего чата в Velix нет
    сосед = await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)
    await сосед.send(protocol.register_message("lena", "parol12345", "Лена"))
    привет = await read_until(сосед, "welcome")

    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
        await ws.send(protocol.register_message("gosha", "parol12345", "Гоша"))
        await read_until(ws, "welcome")

        await ws.send(protocol.direct_request(привет["user"]["id"]))
        # Список переписок приходит и при входе — ждём тот, где уже есть личная
        беседа = None
        while беседа is None:
            список = await read_until(ws, "conversations")
            беседа = next((one["id"] for one in список["items"]
                           if one.get("kind") == "direct"), None)

        await ws.send(protocol.upload_request("kino.mp4", len(РОЛИК),
                                              conversation=беседа, local="l1"))
        готово = await read_until(ws, "upload_ready")
        кусок = int(готово.get("chunk") or protocol.CHUNK_SIZE)

        for место in range(0, len(РОЛИК), кусок):
            await ws.send(protocol.chunk_header(готово["ticket"]))
            await ws.send(РОЛИК[место:место + кусок])

        ответ = await read_until(ws, "ack")
        check("upload-acked", bool(ответ.get("media")), ответ)

        лежит = list(МЕДИА.glob(ответ["media"] + "*"))
        check("upload-file-landed",
              bool(лежит) and лежит[0].stat().st_size == len(РОЛИК),
              [(one.name, one.stat().st_size) for one in лежит])
        check("upload-temp-cleaned",
              not list((МЕДИА / ".uploads").glob("velix-upload-*")),
              list((МЕДИА / ".uploads").glob("velix-upload-*")))

        # Связь после отправки жива: сервер отвечает дальше
        await ws.send(protocol.encode({"type": "people"}))
        люди = await read_until(ws, "people")
        check("upload-connection-alive", bool(люди.get("items")), люди)
    await сосед.close()


try:
    asyncio.run(отправка())
except Exception as беда:
    check("upload-acked", False, беда)
finally:
    server.terminate()
    server.wait(timeout=5)

крах = (SANDBOX / "log.txt").read_text(encoding="utf-8", errors="replace")
check("upload-no-traceback", "Traceback" not in крах,
      [строка for строка in крах.splitlines() if "Error" in строка][:3])

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
