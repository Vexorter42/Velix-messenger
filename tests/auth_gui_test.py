"""Окно: регистрация, вход по сохранённому аккаунту, профиль, аватарки."""

# Эту проверку гоняем в одиночку: она ждёт сообщение от второго клиента по часам, а не по событию,
# а под нагрузкой от соседок часы врут
ПООДИНОЧКЕ = True


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

import harness

from PIL import Image, ImageDraw, ImageGrab

REPO = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("authgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# --- песочница с сервером
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

# Настройки клиента уводим во временный файл, чтобы не трогать настоящие
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-cfg-")) / "velix.json"
# Набор писался под русский интерфейс, а по умолчанию теперь
# английский: язык задаём явно
store.save({"settings": {"language": "ru"}})
# Набор писался под русский интерфейс, а по умолчанию теперь
# английский: язык задаём явно
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

avatar_file = SANDBOX / "лицо.png"
face = Image.new("RGB", (400, 400), (60, 140, 90))
ImageDraw.Draw(face).ellipse([80, 80, 320, 320], fill=(250, 210, 120))
face.save(avatar_file)


# --- сосед по чату: регистрируется, ставит себе аватарку и пишет
peer_state = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect("ws://localhost:8765",
                                      max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "пароль123", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer_state["user"] = welcome["user"]

            # Общего чата нет: дожидаемся Гошу и зовём его в группу
            others = []
            while not others:
                frame = protocol.decode(await ws.recv())
                if frame.get("type") == "people":
                    others = [person["id"] for person in frame["items"]
                              if person["id"] != peer_state["user"]["id"]]
            await ws.send(protocol.group_request("Общая", others))
            frame = None
            while (frame or {}).get("type") != "conversation":
                frame = protocol.decode(await ws.recv())
            peer_state["room"] = frame["item"]["id"]

            picture = io.BytesIO()
            Image.new("RGB", (200, 200), (220, 90, 130)).save(picture, "PNG")
            data = picture.getvalue()
            await ws.send(protocol.avatar_header("лена.png", len(data)))
            await ws.send(data)
            answer = {}
            while answer.get("type") != "profile":
                answer = protocol.decode(await ws.recv())
            peer_state["avatar"] = answer["user"]["avatar"]

            await asyncio.sleep(6)
            await ws.send(protocol.text_message("Лена", "привет, я с аватаркой",
                                                peer_state["room"]))
            await asyncio.sleep(60)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        pass


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(2.5)

app = gui.VelixApp()
harness.тихое_окно(app)


def grab(name):
    # Поднимаем окно наверх: иначе в кадр попадёт то, что лежит поверх него
    app.lift()
    harness.тихое_окно(app)
    app.update_idletasks()
    app.update()
    time.sleep(0.6)
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
def open_register():
    check("auth-form-first-run", app.auth_view.winfo_ismapped(),
          "экран входа не показан")
    grab("auth-1-login.png")
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    grab("auth-2-register.png")
    app._on_primary()


@step
def check_registered():
    check("auth-chat-opened", app.chat_view.winfo_ismapped(), "чат не открылся")
    check("auth-user-known", app.user.get("login") == "gosha"
          and app.user.get("name") == "Гоша", app.user)
    check("auth-token-issued", bool(app.token), "токен не пришёл")
    check("auth-name-in-sidebar", app.my_name.cget("text") == "Гоша",
          app.my_name.cget("text"))

    saved = store.load(store.CONFIG_PATH)["accounts"]
    check("auth-account-saved", saved and saved[0]["login"] == "gosha", saved)
    check("auth-password-not-saved", "секрет123" not in str(saved), "пароль в файле!")


@step
def edit_profile():
    app._show_profile()
    app.profile_name.delete(0, "end")
    app.profile_name.insert(0, "Гоша Петров")
    app.profile_bio.insert("1.0", "живу в Челябинске, держу малину")
    app._save_profile()


@step
def check_profile():
    check("profile-name-updated", app.user.get("name") == "Гоша Петров", app.user)
    check("profile-bio-updated", "малину" in app.user.get("bio", ""), app.user)
    check("profile-sidebar-updated", app.my_name.cget("text") == "Гоша Петров",
          app.my_name.cget("text"))
    saved = store.load(store.CONFIG_PATH)["accounts"][0]
    check("profile-name-in-store", saved["name"] == "Гоша Петров", saved)

    # ставим себе фото
    app._send_avatar_for_test = True
    data = avatar_file.read_bytes()
    app.network.send(protocol.avatar_header("лицо.png", len(data)), data)


@step
def check_avatar():
    check("avatar-assigned", bool(app.user.get("avatar")), app.user)
    grab("auth-3-profile.png")
    app._show_chat()


@step
def check_peer_avatar():
    # сообщение соседа с аватаркой уже должно было прийти
    texts = [text for row in app.messages.winfo_children() for text in labels_of(row)]
    check("chat-peer-message", any("аватаркой" in text for text in texts), texts[-4:])
    check("chat-peer-avatar-loaded",
          any(key[0] == peer_state.get("avatar") for key in app.avatar_cache),
          list(app.avatar_cache))
    check("chat-avatar-not-pending", not app.avatar_waiters, app.avatar_waiters)
    app.message_entry.insert(0, "и я с фото")
    app._on_send()
    grab("auth-4-chat.png")


@step
def switch_account():
    app._on_leave()
    app.update()          # без этого Tkinter ещё не успел показать экран
    check("switch-shows-accounts", app.auth_view.winfo_ismapped(), "не вернулись к входу")
    rows = app.saved_box.winfo_children()
    check("switch-lists-account", len(rows) >= 1, rows)
    grab("auth-5-accounts.png")

    saved = store.load(store.CONFIG_PATH)["accounts"][0]
    app._enter_saved(saved)


@step
def check_token_login():
    check("token-login-works", app.chat_view.winfo_ismapped(), "вход по токену не сработал")
    check("token-login-same-user", app.user.get("login") == "gosha", app.user)
    check("token-login-keeps-avatar", bool(app.user.get("avatar")), app.user)


@step
def check_wrong_password():
    app._on_leave()
    app._show_form(register=False)
    app.server_entry.delete(0, "end")
    app.server_entry.insert(0, "localhost")
    app.login_entry.delete(0, "end")
    app.login_entry.insert(0, "gosha")
    app.password_entry.delete(0, "end")
    app.password_entry.insert(0, "не тот пароль")
    app._on_primary()


@step
def check_error_shown():
    text = app.auth_error.cget("text")
    check("wrong-password-message", "Неверный логин или пароль" in text, text)
    check("wrong-password-stays", app.auth_view.winfo_ismapped(), "ушли с экрана входа")
    grab("auth-6-error.png")


@step
def finish():
    app.destroy()


delay = 800
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
