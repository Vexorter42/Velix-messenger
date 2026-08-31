"""Полный экран: листаем вложения переписки и смотрим видео внутри."""

import asyncio
import io as bytes_io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import harness

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-gal-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

from PIL import Image  # noqa: E402

import protocol  # noqa: E402
import gui  # noqa: E402
import videoplayer  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8819"))


def снимок(цвет):
    холст = bytes_io.BytesIO()
    Image.new("RGB", (600, 400), цвет).save(холст, "PNG")
    return холст.getvalue()


def кино(путь, секунд=10, ширина=320, высота=240, частота=15):
    from ffpyplayer.writer import MediaWriter
    from ffpyplayer.pic import Image as FFImage

    писарь = MediaWriter(str(путь), [{
        "pix_fmt_in": "rgb24", "pix_fmt_out": "yuv420p",
        "width_in": ширина, "height_in": высота,
        "codec": "mpeg4", "frame_rate": (частота, 1)}])
    for номер in range(секунд * частота):
        краски = bytes([(номер * 6) % 256, 70, 180] * (ширина * высота))
        писарь.write_frame(img=FFImage(plane_buffers=[краски], pix_fmt="rgb24",
                                       size=(ширина, высота)),
                           pts=номер / частота, stream=0)
    писарь.close()
    return путь.read_bytes()


КАРТИНКИ = {"aaa111": снимок((200, 90, 60)),
            "bbb222": снимок((60, 140, 200)),
            "ccc333": снимок((90, 190, 110))}
РОЛИК = кино(Path(tempfile.mkdtemp(prefix="velix-kino-")) / "proba.mp4")
ВСЁ = dict(КАРТИНКИ, ddd444=РОЛИК)

ЛЕНТА = [
    {"id": 1, "nick": "Лена", "kind": "image", "media": "aaa111",
     "name": "one.png", "size": len(КАРТИНКИ["aaa111"]),
     "at": "2026-08-26T09:00:00+00:00", "user": 2},
    {"id": 2, "nick": "Лена", "kind": "image", "media": "bbb222",
     "name": "two.png", "size": len(КАРТИНКИ["bbb222"]),
     "at": "2026-08-26T09:01:00+00:00", "user": 2},
    {"id": 3, "nick": "Гоша", "kind": "text", "text": "а вот и видео",
     "at": "2026-08-26T09:02:00+00:00", "user": 1},
    {"id": 4, "nick": "Лена", "kind": "image", "media": "ccc333",
     "name": "three.png", "size": len(КАРТИНКИ["ccc333"]),
     "at": "2026-08-26T09:03:00+00:00", "user": 2},
    {"id": 5, "nick": "Лена", "kind": "video", "media": "ddd444",
     "name": "proba.mp4", "size": len(РОЛИК),
     "at": "2026-08-26T09:04:00+00:00", "user": 2},
]


async def притворщик(websocket):
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 3, "kind": "direct", "title": "Лена", "user": 2},
    ]))

    while True:
        кадр = protocol.decode(await websocket.recv())
        if кадр is None:
            continue
        if кадр.get("type") == "open":
            await websocket.send(protocol.history_page(3, ЛЕНТА, {}, False))
        elif кадр.get("type") == "fetch":
            номер = кадр["id"]
            данные = ВСЁ.get(номер)
            if данные is None:
                continue
            куски = [данные[место:место + protocol.CHUNK_SIZE]
                     for место in range(0, len(данные), protocol.CHUNK_SIZE)] or [b""]
            вид = "video" if номер == "ddd444" else "image"
            await websocket.send(protocol.blob_header(
                номер, вид, "file", len(данные), len(куски)))
            for кусок in куски:
                await websocket.send(кусок)


def сервер():
    async def run():
        import websockets
        async with websockets.serve(притворщик, "localhost", PORT,
                                    max_size=protocol.MAX_FRAME_SIZE):
            await asyncio.Future()

    петля = asyncio.new_event_loop()
    asyncio.set_event_loop(петля)
    петля.run_until_complete(run())


threading.Thread(target=сервер, daemon=True).start()
harness.дождаться(8819)

app = gui.VelixApp()
app.geometry("1000x700")
harness.тихое_окно(app)
steps = []


def step(function):
    steps.append(function)
    return function


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def wait_for_media():
    pass


@step
def check_gallery():
    номера = [one["media"] for one in app.gallery]
    check("gal-collected", номера == ["aaa111", "bbb222", "ccc333", "ddd444"],
          номера)


@step
def open_second():
    app._show_full(КАРТИНКИ["bbb222"], "image", "bbb222")


@step
def check_open():
    check("gal-viewer-open", app.viewer is not None)
    check("gal-starts-at-clicked", app.viewer_at == 1, app.viewer_at)
    check("gal-counter", app.viewer_counter.cget("text") == "2 / 4",
          app.viewer_counter.cget("text"))


@step
def step_right():
    app._viewer_step(1)


@step
def check_next():
    check("gal-step-right", app.viewer_at == 2, app.viewer_at)
    check("gal-counter-moves", app.viewer_counter.cget("text") == "3 / 4",
          app.viewer_counter.cget("text"))
    check("gal-picture-drawn",
          any(isinstance(one, gui.ctk.CTkLabel) and one.cget("image")
              for one in app.viewer_stage.winfo_children()),
          [type(one).__name__ for one in app.viewer_stage.winfo_children()])


@step
def step_to_video():
    app._viewer_step(1)


@step
def check_video_starts():
    check("gal-at-video", app.viewer_at == 3, app.viewer_at)
    check("gal-player-made", app.video is not None and app.video.error is None,
          app.video.error if app.video else "проигрывателя нет")


@step
def check_video_plays():
    игрок = app.video
    check("gal-video-moves", игрок is not None and игрок.position > 0.2,
          игрок.position if игрок else None)
    check("gal-video-duration", игрок is not None and игрок.duration > 8.0,
          игрок.duration if игрок else None)
    держим = игрок.position
    игрок.toggle()
    app.after(700, lambda: check("gal-video-pauses",
                                 abs(игрок.position - держим) < 0.5,
                                 (держим, игрок.position)))


@step
def wrap_around():
    app._viewer_step(1)


@step
def check_wrap():
    check("gal-wraps", app.viewer_at == 0, app.viewer_at)
    check("gal-video-stopped-on-leave", app.video is None, app.video)


@step
def close_viewer():
    app._close_full(app.viewer)


@step
def check_closed():
    check("gal-closed", app.viewer is None and app.video is None)


@step
def finish():
    app.destroy()


delay = 900
паузы = {"wait_for_media": 3000, "check_video_starts": 2600}
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1500)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
