"""Окно на двух языках: английский по умолчанию, переключение на лету."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("langgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8776")
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
time.sleep(2.0)

import store  # noqa: E402

CONFIG = Path(tempfile.mkdtemp(prefix="velix-lang-")) / "velix.json"
store.CONFIG_PATH = CONFIG

import i18n  # noqa: E402
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402
import protocol  # noqa: E402

# Соседка: с кем-то же надо завести группу, общего чата больше нет
peer = {}


def peer_thread():
    import asyncio
    import websockets

    async def run():
        async with websockets.connect("ws://localhost:8776",
                                      max_size=protocol.MAX_FRAME_SIZE) as ws:
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


def labels_of(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, gui.ctk.CTkLabel):
            found.append(child.cget("text"))
        if isinstance(child, (gui.ctk.CTkButton, gui.ctk.CTkSwitch)):
            found.append(child.cget("text"))
        found.extend(labels_of(child))
    return found


def texts():
    return [text for view in (app.auth_view, app.chat_view, app.settings_view)
            for text in labels_of(view) if text]


steps = []


def step(function):
    steps.append(function)
    return function


@step
def start_in_english():
    check("lang-default-english", i18n.language() == "en", i18n.language())
    words = texts()
    check("lang-auth-english", "SIGN IN" in words, words[:8])
    # Названия языков в списке остаются на своих языках — это не перевод
    foreign = [word for word in words if word not in i18n.NAMES.values()
               and any("Й" <= letter <= "я" for letter in word)]
    check("lang-auth-no-russian", not foreign, foreign)
    check("lang-placeholder-english",
          app.server_entry.cget("placeholder_text") == "Server address",
          app.server_entry.cget("placeholder_text"))

    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8776")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Gosha")
    app._on_primary()


@step
def make_group():
    check("lang-chat-opened", app.chat_view.winfo_ismapped(), "чат не открылся")
    words = texts()
    check("lang-empty-hint-english",
          any("Create a group" in word for word in words), words)
    app.pending_group = True
    app.network.send(protocol.group_request("Hiking", [peer["id"]]))


@step
def chat_in_english():
    words = texts()
    check("lang-chat-english", "Hiking" in words, words[:12])
    check("lang-composer-english",
          app.message_entry.cget("placeholder_text") == "Write a message…",
          app.message_entry.cget("placeholder_text"))

    app.message_entry.insert(0, "hello")
    app._on_send()
    app._show_settings()


@step
def switch_to_russian():
    words = texts()
    check("lang-settings-english", "Settings" in words and "Language" in words,
          words[:12])
    check("lang-picker-shows-current",
          app.language_picker.get() == "English", app.language_picker.get())

    app._on_language("Русский")


@step
def chat_in_russian():
    check("lang-switched", i18n.language() == "ru", i18n.language())
    check("lang-saved-in-settings",
          store.load(CONFIG)["settings"]["language"] == "ru",
          store.load(CONFIG).get("settings"))
    check("lang-stays-on-settings", app.settings_view.winfo_ismapped(),
          "после смены языка ушли с настроек")

    words = texts()
    check("lang-settings-russian", "Настройки" in words and "Язык" in words,
          words[:12])

    app._show_chat()
    words = texts()
    check("lang-chat-russian", "Hiking" in words, words[:10])
    check("lang-message-survived",
          any("hello" in word for word in labels_of(app.messages)),
          labels_of(app.messages))
    check("lang-composer-russian",
          app.message_entry.cget("placeholder_text") == "Написать сообщение…",
          app.message_entry.cget("placeholder_text"))
    check("lang-network-alive", app.network.websocket is not None,
          "после пересборки окна связь потерялась")

    # сообщение после пересборки доходит до сервера и обратно
    app.message_entry.insert(0, "снова привет")
    app._on_send()


@step
def message_after_rebuild():
    check("lang-send-after-rebuild",
          any("снова привет" in word for word in labels_of(app.messages)),
          labels_of(app.messages))
    app._on_leave()


@step
def error_in_russian():
    app._show_form(register=False)
    app.server_entry.delete(0, "end")
    app.server_entry.insert(0, "localhost:8776")
    app.login_entry.delete(0, "end")
    app.login_entry.insert(0, "gosha")
    app.password_entry.delete(0, "end")
    app.password_entry.insert(0, "не тот пароль")
    app._on_primary()


@step
def check_error_language():
    text = app.auth_error.cget("text")
    check("lang-server-error-russian", text == "Неверный логин или пароль.", text)
    app.destroy()


delay = 800
for function in steps:
    app.after(delay, function)
    delay += 3000

try:
    app.mainloop()

    # --- новое окно поднимает язык из настроек
    i18n.set_language("en")
    second = gui.VelixApp()
    second.update()
    check("lang-remembered-on-start", i18n.language() == "ru", i18n.language())
    words = [text for text in labels_of(second.auth_view) if text]
    check("lang-second-window-russian", "ВОЙТИ" in words, words[:8])

    # --- английская ошибка сервера у английского клиента
    second._on_language("English")
    second.update()
    second._on_authfail_test = None
    second._on_message({"type": "authfail", "text": "Неверный логин или пароль.",
                        "code": "bad_credentials"})
    second.update()
    check("lang-server-error-english",
          second.auth_error.cget("text") == "Wrong username or password.",
          second.auth_error.cget("text"))
    second.destroy()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
