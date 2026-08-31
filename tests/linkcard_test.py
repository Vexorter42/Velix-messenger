"""Ссылка показывается карточкой, а сервер не ходит куда попало.

Присланный адрес открывает сервер, а не клиенты: иначе каждый, кто просто
открыл переписку, засветил бы свой адрес чужому сайту. Значит, и осторожность
на сервере: ссылка приходит от человека, а сервер стоит внутри домашней сети,
и «сходи на http://192.168.0.1» он выполнять не должен.

Сайт для проверки поднимается тут же на localhost, поэтому запрет домашних
адресов на время снимается переменной — но сначала проверяем, что без неё он
и правда работает.
"""

import asyncio
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import harness

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("cardsandbox")
URI = "ws://localhost:8849"

sys.path.insert(0, str(REPO))
import linkpreview  # noqa: E402  (пока без разрешения ходить домой)
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------- ссылку находим в тексте

check("link-found-in-a-sentence",
      linkpreview.find_link("глянь https://example.dev/страницу, там красиво")
      == "https://example.dev/страницу", linkpreview.find_link(
          "глянь https://example.dev/страницу, там красиво"))
check("link-none-when-none", linkpreview.find_link("просто слова") is None)
check("link-keeps-the-brackets-out",
      linkpreview.find_link("(см. https://example.dev/a)")
      == "https://example.dev/a", linkpreview.find_link("(см. https://example.dev/a)"))

# ---------------------------------------------------- домой сервер не ходит

check("refuses-the-router", linkpreview.наружу("http://192.168.0.1/") is None)
check("refuses-itself", linkpreview.наружу("https://127.0.0.1:8765/") is None)
check("refuses-a-file", linkpreview.наружу("file:///etc/passwd") is None)
check("refuses-a-strange-scheme", linkpreview.наружу("ftp://example.dev/") is None)

# --------------------------------------------------------- разбор разметки

СТРАНИЦА = """<!doctype html><html><head>
<title>Заголовок из title</title>
<meta property="og:title" content="Как поймать кита &amp; не намокнуть">
<meta property="og:description" content="Короткая выжимка о ките.">
<meta property="og:image" content="/кит.png">
<meta property="og:site_name" content="Китовый вестник">
</head><body>тело</body></html>"""

разобрано = linkpreview.read_meta(СТРАНИЦА, "https://example.dev/статья")
check("meta-takes-og-title",
      разобрано["title"] == "Как поймать кита & не намокнуть", разобрано["title"])
check("meta-takes-description",
      разобрано["text"] == "Короткая выжимка о ките.", разобрано["text"])
check("meta-takes-site", разобрано["site"] == "Китовый вестник", разобрано["site"])
check("meta-makes-image-absolute",
      разобрано["image"].endswith("/%D0%BA%D0%B8%D1%82.png")
      or разобрано["image"].endswith("/кит.png"), разобрано["image"])

без_разметки = linkpreview.read_meta(
    "<html><head><title>Просто страница</title></head></html>",
    "https://example.dev/")
check("meta-falls-back-to-title",
      без_разметки["title"] == "Просто страница", без_разметки["title"])

# ------------------------------------------------- поднимаем сайт и сервер

КАРТИНКА = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082")


class Сайт(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/кит") or self.path.startswith("/%D0%BA"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(КАРТИНКА)))
            self.end_headers()
            self.wfile.write(КАРТИНКА)
            return
        тело = СТРАНИЦА.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(тело)))
        self.end_headers()
        self.wfile.write(тело)

    def log_message(self, *args):
        pass


сайт = http.server.ThreadingHTTPServer(("127.0.0.1", 8850), Сайт)
threading.Thread(target=сайт.serve_forever, daemon=True).start()

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8849", VELIX_OPEN_REGISTRATION="1",
           VELIX_PREVIEW_ALLOW_LOCAL="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
harness.дождаться(8849)

ССЫЛКА = "http://127.0.0.1:8850/статья"


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
    ws, _ = await войти("gosha", "Гоша")
    лена, привет = await войти("lena", "Лена")
    try:
        await ws.send(protocol.direct_request(привет["user"]["id"]))
        беседа = None
        while беседа is None:
            список = await read_until(ws, "conversations")
            беседа = next((one["id"] for one in список["items"]
                           if one.get("kind") == "direct"), None)

        await ws.send(protocol.text_message("Гоша", f"глянь {ССЫЛКА} вот", беседа))
        карточка = await read_until(ws, "preview")

        check("card-comes-to-the-sender", bool(карточка), карточка)
        check("card-has-the-title",
              карточка.get("title") == "Как поймать кита & не намокнуть",
              карточка.get("title"))
        check("card-has-the-text",
              карточка.get("text") == "Короткая выжимка о ките.",
              карточка.get("text"))
        check("card-has-the-site", карточка.get("site") == "Китовый вестник",
              карточка.get("site"))
        check("card-has-a-picture", bool(карточка.get("image")), карточка)

        # --- картинка забирается тем же путём, что и обычное вложение
        if карточка.get("image"):
            await ws.send(protocol.fetch_request(карточка["image"]))
            шапка = await read_until(ws, "blob")
            байты = await asyncio.wait_for(ws.recv(), timeout=25)
            check("card-picture-can-be-fetched",
                  isinstance(байты, (bytes, bytearray)) and len(байты) > 0,
                  шапка)

        # --- карточку видит и собеседник, а не только отправитель
        чужая = await read_until(лена, "preview")
        check("card-comes-to-the-other-side",
              чужая.get("title") == "Как поймать кита & не намокнуть", чужая)

        # --- после перезахода карточка приезжает вместе с историей
        await ws.send(protocol.open_request(беседа))
        история = await read_until(ws, "history")
        свои = [one for one in история.get("items", []) if one.get("preview")]
        check("card-comes-back-with-history", bool(свои),
              [one.get("id") for one in история.get("items", [])])
        if свои:
            check("card-in-history-keeps-the-title",
                  свои[-1]["preview"].get("title")
                  == "Как поймать кита & не намокнуть",
                  свои[-1]["preview"])

        # --- второй раз за той же ссылкой сервер не ходит: берёт готовое
        await ws.send(protocol.text_message("Гоша", f"ещё раз {ССЫЛКА}", беседа))
        вторая = await read_until(ws, "preview")
        check("card-is-remembered",
              вторая.get("image") == карточка.get("image"),
              (вторая.get("image"), карточка.get("image")))
    finally:
        await ws.close()
        await лена.close()


try:
    asyncio.run(проверки())
finally:
    сайт.shutdown()
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(SANDBOX, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
