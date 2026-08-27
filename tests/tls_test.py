"""Шифрование: разбор адреса, сервер по TLS, откат на открытый канал."""

import asyncio
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("tlssandbox")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", VELIX_PORT="8767")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import client as console  # noqa: E402
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402  (только ради разбора адреса)
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------------ разбор адреса

cases = [
    ("velix.example.org", ["wss://velix.example.org:8765", "ws://velix.example.org:8765"]),
    ("velix.example.org:9000", ["wss://velix.example.org:9000", "ws://velix.example.org:9000"]),
    ("wss://velix.example.org", ["wss://velix.example.org:8765"]),
    ("ws://velix.example.org", ["ws://velix.example.org:8765"]),
    ("wss://velix.example.org:9000", ["wss://velix.example.org:9000"]),
    ("", ["wss://localhost:8765", "ws://localhost:8765"]),
    ("192.168.0.225", ["wss://192.168.0.225:8765", "ws://192.168.0.225:8765"]),
    ("[::1]:9000", ["wss://[::1]:9000", "ws://[::1]:9000"]),
]
for source, expected in cases:
    got_gui = gui.connection_uris(source)
    got_console = console.connection_uris(source)
    check(f"uri {source!r}", got_gui == expected and got_console == expected,
          f"{got_gui} / {got_console}")

check("secure-first", gui.connection_uris("host")[0].startswith("wss://"))

# ------------------------------------------------------------ сервер по TLS

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)

# самоподписанный сертификат для проверки
subprocess.run([
    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", str(SANDBOX / "key.pem"), "-out", str(SANDBOX / "cert.pem"),
    "-days", "2", "-subj", "/CN=localhost",
    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
], check=True, capture_output=True)
check("cert-created", (SANDBOX / "cert.pem").exists() and (SANDBOX / "key.pem").exists())


def start_server(with_tls):
    env = dict(ENV)
    if with_tls:
        env["VELIX_CERT"] = str(SANDBOX / "cert.pem")
        env["VELIX_KEY"] = str(SANDBOX / "key.pem")
    process = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    time.sleep(2.2)
    return process


def trusting_context():
    """Клиент, который доверяет нашему самодельному сертификату."""
    context = ssl.create_default_context(cafile=str(SANDBOX / "cert.pem"))
    return context


server = start_server(with_tls=True)
try:
    async def over_tls():
        uri = "wss://localhost:8767"
        async with websockets.connect(uri, ssl=trusting_context(),
                                      max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("tlsuser", "пароль123", "Шифр"))
            welcome = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=15))
            check("tls-login-works", welcome["type"] == "welcome", welcome)
            version = ws.transport.get_extra_info("ssl_object").version()
            check("tls-modern-version", version in ("TLSv1.2", "TLSv1.3"), version)
            print(f"   для справки: соединение по {version}")

    asyncio.run(over_tls())

    async def plain_rejected():
        """По открытому ws:// на TLS-порт зайти не выйдет."""
        try:
            async with websockets.connect("ws://localhost:8767",
                                          max_size=protocol.MAX_FRAME_SIZE):
                check("plain-rejected-on-tls", False, "пустил без шифрования")
        except Exception as error:
            check("plain-rejected-on-tls", True, error.__class__.__name__)

    asyncio.run(plain_rejected())
finally:
    server.terminate()
    server.wait(timeout=5)
    time.sleep(0.5)

# ------------------------------------------------- откат, когда TLS нет

server = start_server(with_tls=False)
try:
    output_warned = []

    async def fallback():
        uris = console.connection_uris("localhost:8767")
        opened = None
        for uri in uris:
            try:
                opened = await websockets.connect(uri, max_size=protocol.MAX_FRAME_SIZE)
                output_warned.append(uri)
                break
            except Exception:
                continue
        check("fallback-connects", opened is not None, uris)
        check("fallback-uses-plain", output_warned and output_warned[0].startswith("ws://"),
              output_warned)
        if opened is not None:
            await opened.close()

    asyncio.run(fallback())
finally:
    server.terminate()
    log = server.communicate(timeout=5)[0]
    check("server-warns-without-tls", "шифрования нет" in log, log[-200:])

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
