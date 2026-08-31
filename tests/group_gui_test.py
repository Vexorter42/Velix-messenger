"""Окно: меню группы — фото и удаление."""

import asyncio
import io as bytes_io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

from PIL import Image, ImageGrab

REPO = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("groupgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8791")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"
URI = "ws://localhost:8791"

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
time.sleep(2.0)

import protocol  # noqa: E402
import store  # noqa: E402

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-group-")) / "velix.json"
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

peer = {"conversations": []}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "пароль123", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]
            while True:
                frame = protocol.decode(await ws.recv())
                if frame is None:
                    continue
                if frame.get("type") == "conversation":
                    peer["room"] = frame["item"]["id"]
                    peer["seen"] = frame["item"]
                elif frame.get("type") == "conversations":
                    peer["conversations"] = frame.get("items") or []

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        pass


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(1.5)

app = gui.VelixApp()
harness.тихое_окно(app)


def grab(name):
    app.lift()
    app.update_idletasks()
    app.update()
    time.sleep(0.6)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()),
                   all_screens=True).save(SHOTS / name)
    print(f"снят {name}")


def widgets_of(root, kind):
    found = []
    for child in root.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found.extend(widgets_of(child, kind))
    return found


def menu_labels():
    if app.menu is None:
        return []
    return [w.cget("text").strip()
            for w in widgets_of(app.menu, gui.ctk.CTkButton)]


class FakeEvent:
    x_root = 300
    y_root = 300
    widget = None


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8791")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def make_group():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def open_menu():
    room = next(one for one in app.conversations if one.get("title") == "Поход")
    peer["gui_room"] = room
    check("group-owner-known", room.get("owner") == app.user.get("id"),
          room.get("owner"))
    app._group_menu(FakeEvent(), room)


@step
def check_menu():
    labels = menu_labels()
    check("group-menu-photo", any("Фото группы" in one for one in labels), labels)
    check("group-menu-delete", any("Удалить группу" in one for one in labels),
          labels)
    grab("group-1-menu.png")
    app._close_menu()


@step
def menu_denied_for_direct():
    # На личной переписке и на участнике меню не появляется
    app._group_menu(FakeEvent(), {"id": 99, "kind": "direct"})
    check("group-menu-direct", app.menu is None, "меню открылось не там")


@step
def send_photo():
    picture = bytes_io.BytesIO()
    Image.new("RGB", (120, 120), (200, 60, 60)).save(picture, "PNG")
    data = picture.getvalue()
    room = peer["gui_room"]["id"]
    app.network.send(protocol.group_avatar_header(room, "поход.png", len(data)),
                     data)


@step
def check_photo():
    room = peer["gui_room"]["id"]
    mine = next((one for one in app.conversations if one["id"] == room), None)
    check("group-photo-mine", bool(mine and mine.get("avatar")),
          mine and mine.get("avatar"))
    theirs = peer.get("seen") or {}
    check("group-photo-shared", theirs.get("avatar") == (mine or {}).get("avatar"),
          theirs.get("avatar"))
    # Шапка чата тоже должна показать фото, а не букву
    check("group-photo-header", app.header_avatar.cget("image") is not None,
          "в шапке осталась буква")
    grab("group-2-photo.png")


@step
def delete_group():
    app._confirm = lambda *args, **values: True     # без окна «точно?»
    app._delete_group(peer["gui_room"])


@step
def check_deleted():
    room = peer["gui_room"]["id"]
    check("group-deleted-here",
          all(one["id"] != room for one in app.conversations), app.conversations)
    check("group-deleted-there",
          all(one["id"] != room for one in peer["conversations"]),
          peer["conversations"])


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
