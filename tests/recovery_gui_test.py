"""Окно: код при регистрации и вход по «Забыли пароль?»."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import harness

from PIL import ImageGrab

REPO = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).with_name("shots")
SANDBOX = Path(__file__).with_name("recovergui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8788")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

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

import store  # noqa: E402

store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-recover-")) / "velix.json"
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

app = gui.VelixApp()
harness.тихое_окно(app)
saved = {}


def grab(name):
    app.lift()
    harness.тихое_окно(app)
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


def toplevels():
    return [child for child in app.winfo_children()
            if isinstance(child, gui.ctk.CTkToplevel)]


steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_up():
    check("recover-button-on-login",
          app.forgot_button.winfo_ismapped(), "кнопки «Забыли пароль?» нет")
    app._show_form(register=True)
    check("recover-button-hidden-on-register",
          not app.forgot_button.winfo_ismapped(),
          "кнопка показана при регистрации")

    app.server_entry.insert(0, "localhost:8788")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "секрет123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def check_code_shown():
    windows = toplevels()
    check("recover-code-window", len(windows) == 1, windows)
    if not windows:
        return

    labels = [label.cget("text") for label in
              widgets_of(windows[0], gui.ctk.CTkLabel)]
    codes = [text for text in labels if len(text) == 19 and text.count("-") == 3]
    check("recover-code-visible", bool(codes), labels)
    saved["code"] = codes[0] if codes else ""

    windows[0].destroy()


@step
def leave():
    app._on_leave()


@step
def open_recovery():
    app._show_form(recover=True)
    app.update()
    check("recover-form-has-code-field", app.code_entry.winfo_ismapped(),
          "поля кода нет")
    check("recover-form-title", "Восстановление" in app.auth_subtitle.cget("text"),
          app.auth_subtitle.cget("text"))
    check("recover-password-hint",
          app.password_entry.cget("placeholder_text") == "Новый пароль",
          app.password_entry.cget("placeholder_text"))

    app.server_entry.delete(0, "end")
    app.server_entry.insert(0, "localhost:8788")
    app.login_entry.delete(0, "end")
    app.login_entry.insert(0, "gosha")
    app.password_entry.delete(0, "end")
    app.password_entry.insert(0, "совсемдругой1")
    grab("recover-2-form.png")


@step
def wrong_code():
    app.code_entry.delete(0, "end")
    app.code_entry.insert(0, "AAAA-BBBB-CCCC-DDDD")
    app._on_primary()


@step
def check_wrong():
    check("recover-wrong-code-message",
          "не подошёл" in app.auth_error.cget("text"), app.auth_error.cget("text"))
    check("recover-stays-on-form", app.auth_view.winfo_ismapped(),
          "ушли с экрана входа")

    app.code_entry.delete(0, "end")
    app.code_entry.insert(0, saved.get("code", ""))
    app.password_entry.delete(0, "end")
    app.password_entry.insert(0, "совсемдругой1")
    app._on_primary()


@step
def check_recovered():
    check("recover-lets-in", app.chat_view.winfo_ismapped(), "чат не открылся")
    check("recover-same-account", app.user.get("login") == "gosha", app.user)
    windows = toplevels()
    fresh = []
    for window in windows:
        fresh += [label.cget("text") for label in
                  widgets_of(window, gui.ctk.CTkLabel)
                  if len(label.cget("text")) == 19]
    check("recover-gives-new-code",
          bool(fresh) and fresh[0] != saved.get("code"), fresh)
    grab("recover-3-done.png")
    for window in windows:
        window.destroy()


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
