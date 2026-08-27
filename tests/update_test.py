"""Обновление: сравнение версий, подмена файла, раздача с сервера."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("updatesandbox")
URI = "ws://localhost:8766"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", VELIX_PORT="8766")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402
import i18n
import updates  # noqa: E402
import version  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------- сравнение версий

check("version-newer", version.is_newer("1.4.0", "1.3.0"))
check("version-not-older", not version.is_newer("1.3.0", "1.4.0"))
check("version-equal", not version.is_newer("1.3.0", "1.3.0"))
check("version-minor", version.is_newer("1.3.1", "1.3.0"))
check("version-major", version.is_newer("2.0.0", "1.9.9"))
check("version-short", version.as_tuple("2") == (2, 0, 0), version.as_tuple("2"))
check("version-garbage", version.as_tuple("абв") == (0, 0, 0), version.as_tuple("абв"))
check("version-prefixed", version.as_tuple("v1.4.0") == (1, 4, 0), version.as_tuple("v1.4.0"))

# --------------------------------------------------------- подмена файла

folder = Path(tempfile.mkdtemp(prefix="velix-upd-"))
current = folder / "Velix.exe"
current.write_bytes(b"\x4d\x5a" + "старая версия".encode("utf-8") + b"\x00" * 100)
before = current.read_bytes()

problem = updates.swap(current, b"\x4d\x5a" + "новая версия".encode() + b"\x00" * 100)
check("swap-succeeds", problem is None, problem)
check("swap-replaces", b"\xd0\xbd\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x8f" in current.read_bytes(),
      "содержимое не поменялось")
retired = folder / "Velix.exe.old"
check("swap-keeps-old", retired.exists() and retired.read_bytes() == before,
      "старая версия не отложена")
check("swap-no-leftover-new", not (folder / "Velix.exe.new").exists(),
      "остался промежуточный файл")

check("cleanup-removes-old", updates.cleanup(folder) == 1)
check("cleanup-really-gone", not retired.exists())
check("cleanup-on-empty", updates.cleanup(folder) == 0)

# --- если запись невозможна, старый файл остаётся на месте
locked = Path(tempfile.mkdtemp(prefix="velix-upd2-")) / "нет" / "Velix.exe"
problem = updates.swap(locked, "данные".encode("utf-8"))
# Текст ошибки идёт на языке клиента, по умолчанию английском
check("swap-reports-problem",
      problem is not None and "could not write" in problem, problem)
i18n.set_language("ru")
check("swap-problem-in-russian",
      "не удалось" in updates.swap(locked, "данные".encode("utf-8")),
      updates.swap(locked, b"x"))
i18n.set_language("en")

# ---------------------------------------------------- раздача с сервера

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)


async def sign_in(ws, login="updater"):
    await ws.send(protocol.register_message(login, "пароль123", login))
    welcome = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=15))
    # За приветствием идут список переписок и участники; истории у новичка нет
    frame = None
    while (frame or {}).get("type") != "people":
        frame = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=10))
    return welcome, None


def start_server():
    process = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    return process


# --- без каталога обновлений сервер молчит про версии
server = start_server()
try:
    async def without_update():
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            welcome, _ = await sign_in(ws, "nobody")
            check("server-no-update-offer", "update" not in welcome, welcome.get("update"))
            await ws.send(protocol.update_request())
            answer = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=10))
            check("server-refuses-missing", answer["type"] == "error", answer)
    asyncio.run(without_update())
finally:
    server.terminate()
    server.wait(timeout=5)
    time.sleep(0.5)

# --- кладём сборку и версию
build = SANDBOX / "updates"
build.mkdir()
payload = b"\x4d\x5a" + b"P" * (3 * 1024 * 1024)
(build / "Velix.exe").write_bytes(payload)
(build / "version.txt").write_text("9.9.9\n", encoding="utf-8")

server = start_server()
try:
    async def with_update():
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            welcome, _ = await sign_in(ws, "updater")
            offer = welcome.get("update")
            check("server-offers-update", offer and offer["version"] == "9.9.9"
                  and offer["size"] == len(payload), offer)
            check("client-sees-it-as-newer", version.is_newer(offer["version"]), offer)

            await ws.send(protocol.update_request())
            header = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=20))
            check("server-sends-header", header["type"] == "update_blob"
                  and header["version"] == "9.9.9", header)

            # Сборка едет кусками, как и большое вложение
            сколько = max(1, int(header.get("parts") or 1))
            куски = []
            while len(куски) < сколько:
                куски.append(await asyncio.wait_for(ws.recv(), timeout=30))
            data = b"".join(куски)
            check("server-sends-parts",
                  сколько == max(1, -(-len(payload) // protocol.CHUNK_SIZE)),
                  f"кусков {сколько} на {len(payload)} байт")
            check("server-sends-bytes", data == payload,
                  f"пришло {len(data)} из {len(payload)} байт")
    asyncio.run(with_update())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
