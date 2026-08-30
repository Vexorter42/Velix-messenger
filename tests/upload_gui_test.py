"""Окно: отправка большого файла кусками и его получение."""

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
SANDBOX = Path(__file__).with_name("uploadgui")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8798", VELIX_OPEN_REGISTRATION="1", VELIX_ADMIN="gosha")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
URI = "ws://localhost:8798"

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
time.sleep(2.0)

import protocol  # noqa: E402
import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-upgui-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

# Видео на 60 МБ — пятнадцать кусков: на одном-двух беда не проявится
ВИДЕО = bytes(range(256)) * (60 * 1024 * 1024 // 256)
ФАЙЛ = Path(tempfile.gettempdir()) / "поход.mp4"
ФАЙЛ.write_bytes(ВИДЕО)

peer = {}


def peer_thread():
    async def run():
        import websockets
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
            await ws.send(protocol.register_message("lena", "parol12345", "Лена"))
            welcome = protocol.decode(await ws.recv())
            peer["id"] = welcome["user"]["id"]

            собрано = b""
            ждём = 0
            while True:
                кадр = await ws.recv()
                if isinstance(кадр, (bytes, bytearray)):
                    if ждём:
                        собрано += кадр
                        if len(собрано) >= ждём:
                            peer["скачано"] = собрано
                            ждём = 0
                    continue

                frame = protocol.decode(кадр)
                if frame is None:
                    continue
                if frame.get("type") == "media":
                    peer["видео"] = frame
                    await ws.send(protocol.fetch_request(frame["media"]))
                elif frame.get("type") == "blob":
                    ждём = frame.get("size") or 0
                    собрано = b""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception as error:
        print("сосед отвалился:", error)


threading.Thread(target=peer_thread, daemon=True).start()
time.sleep(1.5)

app = gui.VelixApp()
app.attributes("-topmost", True)
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


@step
def sign_up():
    app._show_form(register=True)
    app.server_entry.insert(0, "localhost:8798")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app.name_entry.insert(0, "Гоша")
    app._on_primary()


@step
def check_limits():
    for window in [c for c in app.winfo_children()
                   if isinstance(c, gui.ctk.CTkToplevel)]:
        window.destroy()
    check("upgui-limits-known",
          app.limits.get("video") == protocol.DEFAULT_VIDEO_LIMIT, app.limits)
    app.pending_group = True
    app.network.send(protocol.group_request("Поход", [peer["id"]]))


@step
def send_video():
    app._send_file(ФАЙЛ)
    check("upgui-upload-started", len(app.sending) == 1, app.sending)
    grab("upload-1-progress.png")


@step
def chat_while_sending():
    # Пишем прямо во время заливки: кадры не должны перемешаться
    app.message_entry.insert(0, "а я пока пишу")
    app._on_send()


@step
def wait_a_bit():
    pass


@step
def wait_more():
    pass


@step
def check_sent():
    check("upgui-upload-finished", not app.sending, app.sending)
    видео = next((one for one in app.loaded_items
                  if one.get("kind") == "video"), {})
    check("upgui-item-has-media", bool(видео.get("media")), видео)
    check("upgui-kind-video", видео.get("kind") == "video", видео)
    # Написанное во время заливки тоже должно дойти
    письмо = next((one for one in app.loaded_items
                   if one.get("text") == "а я пока пишу"), {})
    check("upgui-chat-while-sending", bool(письмо.get("id")), письмо)
    grab("upload-2-sent.png")


@step
def check_peer():
    пришло = peer.get("видео") or {}
    check("upgui-peer-got-frame", пришло.get("size") == len(ВИДЕО), пришло)
    check("upgui-peer-downloaded", peer.get("скачано") == ВИДЕО,
          f"скачано {len(peer.get('скачано') or b'')} из {len(ВИДЕО)}")


@step
def too_big():
    огромный = Path(tempfile.gettempdir()) / "огромное.bin"
    огромный.write_bytes(b"x" * 1024)
    # Подменяем предел, чтобы не писать на диск гигабайты
    app.limits = dict(app.limits, file=512)
    app._send_file(огромный)
    check("upgui-too-big-refused", not app.sending, app.sending)
    app.limits = dict(app.limits, file=protocol.DEFAULT_FILE_LIMIT)


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
