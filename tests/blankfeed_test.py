"""Окно: короткая переписка после длинной не показывает пустоту.

Та самая беда, на которую человек жаловался месяцами. В группе много
сообщений, лента высокая. Переходишь в личную переписку с тремя
строчками — и она пуста. Сообщения на месте, пузыри построены, но
область прокрутки осталась от прежней ленты, и окно смотрит в пустое
место далеко под последним сообщением.
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

уголок = Path(tempfile.mkdtemp(prefix="velix-blank-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8813"))

ДЛИННАЯ = [
    {"id": номер, "nick": "Лена", "kind": "text",
     "text": f"строка номер {номер}", "user": 2,
     "at": f"2026-08-24T09:{номер:02d}:00+00:00"}
    for номер in range(1, 41)
]
КОРОТКАЯ = [
    {"id": 101, "nick": "Руслан", "kind": "text", "text": "привет", "user": 2,
     "at": "2026-08-25T21:15:00+00:00"},
    {"id": 102, "nick": "Гоша", "kind": "text", "text": "тест", "user": 1,
     "at": "2026-08-25T21:16:00+00:00"},
]


async def притворщик(websocket):
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 7, "kind": "group", "title": "Поход"},
        {"id": 3, "kind": "direct", "title": "Руслан", "user": 2},
    ]))

    while True:
        кадр = protocol.decode(await websocket.recv())
        if кадр is None:
            continue
        if кадр.get("type") == "open":
            какая = кадр.get("conversation")
            await websocket.send(protocol.history_page(
                какая, ДЛИННАЯ if какая == 7 else КОРОТКАЯ, {}, False))


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())


threading.Thread(target=сервер, daemon=True).start()
harness.дождаться(8813)

app = gui.VelixApp()
harness.тихое_окно(app)
steps = []
запомнили = {}


def step(function):
    steps.append(function)
    return function


def полотно():
    return app.messages._parent_canvas


def область():
    сказано = полотно().cget("scrollregion")
    return [int(float(one)) for one in сказано.split()] if сказано else None


def подписи():
    найдено = []

    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("text"):
                найдено.append(str(child.cget("text"))[:20])
            обход(child)

    обход(app.messages)
    return найдено


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def look_at_long():
    высокая = (область() or [0, 0, 0, 0])[3]
    запомнили["высокая"] = высокая
    check("blank-long-tall", высокая > 1200,
          f"лента группы вышла невысокой: {высокая}")


@step
def open_direct():
    app._open(3, force=True)


@step
def wait_a_bit():
    pass


@step
def check_direct():
    всё = полотно().bbox("all")
    имеем = область()
    верх = полотно().canvasy(0)
    print(f"      область {имеем} | всё {всё} | верх окна {верх:.0f}"
          f" | было {запомнили.get('высокая')}")
    check("blank-history-came", "привет" in подписи(), подписи()[:6])
    check("blank-region-shrunk", имеем and всё and abs(имеем[3] - всё[3]) <= 4,
          f"область {имеем} не сошлась с лентой {всё}")
    check("blank-feed-visible", всё and верх < всё[3],
          f"окно стоит на {верх:.0f}, а лента кончается на "
          f"{всё[3] if всё else '?'} — человек видит пустоту")


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 2200

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
