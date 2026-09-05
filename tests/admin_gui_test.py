"""Окно: панель управления, значки непрочитанного, удаление из панели."""

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
SANDBOX = Path(__file__).with_name("admingui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8790")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"
# Хозяин чата назван явно: соседка регистрируется первой и иначе
# панель досталась бы ей
ENV["VELIX_ADMIN"] = "gosha"
URI = "ws://localhost:8790"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir()
SHOTS.mkdir(exist_ok=True)
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)

import protocol  # noqa: E402
import store  # noqa: E402

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-admin-")) / "velix.json"
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

            while True:
                frame = protocol.decode(await ws.recv())
                if frame is None:
                    continue
                if frame.get("type") == "conversation" and "room" not in peer:
                    peer["room"] = frame["item"]["id"]
                    await asyncio.sleep(7)
                    await ws.send(protocol.text_message("Лена", "первое",
                                                        peer["room"]))
                    await ws.send(protocol.text_message("Лена", "второе",
                                                        peer["room"]))

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


def grab(name, window=None):
    target = window or app
    target.lift()
    harness.тихое_окно(target)
    target.update_idletasks()
    target.update()
    time.sleep(0.7)
    x, y = target.winfo_rootx(), target.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + target.winfo_width(), y + target.winfo_height()),
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


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8790")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def make_group():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()          # окно с кодом восстановления
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def leave_to_list():
    check("admin-group-open", app.conversation is not None, app.conversation)
    # уходим из переписки, чтобы сообщения Лены пришли «мимо»
    app.conversation = -1


@step
def wait_for_peer():
    pass


@step
def check_unread():
    check("admin-unread-counted", app.unread.get(peer["room"], 0) >= 2,
          app.unread)
    app._refresh_side_list()
    app.update()
    side = labels_of(app.side_list)
    check("admin-unread-badge", any(text.strip().isdigit() for text in side), side)
    grab("admin-1-unread.png")

    app._open(peer["room"])


@step
def check_unread_cleared():
    check("admin-unread-cleared", peer["room"] not in app.unread, app.unread)
    app._show_settings()


@step
def open_panel():
    check("admin-button-visible", app.admin_button.winfo_ismapped(),
          "кнопки панели нет")
    app._show_admin()


@step
def check_panel():
    check("admin-panel-open", app.admin_window.winfo_exists(), "панель не открылась")
    summary = app.admin_summary.cget("text")
    check("admin-summary-filled", "Сообщений" in summary and "диске" in summary,
          summary)

    rows = labels_of(app.admin_list)
    check("admin-lists-people", any("Лена" in text for text in rows), rows[:8])
    check("admin-lists-rooms", any("Поход" in text for text in rows), rows[:12])
    check("admin-shows-limits", any("Видео" in text for text in rows), rows[:6])
    check("admin-limit-value",
          app.limit_entries["video"].get() == str(1024), 
          app.limit_entries["video"].get())
    grab("admin-2-panel.png", app.admin_window)


@step
def change_limits():
    app.limit_entries["file"].delete(0, "end")
    app.limit_entries["file"].insert(0, "200")
    app._admin_save_limits()


@step
def check_limits_saved():
    пределы = (app.stats or {}).get("limits") or {}
    check("admin-limit-saved", пределы.get("file") == 200 * 1024 * 1024, пределы)


@step
def drop_room():
    room = next(one for one in app.stats["rooms"] if one["title"] == "Поход")
    app.network.send(protocol.admin_request("drop_room", conversation=room["id"]))


@step
def check_dropped():
    titles = [one.get("title") for one in app.stats["rooms"]]
    check("admin-room-dropped", "Поход" not in titles, titles)
    check("admin-list-updated",
          all(one["id"] != peer["room"] for one in app.conversations),
          app.conversations)
    app.admin_window.destroy()


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
