"""Меню по правой кнопке: вид, закрепление и пересылка."""

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
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("menugui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8782")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"
URI = "ws://localhost:8782"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
SHOTS.mkdir(exist_ok=True)
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)

import protocol  # noqa: E402
import store  # noqa: E402

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-menu-")) / "velix.json"
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

peer = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "пароль123", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]
            await asyncio.sleep(600)

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


def grab(name):
    app.lift()
    app.attributes("-topmost", True)
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


def labels_of(widget):
    return [item.cget("text") for item in widgets_of(widget, gui.ctk.CTkLabel)
            if item.cget("text")]


class FakeClick:
    """Событие правой кнопки в середине окна."""
    def __init__(self, app):
        self.x_root = app.winfo_rootx() + 600
        self.y_root = app.winfo_rooty() + 300


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8782")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def make_groups():
    # Две группы: одна для сообщений, вторая — куда пересылать
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def second_group():
    app.network.send(protocol.group_request("Работа", [peer["id"]]))


@step
def write():
    check("menu-two-groups", len(app.conversations) == 2, app.conversations)
    app.message_entry.insert(0, "встречаемся в семь")
    app._on_send()


@step
def open_menu():
    item = next((one for one in app.loaded_items if one.get("id")), None)
    check("menu-message-has-id", item is not None, app.loaded_items)
    if item is None:
        return
    app._message_menu(FakeClick(app), item, own=True)


@step
def check_menu():
    check("menu-opened", app.menu is not None, "меню не открылось")
    buttons = [b.cget("text") for b in widgets_of(app.menu, gui.ctk.CTkButton)]
    words = " ".join(buttons)
    check("menu-has-reply", "Ответить" in words, buttons)
    check("menu-has-pin", "Закрепить" in words, buttons)
    check("menu-has-copy", "Копировать текст" in words, buttons)
    check("menu-has-forward", "Переслать" in words, buttons)
    check("menu-has-delete", "Удалить" in words, buttons)
    check("menu-has-emoji", all(any(emoji in b for b in buttons)
                                for emoji in gui.EMOJI[:3]), buttons)
    check("menu-inside-window", app.menu.winfo_ismapped(), "меню вне окна")
    grab("menu-1-panel.png")


@step
def pin_message():
    item = next(one for one in app.loaded_items if one.get("id"))
    app._pin_message(item["id"])


@step
def check_pinned():
    check("menu-pin-bar", app.pin_bar.winfo_ismapped(), "полоска закрепления не видна")
    check("menu-pin-text", "встречаемся" in app.pin_label.cget("text"),
          app.pin_label.cget("text"))
    check("menu-pin-remembered",
          (app.pinned.get(app.conversation) or {}).get("text") == "встречаемся в семь",
          app.pinned)
    grab("menu-2-pinned.png")

    # меню теперь предлагает открепить
    item = next(one for one in app.loaded_items if one.get("id"))
    app._message_menu(FakeClick(app), item, own=True)


@step
def check_unpin_offer():
    buttons = [b.cget("text") for b in widgets_of(app.menu, gui.ctk.CTkButton)]
    check("menu-offers-unpin", any("Открепить" in b for b in buttons), buttons)
    app._close_menu()
    check("menu-closes", app.menu is None, "меню не закрылось")

    # пересылаем во вторую группу
    item = next(one for one in app.loaded_items if one.get("id"))
    other = next(c for c in app.conversations if c["id"] != app.conversation)
    app.network.send(protocol.forward_request(item["id"], other["id"]))
    app._open(other["id"])


@step
def check_forwarded():
    # Лента перерисовывается по событиям Tk: сначала даём им пройти
    app.update()
    texts = labels_of(app.messages)
    check("menu-forward-arrived", any("встречаемся в семь" in text for text in texts),
          texts)
    check("menu-forward-marked", any("Переслано от" in text for text in texts), texts)
    check("menu-pin-bar-hidden-elsewhere", not app.pin_bar.winfo_ismapped(),
          "закрепление показалось в чужой переписке")
    grab("menu-3-forwarded.png")


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 3000

try:
    app.mainloop()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
