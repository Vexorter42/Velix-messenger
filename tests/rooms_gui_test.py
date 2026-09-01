"""Окно с переписками: список слева, личные диалоги, ответы, удаление, поиск."""

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
SANDBOX = Path(__file__).with_name("roomguisandbox")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8772")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)

import protocol  # noqa: E402
import store  # noqa: E402
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-rooms-")) / "velix.json"
# Набор писался под русский интерфейс, а по умолчанию теперь
# английский: язык задаём явно
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402
gui.PORT = 8772

peer = {}


def peer_thread():
    """Соседка: заходит, пишет в общий чат и отвечает в личке."""
    async def run():
        import websockets
        async with websockets.connect("ws://localhost:8772",
                                      max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "пароль123", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]

            # Ждём, пока зарегистрируется Гоша, и зовём его в группу:
            # общего чата больше нет
            others = []
            while not others:
                frame = protocol.decode(await ws.recv())
                if frame.get("type") == "people":
                    others = [person["id"] for person in frame["items"]
                              if person["id"] != peer["id"]]

            await ws.send(protocol.group_request("Общая", others))
            frame = None
            while (frame or {}).get("type") != "conversation":
                frame = protocol.decode(await ws.recv())
            peer["room"] = frame["item"]["id"]

            await asyncio.sleep(2)
            await ws.send(protocol.text_message("Лена", "привет всем", peer["room"]))
            peer["ready"] = True

            while True:
                frame = protocol.decode(await ws.recv())
                if frame.get("type") == "text"                         and frame.get("conversation") != peer["room"]:
                    await ws.send(protocol.text_message("Лена", "отвечаю в личку",
                                                        frame["conversation"]))
                    peer["answered"] = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        pass


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(2.0)

app = gui.VelixApp()
harness.тихое_окно(app)


def grab(name):
    app.lift()
    harness.тихое_окно(app)
    app.update_idletasks()
    app.update()
    time.sleep(0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()),
                   all_screens=True).save(SHOTS / name)
    print(f"снят {name}")


def labels_of(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, gui.ctk.CTkLabel):
            found.append(child.cget("text"))
        found.extend(labels_of(child))
    return found


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_in():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8772")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "пароль123")
    app.name_entry.insert(0, "Гоша")
    app.invite_entry.insert(0, "не нужен")
    app._on_primary()


@step
def check_lists():
    check("rooms-chat-opened", app.chat_view.winfo_ismapped())
    check("rooms-general-listed", any(c["kind"] == "group" for c in app.conversations),
          app.conversations)
    check("rooms-people-listed", any(p["name"] == "Лена" for p in app.people),
          app.people)
    check("rooms-online-known", peer.get("id") in app.online, app.online)

    side = labels_of(app.side_list)
    check("rooms-side-shows-room", "Общая" in side, side[:8])
    # Людей в списке больше нет — их находят поиском по @username
    check("rooms-side-hides-people", "Лена" not in side, side[:12])
    app.search_entry.insert(0, "@len")
    app._on_search_typing()
    найдено = labels_of(app.side_list)
    check("rooms-side-finds-people", "Лена" in найдено, найдено[:12])
    app.search_entry.delete(0, "end")
    app._on_search_typing()
    grab("rooms-1-general.png")


@step
def check_room_message():
    texts = labels_of(app.messages)
    check("rooms-room-message-shown", any("привет всем" in t for t in texts), texts[-4:])
    app.message_entry.insert(0, "и тебе привет")
    app._on_send()


@step
def open_direct():
    app._start_direct(peer["id"])


@step
def check_direct():
    direct = [c for c in app.conversations if c["kind"] == "direct"]
    check("rooms-direct-created", len(direct) == 1 and direct[0]["title"] == "Лена",
          app.conversations)
    check("rooms-direct-opened", app.conversation == direct[0]["id"],
          (app.conversation, direct))
    check("rooms-header-shows-name", app.header_title.cget("text") == "Лена",
          app.header_title.cget("text"))
    check("rooms-direct-empty", not any("привет всем" in t
                                        for t in labels_of(app.messages)),
          "в личку затесались сообщения из общего чата")

    app.message_entry.insert(0, "привет лично")
    app._on_send()


@step
def check_direct_answer():
    texts = labels_of(app.messages)
    check("rooms-direct-answer", any("отвечаю в личку" in t for t in texts), texts[-4:])
    grab("rooms-2-direct.png")

    # отвечаем цитатой на последнее сообщение
    last_id = max(app.rows) if app.rows else None
    check("rooms-rows-tracked", last_id is not None, app.rows)
    if last_id:
        app._start_reply({"id": last_id, "nick": "Лена", "text": "отвечаю в личку"})


@step
def check_reply():
    check("rooms-reply-bar", app.reply_bar.winfo_ismapped(), "полоска ответа не видна")
    check("rooms-reply-remembered", app.reply_to is not None, app.reply_to)
    app.message_entry.insert(0, "это ответ цитатой")
    app._on_send()


@step
def check_reply_sent():
    check("rooms-reply-cleared", app.reply_to is None, app.reply_to)
    check("rooms-reply-bar-hidden", not app.reply_bar.winfo_ismapped())
    grab("rooms-3-reply.png")

    # возвращаемся в группу и ищем
    general = [c for c in app.conversations if c["kind"] == "group"][0]
    app._open(general["id"])


@step
def put_reaction():
    # ставим реакцию на последнее сообщение в ленте
    last = max(app.rows) if app.rows else None
    check("rooms-reaction-target", last is not None, app.rows)
    if last:
        app._react(last, "👍")


@step
def check_reaction():
    marks = {mid: summary for mid, summary in app.reactions.items() if summary}
    check("rooms-reaction-stored", bool(marks), app.reactions)
    check("rooms-reaction-mine",
          any(app.user["id"] in who
              for summary in marks.values() for who in summary.values()), marks)

    # повторное нажатие снимает
    message_id, summary = next(iter(marks.items()))
    app._react(message_id, list(summary)[0])


@step
def check_reaction_removed():
    check("rooms-reaction-toggled",
          all(not summary for summary in app.reactions.values()), app.reactions)


@step
def check_switch_back():
    check("rooms-switched-back", app.conversation == peer["room"],
          (app.conversation, peer.get("room")))
    texts = labels_of(app.messages)
    check("rooms-general-history-back", any("привет всем" in t for t in texts),
          texts[-4:])

    app.search_entry.insert(0, "привет")
    app._on_search()


@step
def check_search():
    texts = labels_of(app.messages)
    check("rooms-search-shows-count", any("Найдено" in t for t in texts), texts[:3])
    check("rooms-search-found", any("привет" in t for t in texts), texts[:6])
    grab("rooms-4-search.png")

    # удаляем своё сообщение
    app._open(peer["room"])


@step
def delete_own():
    own = [message_id for message_id in app.rows]
    check("rooms-has-rows", bool(own), app.rows)
    if own:
        app._delete_message(max(own))


@step
def check_deleted():
    texts = labels_of(app.messages)
    check("rooms-delete-applied", any("удалено" in t for t in texts), texts[-4:])
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
