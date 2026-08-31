"""Окно: вкладка «медиа» показывает всю переписку сеткой."""

import asyncio
import io as bytes_io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-galtab-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

from PIL import Image  # noqa: E402

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8839"))


def снимок(цвет):
    холст = bytes_io.BytesIO()
    Image.new("RGB", (300, 200), цвет).save(холст, "PNG")
    return холст.getvalue()


КАРТИНКИ = {"aaa111": снимок((200, 90, 60)),
            "bbb222": снимок((60, 140, 200)),
            "ccc333": снимок((90, 190, 110))}

# В ленте — только последнее, а во вкладке должно быть всё
ВЛОЖЕНИЯ = [
    {"id": 9, "kind": "image", "media": "ccc333", "name": "три.png",
     "size": len(КАРТИНКИ["ccc333"]), "at": "2026-08-27T09:03:00+00:00",
     "nick": "Лена", "user": 2},
    {"id": 5, "kind": "image", "media": "bbb222", "name": "два.png",
     "size": len(КАРТИНКИ["bbb222"]), "at": "2026-08-27T09:02:00+00:00",
     "nick": "Лена", "user": 2},
    {"id": 2, "kind": "image", "media": "aaa111", "name": "один.png",
     "size": len(КАРТИНКИ["aaa111"]), "at": "2026-08-27T09:01:00+00:00",
     "nick": "Лена", "user": 2},
]
просили = []


async def притворщик(websocket):
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 3, "kind": "direct", "title": "Лена", "user": 2},
    ]))

    while True:
        кадр = protocol.decode(await websocket.recv())
        if кадр is None:
            continue
        if кадр.get("type") == "open":
            await websocket.send(protocol.history_page(3, [], {}, False))
        elif кадр.get("type") == "gallery":
            просили.append(кадр)
            await websocket.send(protocol.gallery_message(3, ВЛОЖЕНИЯ))
        elif кадр.get("type") == "fetch":
            данные = КАРТИНКИ.get(кадр["id"])
            if данные is None:
                continue
            await websocket.send(protocol.blob_header(
                кадр["id"], "image", "кадр.png", len(данные), 1))
            await websocket.send(данные)


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    петля = asyncio.new_event_loop()
    asyncio.set_event_loop(петля)
    петля.run_until_complete(run())


threading.Thread(target=сервер, daemon=True).start()
harness.дождаться(8839)

app = gui.VelixApp()
app.geometry("1000x700")
harness.тихое_окно(app)
steps = []


def step(function):
    steps.append(function)
    return function


def клетки(widget):
    """Все подписи внутри просмотра — по ним и считаем клетки."""
    найдено = []

    def обход(место):
        for child in место.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel):
                найдено.append(child)
            обход(child)

    обход(widget)
    return найдено


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def открыть():
    app._open(3, force=True)


@step
def нажать_медиа():
    check("galtab-gui-button-there", hasattr(app, "gallery_button"))
    app._ask_gallery()


@step
def проверить_запрос():
    check("galtab-gui-asked", bool(просили), просили)
    check("galtab-gui-overlay", app.viewer is not None)


@step
def проверить_сетку():
    подписи = [one.cget("text") for one in клетки(app.viewer)]
    check("galtab-gui-title", any("Вложения" in one for one in подписи), подписи)
    check("galtab-gui-count", any("3" in one for one in подписи), подписи)
    check("galtab-gui-order",
          [one["media"] for one in app.gallery] == ["aaa111", "bbb222", "ccc333"],
          app.gallery)


@step
def проверить_картинки():
    сколько = sum(1 for one in клетки(app.viewer) if one.cget("image"))
    check("galtab-gui-thumbs", сколько >= 3,
          f"нарисовано клеток с картинкой: {сколько}")


@step
def открыть_из_сетки():
    app._open_from_gallery("bbb222", "image")


@step
def проверить_просмотр():
    check("galtab-gui-opens-viewer", app.viewer is not None)
    check("galtab-gui-right-item", app.viewer_at == 1, app.viewer_at)
    check("galtab-gui-pages-all", len(app.viewer_items) == 3, app.viewer_items)


@step
def finish():
    app.destroy()


delay = 900
паузы = {"проверить_запрос": 2200, "проверить_сетку": 2600}
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1500)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
