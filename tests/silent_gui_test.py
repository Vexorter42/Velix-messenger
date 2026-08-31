"""Окно: молчащий сокет не оставляет переписку пустой навсегда.

Беда с боевого сервера: связь умерла тихо (например, ноутбук поспал),
клиент об этом не знает, запрос истории уходит в никуда — и лента
остаётся чистой, сколько по ней ни щёлкай.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

from PIL import ImageGrab

REPO = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("silentgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8807", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8807"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
SHOTS.mkdir(exist_ok=True)
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)

import protocol  # noqa: E402
import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-silent-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

peer = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "parol12345", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]

            # Сама заводим личную переписку с Гошей и пишем в неё: так не
            # нужно угадывать, когда он до нас доберётся
            await asyncio.sleep(4)
            await ws.send(protocol.direct_request(1))
            while "room" not in peer:
                frame = protocol.decode(await ws.recv())
                if frame and frame.get("type") == "history":
                    peer["room"] = frame.get("conversation")
            await ws.send(protocol.text_message("Лена", "привет", peer["room"]))

            while True:
                await ws.recv()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception as error:
        print("      сосед отвалился:", error)


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(1.5)

app = gui.VelixApp()
harness.тихое_окно(app)
steps = []


def step(function):
    steps.append(function)
    return function


def grab(name):
    app.lift()
    app.update_idletasks()
    app.update()
    time.sleep(0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()),
                   all_screens=True).save(SHOTS / name)
    print(f"снят {name}")


def строки_ленты():
    подписи = []

    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("text"):
                подписи.append(child.cget("text"))
            обход(child)

    обход(app.messages)
    return подписи


кадры = []
настоящий_разбор = None


@step
def watch_frames():
    """Записываем, что приходит с сервера — для разбора полётов."""
    global настоящий_разбор
    настоящий_разбор = app._on_message

    def перехват(message):
        кадры.append((round(time.monotonic() % 1000, 1), message.get("type"),
                      message.get("conversation")))
        return настоящий_разбор(message)

    app._on_message = перехват


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8807")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def open_direct():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    app._start_direct(peer["id"])


@step
def check_alive():
    app._куда = app.conversation
    # Здесь важно лишь то, что переписка открыта и лента отрисована;
    # содержимое личных переписок проверяет direct_gui_test
    check("silent-direct-open", app.conversation is not None
          and app.waiting_for is None, (app.conversation, app.waiting_for))


@step
def go_silent():
    """Глушим сокет так, будто связь умерла молча: отправка проваливается."""
    настоящий = app.network.websocket

    class Немой:
        """Молчит в ответ на всё, как повисшее соединение."""

        async def send(self, *args, **kwargs):
            await asyncio.sleep(3600)

        async def close(self, *args, **kwargs):
            await настоящий.close()

    app.network.websocket = Немой()
    app._настоящий = настоящий
    print("      сокет замолчал")


@step
def click_chat():
    app._open(app._куда, force=True)
    check("silent-feed-cleared", not строки_ленты(), строки_ленты())


@step
def check_notice():
    подписи = строки_ленты()
    check("silent-says-something", any("связь" in one.lower()
                                       or "сервер" in one.lower()
                                       for one in подписи),
          подписи or "лента молча пуста")
    grab("silent-1-notice.png")


@step
def check_recovered():
    # Связь тут не рвём: сторож только просит историю заново и подписывает,
    # а возвращение после обрыва проверяет reconnect_gui_test
    print("      кадры:", кадры[-8:])
    check("silent-keeps-socket", app.network.websocket is not None,
          "сторож зачем-то оборвал связь")
    подписи = строки_ленты()
    check("silent-one-notice",
          len([one for one in подписи if "Ждём связи" in one]) == 1,
          подписи)
    grab("silent-2-notice.png")


@step
def finish():
    app.destroy()


# Сторож ждёт историю дважды по четыре секунды, потом рвёт связь и
# переподключается — расписание с запасом
паузы = {"click_chat": 15000, "check_notice": 18000}
delay = 900
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 2600)

try:
    app.mainloop()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
