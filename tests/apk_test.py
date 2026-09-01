"""Сервер раздаёт приложение для телефона так же, как сборку для окна."""

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
SANDBOX = Path(__file__).with_name("apksandbox")
URI = "ws://localhost:8841"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8841", VELIX_OPEN_REGISTRATION="1")
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
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

# Кладём «приложение» руками — так же, как это делает publish-update.sh
ПРИЛОЖЕНИЕ = bytes(range(256)) * 300          # ~77 КБ
updates = SANDBOX / "updates"
updates.mkdir()
(updates / "Velix.apk").write_bytes(ПРИЛОЖЕНИЕ)
(updates / "apk-version.txt").write_text("9.9.9\n", encoding="utf-8")

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
harness.дождаться(8841)


async def read_until(ws, kind, timeout=20):
    предел = asyncio.get_running_loop().time() + timeout
    while True:
        осталось = предел - asyncio.get_running_loop().time()
        кадр = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                      timeout=max(осталось, 0.1)))
        if кадр and кадр.get("type") == kind:
            return кадр


async def проверки():
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
        await ws.send(protocol.register_message("gosha", "parol12345", "Гоша"))
        привет = await read_until(ws, "welcome")

        предложение = привет.get("apk")
        check("apk-offered-in-welcome", bool(предложение), привет.keys())
        check("apk-version-told",
              предложение and предложение.get("version") == "9.9.9", предложение)
        check("apk-size-told",
              предложение and предложение.get("size") == len(ПРИЛОЖЕНИЕ),
              предложение)
        check("apk-not-confused-with-exe", привет.get("update") is None,
              привет.get("update"))

        # --- сам файл едет кусками, как вложение
        await ws.send(protocol.apk_request())
        шапка = await read_until(ws, "apk_blob")
        сколько = max(1, int(шапка.get("parts") or 1))
        куски = []
        while len(куски) < сколько:
            куски.append(await asyncio.wait_for(ws.recv(), timeout=30))
        приехало = b"".join(куски)

        check("apk-header-version", шапка.get("version") == "9.9.9", шапка)
        check("apk-bytes-match", приехало == ПРИЛОЖЕНИЕ,
              f"пришло {len(приехало)} из {len(ПРИЛОЖЕНИЕ)}")

        # --- связь после этого жива
        await ws.send(protocol.encode({"type": "people"}))
        люди = await read_until(ws, "people")
        check("apk-connection-alive", bool(люди.get("items")), люди)


try:
    asyncio.run(проверки())
finally:
    server.terminate()
    server.wait(timeout=5)

# ------------------------------------------- сравнение версий на телефоне

главная = (REPO / "android/java/org/vexorter/velix/MainActivity.java").read_text(
    encoding="utf-8")
check("apk-phone-compares-versions", "isNewer(свежая, appVersion())" in главная,
      "телефон не сравнивает версию")
check("apk-phone-installs", "PackageInstaller" in главная,
      "телефон не умеет ставить")
манифест = (REPO / "android/AndroidManifest.xml").read_text(encoding="utf-8")
check("apk-phone-allowed-to-install",
      "REQUEST_INSTALL_PACKAGES" in манифест, "нет разрешения на установку")

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
