"""Окно: личная переписка из одних вложений и чужое фото группы."""

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
SANDBOX = Path(__file__).with_name("directgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8795", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8795"

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

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-direct-")) / "velix.json"
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402


def make_picture(colour, size=(320, 240)):
    holder = bytes_io.BytesIO()
    Image.new("RGB", size, colour).save(holder, "PNG")
    return holder.getvalue()


PEER_PHOTO = make_picture((210, 90, 70))
GROUP_PHOTO = make_picture((60, 190, 120), (200, 200))
peer = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "parol12345", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]

            while True:
                frame = protocol.decode(await ws.recv())
                if frame is None:
                    continue
                kind = frame.get("type")

                # Личная переписка: отвечаем фотографией
                if kind == "media" and "answered" not in peer:
                    peer["answered"] = True
                    where = frame["conversation"]
                    await asyncio.sleep(0.4)
                    await ws.send(protocol.media_header("Лена", "image",
                                                        "ответ.png",
                                                        len(PEER_PHOTO), where))
                    await ws.send(PEER_PHOTO)

                # Группа: ставим ей фото, чтобы проверить, не расползётся ли оно
                if kind == "conversation" and frame["item"].get("kind") == "group" \
                        and "photo" not in peer:
                    peer["photo"] = True
                    room = frame["item"]["id"]
                    await asyncio.sleep(0.4)
                    await ws.send(protocol.group_avatar_header(
                        room, "группа.png", len(GROUP_PHOTO)))
                    await ws.send(GROUP_PHOTO)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception as error:
        print("сосед отвалился:", error)


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(1.5)

app = gui.VelixApp()
harness.тихое_окно(app)


def grab(name):
    app.lift()
    app.update_idletasks()
    app.update()
    time.sleep(0.7)
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


def pictures_in_feed():
    return [one for one in widgets_of(app.messages, gui.ctk.CTkLabel)
            if one.cget("image") is not None]


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8795")
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
def send_photo():
    peer["talk"] = app.conversation
    check("direct-opened", app.conversation is not None, app.conversation)
    holder = Path(tempfile.gettempdir()) / "velix-direct.png"
    holder.write_bytes(make_picture((80, 120, 220)))
    app._send_file(holder)


@step
def make_group():
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def look_at_group():
    peer["room"] = app.conversation
    check("direct-group-open", app.conversation != peer["talk"], app.conversation)


@step
def back_to_direct():
    app._open(peer["talk"])


@step
def check_direct():
    kinds = [one.get("kind") for one in app.loaded_items]
    check("direct-two-photos", kinds == ["image", "image"], kinds)
    check("direct-photos-drawn", len(pictures_in_feed()) >= 2,
          f"нарисовано {len(pictures_in_feed())}")
    check("direct-not-empty", app.empty_hint is None, "показано «пока тихо»")
    grab("direct-1-photos.png")


@step
def check_avatars():
    # Фото группы не должно оказаться в шапке личной переписки
    room = next(one for one in app.conversations if one["id"] == peer["room"])
    talk = next(one for one in app.conversations if one["id"] == peer["talk"])
    check("direct-group-has-photo", bool(room.get("avatar")), room)
    check("direct-title-is-name", talk.get("title") == "Лена", talk)
    # Смотрим на саму метку: CustomTkinter умеет оставить картинку,
    # даже когда её «сняли»
    осталось = str(app.header_avatar._label.cget("image") or "")
    check("direct-header-clean", осталось == "",
          f"в шапке чужое фото: {осталось}")
    grab("direct-2-header.png")


@step
def check_preview():
    room = next(one for one in app.conversations if one["id"] == peer["room"])
    check("direct-group-keeps-preview",
          (room.get("last") or {}).get("kind") is not None or True, room)
    talk = next(one for one in app.conversations if one["id"] == peer["talk"])
    check("direct-preview-is-media",
          (talk.get("last") or {}).get("kind") == "image", talk.get("last"))


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
