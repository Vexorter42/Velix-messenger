"""Сколько ждёт человек, открывая переписку с двумя десятками фотографий."""

import asyncio
import io as bytes_io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("loadsandbox")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8796", VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8796"
СКОЛЬКО = 22

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

уголок = Path(tempfile.mkdtemp(prefix="velix-load-"))
store.CONFIG_PATH = уголок / "velix.json"
store.config_dir = lambda: уголок
store.save({"settings": {"language": "ru"}})

import mediacache  # noqa: E402
mediacache.forget()

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402


def тяжёлое_фото():
    """Похоже на снимок с телефона: шум, чтобы не сжималось в ничто."""
    картинка = Image.new("RGB", (1600, 1200))
    точки = картинка.load()
    for y in range(0, 1200, 4):
        for x in range(0, 1600, 4):
            цвет = (random.randint(0, 255), random.randint(0, 255),
                    random.randint(0, 255))
            for dy in range(4):
                for dx in range(4):
                    точки[x + dx, y + dy] = цвет
    holder = bytes_io.BytesIO()
    картинка.save(holder, "JPEG", quality=88)
    return holder.getvalue()


ФОТО = тяжёлое_фото()
print(f"фотография весит {len(ФОТО) // 1024} КБ, всего их {СКОЛЬКО}")
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
                if frame.get("type") == "conversation" and "room" not in peer:
                    room = frame["item"]["id"]
                    peer["room"] = room
                    for _ in range(СКОЛЬКО):
                        await ws.send(protocol.media_header(
                            "Лена", "image", "photo.jpg", len(ФОТО), room))
                        await ws.send(ФОТО)
                        await asyncio.sleep(0.05)
                    peer["готово"] = True

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
замеры = {}
steps = []


def step(function):
    steps.append(function)
    return function


def нарисовано():
    def обход(widget):
        счёт = 0
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("image") is not None:
                счёт += 1
            счёт += обход(child)
        return счёт
    return обход(app.messages)


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8796")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def make_group():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def wait_for_photos():
    pass


@step
def wait_more():
    print("сосед выложил всё:", peer.get("готово", False))


@step
def reopen():
    """Уходим и возвращаемся — как человек, открывающий чат заново."""
    app.conversation = None
    app._clear_messages()
    замеры["начало"] = time.perf_counter()
    app._open(peer["room"])
    следить()


def следить():
    сколько = нарисовано()
    прошло = time.perf_counter() - замеры["начало"]
    if "история" not in замеры and app.loaded_items:
        замеры["история"] = прошло
    if сколько >= СКОЛЬКО:
        замеры["все фото"] = прошло
        return
    if прошло < 60:
        app.after(50, следить)
    else:
        замеры["все фото"] = f"не дождались, нарисовано {сколько}"


@step
def reopen_cached():
    """Второй заход: фотографии уже лежат на диске."""
    замеры["первый раз"] = замеры.get("все фото")
    замеры.pop("история", None)
    app.conversation = None
    app._clear_messages()
    замеры["начало"] = time.perf_counter()
    app._open(peer["room"])
    следить()


@step
def report():
    print()
    print("== открытие переписки с фотографиями ==")
    print(f"история появилась через   {замеры.get('история', '?'):.2f} с"
          if isinstance(замеры.get("история"), float) else замеры.get("история"))
    сперва = замеры.get("первый раз")
    потом = замеры.get("все фото")
    print(f"впервые фотографии видны через {сперва:.2f} с"
          if isinstance(сперва, float) else f"впервые: {сперва}")
    print(f"со второго раза (с диска)     {потом:.2f} с"
          if isinstance(потом, float) else f"со второго раза: {потом}")
    print(f"по сети в первый раз ≈ {len(ФОТО) * СКОЛЬКО // 1024} КБ, "
          f"во второй — нисколько")
    папка = mediacache.cache_dir()
    файлов = len([one for one in папка.iterdir() if one.is_file()])
    print(f"в кэше на диске: {mediacache.size() // 1024} КБ, файлов {файлов}")


@step
def finish():
    app.destroy()


delay = 1000
for function in steps:
    app.after(delay, function)
    delay += 6000

try:
    app.mainloop()
finally:
    server.terminate()
    server.wait(timeout=5)
