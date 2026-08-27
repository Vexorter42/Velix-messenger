"""Окно: запоздавшая картинка не затыкает клиента навсегда.

Та самая беда с боевого сервера. Человек открывает группу с фотографиями
и, не дожидаясь их, уходит в личную переписку. Пузыри к тому времени
уничтожены, а картинка всё-таки приезжает — и рисуется в пустоту. Tk
бросал ошибку, разбор пришедшего умирал насовсем, и клиент, оставаясь на
связи, переставал показывать что-либо: личные переписки стояли пустыми.
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-late-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import protocol  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8811"))

# Настоящая картинка: её и будем рисовать в исчезнувший пузырь
from PIL import Image  # noqa: E402
import io as bytes_io  # noqa: E402

холст = bytes_io.BytesIO()
Image.new("RGB", (400, 300), (200, 90, 60)).save(холст, "PNG")
КАРТИНКА = холст.getvalue()

состояние = {}


async def притворщик(websocket):
    """Отдаёт фотографию с опозданием — когда пузырь уже уничтожен."""
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 7, "kind": "group", "title": "Поход"},
        {"id": 3, "kind": "direct", "title": "Руслан", "user": 2},
    ]))

    async def позже(задержка, дело):
        await asyncio.sleep(задержка)
        await дело

    while True:
        кадр = protocol.decode(await websocket.recv())
        if кадр is None:
            continue

        if кадр.get("type") == "open" and кадр.get("conversation") == 7:
            await websocket.send(protocol.history_page(7, [
                {"id": 1, "nick": "Лена", "kind": "image", "media": "abc123",
                 "name": "photo.png", "size": len(КАРТИНКА),
                 "at": "2026-08-26T09:00:00+00:00", "user": 2},
            ], {}, False))

        elif кадр.get("type") == "fetch":
            # Отдаём с задержкой: человек успеет уйти в другую переписку
            async def отдать(номер=кадр["id"]):
                await websocket.send(protocol.blob_header(
                    номер, "image", "photo.png", len(КАРТИНКА), 1))
                await websocket.send(КАРТИНКА)
                состояние["отдали"] = True
            asyncio.create_task(позже(3.0, отдать()))

        elif кадр.get("type") == "open" and кадр.get("conversation") == 3:
            состояние["просили"] = состояние.get("просили", 0) + 1

            # История приходит уже после запоздавшей картинки
            async def история():
                await websocket.send(protocol.history_page(3, [
                    {"id": 2, "nick": "Руслан", "kind": "text",
                     "text": "привет", "at": "2026-08-26T09:01:00+00:00",
                     "user": 2},
                ], {}, False))
            asyncio.create_task(позже(4.0, история()))


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
time.sleep(1.2)

app = gui.VelixApp()
app.attributes("-topmost", True)
steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def leave_before_photo():
    check("late-group-open", app.conversation == 7, app.conversation)
    check("late-photo-not-yet", not состояние.get("отдали", False),
          "картинка успела прийти — проверка бессмысленна")
    app._open(3, force=True)      # пузырь с картинкой уничтожен


@step
def wait_for_photo():
    pass


@step
def wait_for_history():
    pass


@step
def check_alive():
    check("late-photo-arrived", состояние.get("отдали", False),
          "притворщик не отдал картинку")
    тексты = [one.get("text") for one in app.loaded_items]
    check("late-history-shown", "привет" in тексты, тексты)
    check("late-not-waiting", app.waiting_for is None, app.waiting_for)


@step
def check_pump():
    # Разбор пришедшего должен быть жив: проверяем свежим кадром
    было = len(app.loaded_items)
    app.events.put(("message", {"type": "text", "id": 99, "conversation": 3,
                                "nick": "Руслан", "text": "ещё одно",
                                "at": "2026-08-26T09:02:00+00:00", "user": 2}))
    app.after(300, lambda: check(
        "late-pump-alive", len(app.loaded_items) > было,
        f"разбор молчит: было {было}, стало {len(app.loaded_items)}"))


@step
def finish():
    app.destroy()


delay = 900
паузы = {"leave_before_photo": 4000, "wait_for_photo": 4000}
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1600)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
