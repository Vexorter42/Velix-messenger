"""Старый клиент 0.2.2.0 против свежего сервера: не пустеет ли переписка."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import ImageGrab

REPO = Path(__file__).resolve().parent.parent
СТАРЫЙ = Path(__file__).with_name("oldclient")
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("oldsandbox")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8801", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8801"

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

# Свежий протокол нужен соседу, старый — клиенту
sys.path.insert(0, str(REPO))
import protocol as новый  # noqa: E402

peer = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=новый.MAX_FRAME_SIZE) as ws:
            await ws.send(новый.register_message("lena", "parol12345", "Лена"))
            welcome = новый.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]

            while True:
                frame = новый.decode(await ws.recv())
                if frame is None:
                    continue
                # Личную переписку заводит сам клиент — ждём её появления
                if frame.get("type") in ("conversation", "conversations")                         and "room" not in peer:
                    items = ([frame["item"]] if frame.get("type") == "conversation"
                             else frame.get("items") or [])
                    личные = [one for one in items if one.get("kind") == "direct"]
                    if not личные:
                        continue
                    peer["room"] = личные[0]["id"]
                    await asyncio.sleep(3.0)
                    await ws.send(новый.text_message("Лена", "Тест",
                                                      peer["room"]))
                    peer["написала"] = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception as error:
        print("сосед отвалился:", error)


# Клиент берём старый: он лежит отдельной папкой
sys.path.insert(0, str(СТАРЫЙ))
for name in list(sys.modules):
    if name in ("protocol", "store", "i18n", "gui", "version"):
        del sys.modules[name]

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-old-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})

import gui  # noqa: E402
import version  # noqa: E402
import protocol  # noqa: E402

print(f"клиент {version.VERSION}, его протокол {protocol.VERSION}, "
      f"сервер {новый.VERSION}")

app = gui.VelixApp()
app.attributes("-topmost", True)
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


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8801")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def start_peer():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    threading.Thread(target=peer_thread, daemon=True).start()


@step
def open_direct():
    check("old-peer-known", bool(peer.get("id")), peer)
    app._start_direct(peer["id"])


@step
def check_opened():
    check("old-direct-open", app.conversation is not None, app.conversation)
    peer["room"] = app.conversation


@step
def leave_and_wait():
    # Уходим из переписки, чтобы сообщение соседки пришло «мимо»
    app.conversation = -1


@step
def come_back():
    check("old-peer-wrote", peer.get("написала", False), peer)
    app._open(peer["room"])


@step
def check_feed():
    видно = [one.get("text") for one in app.loaded_items]
    check("old-feed-not-empty", bool(app.loaded_items),
          "переписка пуста, хотя сообщение есть")
    check("old-feed-has-message", "Тест" in видно, видно)
    grab("old-1-feed.png")


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 2600

try:
    app.mainloop()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
