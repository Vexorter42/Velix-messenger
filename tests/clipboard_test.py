"""Вставка из буфера обмена: картинка отправляется, текст вставляется как текст."""

import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("clipsandbox")
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


def put_image_in_clipboard(image):
    """Кладёт картинку в буфер в формате CF_DIB — это BMP без первых 14 байт."""
    import win32clipboard
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "BMP")
    dib = buffer.getvalue()[14:]

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def put_files_in_clipboard(paths):
    """Кладёт в буфер список файлов, как это делает проводник (CF_HDROP)."""
    import struct

    import win32clipboard
    names = "\0".join(str(p) for p in paths) + "\0\0"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # DROPFILES, широкие символы
    payload = header + names.encode("utf-16-le")

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, payload)
    finally:
        win32clipboard.CloseClipboard()


if SANDBOX.exists():
    shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.8)

import store  # noqa: E402
import tempfile
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-clip-")) / "velix.json"

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

sample = SANDBOX / "картинка-файлом.png"
Image.new("RGB", (80, 60), (200, 120, 40)).save(sample)

app = gui.VelixApp()

steps = []


def step(function):
    steps.append(function)
    return function


@step
def connect():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost")
    app.login_entry.insert(0, "nina")
    app.password_entry.insert(0, "пароль123")
    app.name_entry.insert(0, "Нина")
    app._on_primary()


@step
def paste_picture():
    put_image_in_clipboard(Image.new("RGB", (140, 100), (90, 200, 120)))
    before = len(app.images)
    handled = app._paste_from_clipboard()
    app.update()
    check("clipboard-image-handled", handled == "break", handled)
    check("clipboard-image-shown", len(app.images) > before,
          f"картинок было {before}, стало {len(app.images)}")


@step
def paste_text():
    app.clipboard_clear()
    app.clipboard_append("обычный текст")
    app.update()
    before = len(app.images)
    handled = app._paste_from_clipboard()
    check("clipboard-text-passthrough", handled is None, handled)
    check("clipboard-text-no-picture", len(app.images) == before,
          "текст из буфера ушёл как картинка")


@step
def paste_files():
    put_files_in_clipboard([sample])
    before = len(app.images)
    handled = app._paste_from_clipboard()
    app.update()
    check("clipboard-files-handled", handled == "break", handled)
    check("clipboard-files-shown", len(app.images) > before,
          "файл из буфера не отправился")


@step
def finish():
    app.destroy()


delay = 700
for function in steps:
    app.after(delay, function)
    delay += 1800

try:
    app.mainloop()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
