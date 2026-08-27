"""Проверка ограничения по имени хоста.

Рукопожатие собираем вручную: так можно подставить любой заголовок Host,
не полагаясь на DNS и не получая второй Host от клиентской библиотеки.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


def start_server(allowed):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if allowed is None:
        env.pop("VELIX_ALLOWED_HOSTS", None)
    else:
        env["VELIX_ALLOWED_HOSTS"] = allowed
    for junk in ("velix.db", "velix.db-wal", "velix.db-shm"):
        (REPO / junk).unlink(missing_ok=True)
    process = subprocess.Popen([sys.executable, "server.py"], cwd=REPO, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    return process


async def handshake(host_lines):
    """Отправляет рукопожатие с заданными строками Host, возвращает код ответа."""
    reader, writer = await asyncio.open_connection("127.0.0.1", 8765)
    request = ["GET / HTTP/1.1"] + host_lines + [
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
        "", "",
    ]
    writer.write("\r\n".join(request).encode())
    await writer.drain()
    status_line = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    return int(status_line.split()[1])


def code(host_lines):
    return asyncio.run(handshake(host_lines))


# --- Список задан: пускаем только по нужным именам
server = start_server("velix.vexorter.duckdns.org,localhost")
try:
    check("host-allowed-domain",
          code(["Host: velix.vexorter.duckdns.org:8765"]) == 101)
    check("host-allowed-domain-no-port",
          code(["Host: velix.vexorter.duckdns.org"]) == 101)
    check("host-allowed-case",
          code(["Host: VELIX.Vexorter.DuckDNS.org:8765"]) == 101)
    check("host-rejected-wildcard",
          code(["Host: chto-ugodno.vexorter.duckdns.org:8765"]) == 403)
    check("host-rejected-parent-domain",
          code(["Host: vexorter.duckdns.org:8765"]) == 403)
    check("host-rejected-bare-ip",
          code(["Host: 85.234.9.155:8765"]) == 403)
    check("host-rejected-empty",
          code(["Host: "]) == 403)
    check("host-rejected-missing",
          code([]) == 403)
    check("host-rejected-double",
          code(["Host: velix.vexorter.duckdns.org", "Host: evil.example.org"]) == 403)

    # Настоящий клиент по разрешённому имени работает целиком
    async def real_client():
        async with websockets.connect("ws://localhost:8765") as ws:
            await ws.send("[Гоша]: проверка")
            await asyncio.sleep(0.3)
        return True
    check("host-real-client-works", asyncio.run(real_client()))
finally:
    server.terminate()
    server.wait(timeout=5)
    time.sleep(0.5)

# --- Список пуст: поведение как раньше, пускаем всех
server = start_server(None)
try:
    check("host-open-by-default", code(["Host: 85.234.9.155:8765"]) == 101)
    check("host-open-any-name", code(["Host: chto-ugodno.example.org"]) == 101)
finally:
    server.terminate()
    server.wait(timeout=5)

for junk in ("velix.db", "velix.db-wal", "velix.db-shm"):
    (REPO / junk).unlink(missing_ok=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
