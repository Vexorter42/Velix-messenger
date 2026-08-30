"""Окно: группы, галочки, меню правой кнопки и картинка во всё окно."""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("featuregui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8781")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"
URI = "ws://localhost:8781"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)

import protocol  # noqa: E402
import store  # noqa: E402

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-feat-")) / "velix.json"
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

picture = io.BytesIO()
Image.new("RGB", (240, 180), (60, 140, 200)).save(picture, "PNG")
PICTURE = picture.getvalue()

# --- собеседница живёт в отдельном потоке
peer = {"read": False, "sent_image": False}


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
                if frame.get("type") == "text" and not peer["read"]:
                    # Лена прочитала сообщение: галочки должны посинеть
                    peer["read"] = True
                    await asyncio.sleep(0.6)
                    await ws.send(protocol.read_request(frame["conversation"],
                                                        [frame["id"]]))
                    await asyncio.sleep(0.4)
                    await ws.send(protocol.media_header(
                        "Лена", "image", "вид.png", len(PICTURE),
                        frame["conversation"]))
                    await ws.send(PICTURE)
                    peer["sent_image"] = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        pass


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(1.5)

app = gui.VelixApp()
app.attributes("-topmost", True)


def labels_of(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, (gui.ctk.CTkLabel, gui.ctk.CTkButton)):
            found.append(child.cget("text"))
        found.extend(labels_of(child))
    return found


def toplevels():
    return [child for child in app.winfo_children()
            if isinstance(child, gui.ctk.CTkToplevel)]


def widgets_of(root, kind):
    found = []
    for child in root.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found.extend(widgets_of(child, kind))
    return found


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8781")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def check_empty():
    check("feat-chat-opened", app.chat_view.winfo_ismapped(), "чат не открылся")
    check("feat-no-conversation", app.conversation is None, app.conversation)
    check("feat-empty-hint",
          any("Создайте группу" in text for text in labels_of(app.messages)),
          labels_of(app.messages))

    # После регистрации показывается код восстановления — закрываем его
    for window in toplevels():
        window.destroy()
    app.update()
    app._new_group()


@step
def fill_group():
    # Окно группы узнаём по галочкам: других окон с ними нет
    windows = [one for one in toplevels()
               if widgets_of(one, gui.ctk.CTkCheckBox)]
    check("feat-group-dialog", len(windows) == 1, toplevels())
    if not windows:
        return
    window = windows[0]
    entry = widgets_of(window, gui.ctk.CTkEntry)[0]
    entry.insert(0, "Поход")
    boxes = widgets_of(window, gui.ctk.CTkCheckBox)
    check("feat-group-lists-people", len(boxes) == 1, [b.cget("text") for b in boxes])
    if boxes:
        boxes[0].select()
    buttons = [b for b in widgets_of(window, gui.ctk.CTkButton)
               if b.cget("text") == "Создать"]
    if buttons:
        buttons[0].invoke()


@step
def check_group():
    check("feat-group-opened", app.conversation is not None, app.conversation)
    check("feat-group-in-list",
          any("Поход" in text for text in labels_of(app.side_list)),
          labels_of(app.side_list))
    app.message_entry.insert(0, "выходим в семь")
    app._on_send()


@step
def check_ticks_sent():
    states = [state for key, state in app.states.items()]
    check("feat-tick-appears", bool(states), app.states)
    check("feat-tick-not-pending",
          all(state != "sending" for state in states), app.states)


@step
def check_ticks_read():
    check("feat-tick-read", "read" in app.states.values(), app.states)
    marks = [label.cget("text") for label in app.ticks.values()]
    check("feat-tick-double", any(mark == "✓✓" for mark in marks), marks)


@step
def check_incoming_image():
    check("feat-image-arrived", peer["sent_image"], "картинка не отправлена")
    # Картинку в пузыре узнаём по курсу-руке: у аватарок его нет
    holders = [label for label in widgets_of(app.messages, gui.ctk.CTkLabel)
               if label.cget("image") is not None
               and str(label.cget("cursor")) == "hand2"]
    check("feat-image-shown", bool(holders), "картинки в ленте нет")
    if holders:
        # Щёлкаем по внутренней метке: обёртка CustomTkinter события не ловит
        holders[-1]._label.event_generate("<Button-1>")


@step
def check_viewer():
    overlay = app.viewer
    check("feat-viewer-opened", overlay is not None,
          "картинка не открылась во всё окно")
    if overlay is not None:
        check("feat-viewer-has-picture",
              any(label.cget("image") is not None
                  for label in widgets_of(overlay, gui.ctk.CTkLabel)),
              "в просмотре нет картинки")
        app._close_full(overlay)
        app.update()
        check("feat-viewer-closed",
              app.viewer is None and not overlay.winfo_exists(),
              "просмотр не закрылся")
        check("feat-chat-intact", app.chat_view.winfo_ismapped(), "чат пострадал")
    else:
        check("feat-viewer-has-picture", False, "просмотр не открылся")
        check("feat-viewer-closed", False, "нечего закрывать")
        check("feat-chat-intact", False, "просмотр не открылся")


@step
def check_copy():
    own = [item for item in app.loaded_items if item.get("text") == "выходим в семь"]
    check("feat-own-item-has-id", own and own[0].get("id"), own)
    if own:
        app._copy_item(own[0])
        app.update()
        check("feat-copy-text", app.clipboard_get() == "выходим в семь",
              app.clipboard_get())
    else:
        check("feat-copy-text", False, "своего сообщения нет")


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
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
