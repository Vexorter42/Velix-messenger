"""Проверка боевого сервера через домен — только чтение.

Ничего не пишет в чат и ни под кем не входит: подключается, убеждается,
что сертификат настоящий, что протокол отвечает и что веб-клиент раздаётся.
"""
import asyncio
import http.client
import ssl
import sys
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import protocol  # noqa: E402

HOST = "velix.vexorter.duckdns.org"
PORT = 8765
URI = f"wss://{HOST}:{PORT}"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


async def probe():
    # Сертификат проверяем по-настоящему: без ssl=None websockets не стал бы
    # ругаться на подделку
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE,
                                  open_timeout=15) as ws:
        check("domain-tls-handshake", True)

        # Без входа сервер обязан отказать. Сообщение при этом не сохраняется.
        await ws.send(protocol.text_message("проверка", "проверка связи"))
        answer = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=15))
        check("domain-requires-login", answer["type"] == "authfail", answer)
        check("domain-protocol-version", answer.get("v") == protocol.VERSION,
              answer.get("v"))


def fetch(path):
    """Одна страница за одно соединение: сервер не держит HTTP открытым."""
    connection = http.client.HTTPSConnection(HOST, PORT, timeout=15,
                                             context=ssl.create_default_context())
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def web_probe():
    status, body = fetch("/")
    check("domain-serves-web", status == 200 and "Velix" in body,
          f"{status}, {len(body)} байт")

    status, manifest = fetch("/manifest.webmanifest")
    check("domain-serves-manifest", status == 200 and "icons" in manifest, status)


try:
    asyncio.run(probe())
    web_probe()
except Exception as error:
    check("domain-reachable", False, error)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
