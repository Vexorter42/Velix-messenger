"""Окно: поиск людей по @username, значок группы, приглашение в группу."""

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
SANDBOX = Path(__file__).with_name("peoplegui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8803", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8803"

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

уголок = Path(tempfile.mkdtemp(prefix="velix-people-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

peers = {}


def peer_thread(login, name):
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message(login, "parol12345", name))
            welcome = protocol.decode(await ws.recv())
            peers[login] = {"id": welcome["user"]["id"], "позвали": False}
            while True:
                frame = protocol.decode(await ws.recv())
                if frame and frame.get("type") == "conversation" \
                        and frame["item"].get("kind") == "group":
                    peers[login]["позвали"] = True
                    peers[login]["группа"] = frame["item"]["id"]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        pass


for логин, имя in (("lena", "Лена"), ("dima", "Дима")):
    threading.Thread(target=peer_thread, args=(логин, имя), daemon=True).start()
    time.sleep(0.7)
time.sleep(1.0)

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


def widgets_of(root, kind):
    found = []
    for child in root.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found.extend(widgets_of(child, kind))
    return found


def подписи():
    return [one.cget("text") for one in widgets_of(app.side_list, gui.ctk.CTkLabel)
            if one.cget("text")]


class FakeEvent:
    x_root = 300
    y_root = 300
    widget = None


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8803")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def check_no_people():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    app._refresh_side_list()
    видно = подписи()
    check("people-hidden", not any("Лена" in one for one in видно), видно)
    check("people-knows-them", len(app.people) >= 3, app.people)
    grab("people-1-empty.png")


@step
def search_by_login():
    app.search_entry.insert(0, "@len")
    app._on_search_typing()
    видно = подписи()
    check("people-found-by-login", any("Лена" in one for one in видно), видно)
    check("people-shows-username", any("@lena" in one for one in видно), видно)
    check("people-only-match", not any("Дима" in one for one in видно), видно)
    grab("people-2-search.png")


@step
def make_group():
    app.search_entry.delete(0, "end")
    app._on_search_typing()
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peers["lena"]["id"]]))


@step
def check_group_mark():
    видно = подписи()
    check("people-group-badge", any("👥" in one for one in видно), видно)
    grab("people-3-group.png")


@step
def invite_dima():
    группа = next(one for one in app.conversations if one.get("title") == "Поход")
    check("people-members-known", peers["lena"]["id"] in (группа.get("members") or []),
          группа.get("members"))
    app._invite_to_group(группа)


@step
def choose_dima():
    окно = [c for c in app.winfo_children() if isinstance(c, gui.ctk.CTkToplevel)][-1]
    флажки = widgets_of(окно, gui.ctk.CTkCheckBox)
    подписи_окна = [one.cget("text") for one in флажки]
    check("people-invite-lists-free", any("Дима" in one for one in подписи_окна),
          подписи_окна)
    check("people-invite-hides-members",
          not any("Лена" in one for one in подписи_окна), подписи_окна)
    grab("people-4-invite.png")

    for флажок in флажки:
        if "Дима" in флажок.cget("text"):
            флажок.select()
    кнопки = [one for one in widgets_of(окно, gui.ctk.CTkButton)
              if one.cget("text") == "Позвать"]
    кнопки[0].invoke()


@step
def check_invited():
    check("people-dima-invited", peers["dima"].get("позвали", False), peers["dima"])
    группа = next(one for one in app.conversations if one.get("title") == "Поход")
    # Список участников должен обновиться и у того, кто звал
    check("people-members-updated",
          peers["dima"]["id"] in (группа.get("members") or []),
          группа.get("members"))


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
