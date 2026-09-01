"""Окно: после обрыва связи переписка не остаётся пустой."""

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
SANDBOX = Path(__file__).with_name("reconnectgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8802", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8802"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
SHOTS.mkdir(exist_ok=True)
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)


def запустить_сервер():
    return subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


server = запустить_сервер()
time.sleep(2.2)

import protocol  # noqa: E402
import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-reconn-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

peer = {}


def peer_thread():
    async def run():
        import websockets
        while True:
            try:
                async with websockets.connect(
                        URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
                    if "id" in peer:
                        await ws.send(protocol.login_message("lena", "parol12345"))
                    else:
                        await ws.send(protocol.register_message(
                            "lena", "parol12345", "Лена"))
                    welcome = protocol.decode(await ws.recv())
                    peer["id"] = welcome["user"]["id"]
                    peer["ws"] = ws

                    while True:
                        frame = protocol.decode(await ws.recv())
                        if frame is None:
                            continue
                        if frame.get("type") in ("conversation", "conversations"):
                            items = ([frame["item"]]
                                     if frame.get("type") == "conversation"
                                     else frame.get("items") or [])
                            личные = [one for one in items
                                      if one.get("kind") == "direct"]
                            if личные:
                                peer["room"] = личные[0]["id"]
            except Exception:
                await asyncio.sleep(0.5)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    peer["loop"] = loop
    loop.run_until_complete(run())


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


def сказать(текст):
    """Просит соседку написать в переписку."""
    async def дело():
        await peer["ws"].send(protocol.text_message("Лена", текст, peer["room"]))
    asyncio.run_coroutine_threadsafe(дело(), peer["loop"])


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8802")
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
def write_something():
    peer["room"] = app.conversation
    check("reconn-direct-open", app.conversation is not None, app.conversation)
    сказать("первое")


@step
def kill_server():
    check("reconn-first-seen",
          any(one.get("text") == "первое" for one in app.loaded_items),
          [one.get("text") for one in app.loaded_items])

    global server
    server.terminate()
    server.wait(timeout=5)
    print("сервер остановлен")


@step
def click_while_down():
    # Человек щёлкает по переписке, пока связи нет
    app._open(peer["room"], force=True)
    подписи = [one.cget("text") for one in app.messages.winfo_children()
               if isinstance(one, gui.ctk.CTkFrame)
               for one in one.winfo_children()
               if isinstance(one, gui.ctk.CTkLabel)]
    check("reconn-says-no-link", any("Нет связи" in one for one in подписи),
          подписи)
    grab("reconnect-1-down.png")


@step
def start_again():
    global server
    server = запустить_сервер()
    print("сервер снова поднят")


@step
def wait_for_return():
    pass


@step
def wait_more():
    pass


@step
def check_back():
    check("reconn-back-online", app.network.websocket is not None,
          "клиент не вернулся")
    # Лента должна быть перечитана с сервера, а не оставаться прежней
    видно = [one.get("text") for one in app.loaded_items]
    check("reconn-feed-restored", "первое" in видно, видно)
    пузырей = len([one for one in app.messages.winfo_children()])
    check("reconn-feed-drawn", пузырей > 1, f"в ленте {пузырей} строк")
    check("reconn-same-place", app.conversation == peer["room"],
          (app.conversation, peer["room"]))
    grab("reconnect-2-back.png")


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 3200

try:
    app.mainloop()
finally:
    try:
        server.terminate()
        server.wait(timeout=5)
    except Exception:
        pass

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
