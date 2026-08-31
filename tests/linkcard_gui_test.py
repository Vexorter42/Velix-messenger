"""Окно: ссылка показывается карточкой — и в истории, и когда приедет потом.

Сервер ходит по ссылке отдельно от сообщения, поэтому карточка появляется в
двух видах: уже готовой в истории и отдельным кадром через секунду после того,
как сообщение легло в ленту. Проверяем оба, и что дважды она не рисуется.
"""

import asyncio
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

уголок = Path(tempfile.mkdtemp(prefix="velix-card-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cardcache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8832"))

КАРТОЧКА = {"url": "https://kit.dev/статья", "title": "Как поймать кита",
            "text": "Короткая выжимка о ките.", "site": "Китовый вестник"}

ЛЕНТА = [
    {"id": 5, "nick": "Гоша", "kind": "text",
     "text": "глянь https://kit.dev/статья вот",
     "at": "2026-08-29T09:00:00+00:00", "user": 1, "preview": КАРТОЧКА},
    {"id": 6, "nick": "Лена", "kind": "text",
     "text": "и вот ещё https://kit.dev/другое",
     "at": "2026-08-29T09:01:00+00:00", "user": 2},
]

ПОЗЖЕ = {"url": "https://kit.dev/другое", "title": "Второй заголовок",
         "text": "И вторая выжимка.", "site": "Китовый вестник"}

связь = {}


async def притворщик(websocket):
    await websocket.recv()
    связь["ws"] = websocket
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
            await websocket.send(protocol.history_page(3, ЛЕНТА, {}, False))


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    петля = asyncio.new_event_loop()
    asyncio.set_event_loop(петля)
    связь["петля"] = петля
    петля.run_until_complete(run())


threading.Thread(target=сервер, daemon=True).start()
harness.дождаться(8832)

app = gui.VelixApp()
harness.тихое_окно(app)
steps = []


def step(function):
    steps.append(function)
    return function


def подписи():
    найдено = []

    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("text"):
                найдено.append(str(child.cget("text")))
            обход(child)

    обход(app.messages)
    return найдено


def карточек():
    """Сколько карточек нарисовано в ленте."""
    сколько = [0]

    def обход(widget):
        for child in widget.winfo_children():
            if getattr(child, "velix_card", None) is not None:
                сколько[0] += 1
            обход(child)

    обход(app.messages)
    return сколько[0]


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
def карточка_из_истории():
    строки = подписи()
    check("card-gui-title-shown", "Как поймать кита" in строки, строки)
    check("card-gui-text-shown", "Короткая выжимка о ките." in строки, строки)
    check("card-gui-site-shown", "Китовый вестник" in строки, строки)
    check("card-gui-one-card", карточек() == 1, карточек())


@step
def карточка_приезжает_потом():
    app._on_preview({"type": "preview", "conversation": 3, "id": 6, **ПОЗЖЕ})


@step
def проверить_вторую():
    строки = подписи()
    check("card-gui-late-card-drawn", "Второй заголовок" in строки, строки)
    check("card-gui-two-cards-now", карточек() == 2, карточек())
    # Тот же кадр во второй раз не должен рисовать вторую такую же карточку
    app._on_preview({"type": "preview", "conversation": 3, "id": 6, **ПОЗЖЕ})


@step
def без_дублей():
    check("card-gui-no-double-card", карточек() == 2, карточек())
    # Карточка попала и в память ленты — значит уедет в сохранённое
    запись = next((one for one in app.loaded_items if one.get("id") == 6), None)
    check("card-gui-kept-in-items",
          запись is not None and запись.get("preview", {}).get("title")
          == "Второй заголовок", запись)


@step
def чужая_переписка():
    было = карточек()
    app._on_preview({"type": "preview", "conversation": 99, "id": 5, **ПОЗЖЕ})
    check("card-gui-ignores-other-room", карточек() == было, карточек())


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 1600

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
