"""Окно: голосовое и кружочек — запись, отправка и показ в ленте.

Микрофон здесь не трогаем: настоящая запись проверяется отдельно, а тут
важно другое — что полоска записи появляется и уходит, что отправляется
именно голос, а не «файл», и что пришедшее рисуется кнопкой со временем,
а не строчкой «вложение».
"""

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-voice-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-voicecache-")

import protocol  # noqa: E402
import recorder  # noqa: E402
import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


PORT = int(os.environ.get("VELIX_TESTPORT", "8833"))

# Настоящий ogg-файл нам не нужен: до проигрывателя дело не дойдёт, а вот
# путь по всей отправке — от кнопки до кадра на сервере — пройти должен
ЗАПИСЬ = b"OggS" + b"\x00" * 4000

ЛЕНТА = [
    {"id": 5, "nick": "Лена", "kind": "voice", "media": "abc123",
     "name": "voice.ogg", "size": 4004, "seconds": 7,
     "at": "2026-08-30T09:00:00+00:00", "user": 2},
    {"id": 6, "nick": "Лена", "kind": "circle", "media": "def456",
     "name": "circle.mp4", "size": 90000, "seconds": 12,
     "at": "2026-08-30T09:01:00+00:00", "user": 2},
]

видано = []


async def притворщик(websocket):
    await websocket.recv()
    await websocket.send(protocol.welcome_message(
        {"id": 1, "login": "gosha", "name": "Гоша"}, "токен"))
    await websocket.send(protocol.conversations_message([
        {"id": 3, "kind": "direct", "title": "Лена", "user": 2},
    ]))

    while True:
        кадр = await websocket.recv()
        разобрано = protocol.decode(кадр) if isinstance(кадр, str) else None
        if разобрано is None:
            видано.append(("байты", len(кадр)))
            continue
        if разобрано.get("type") == "open":
            await websocket.send(protocol.history_page(3, ЛЕНТА, {}, False))
        elif разобрано.get("type") == "media":
            видано.append(("описание", разобрано))


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
time.sleep(1.2)


# ------------------------------------------------- подставная запись
#
# Настоящий микрофон в проверке не нужен: важно, что окно правильно ведёт
# себя вокруг записи, а не то, как звучит комната.

class ПодставнаяЗапись:
    def __init__(self, kind, microphone, camera=None, folder=None):
        self.kind = kind
        self.error = None
        self.started = time.monotonic()
        self.path = уголок / ("проба.ogg" if kind == "voice" else "проба.mp4")
        self.path.write_bytes(ЗАПИСЬ)
        self._жива = True

    @property
    def seconds(self):
        return time.monotonic() - self.started

    @property
    def running(self):
        return self._жива

    def stop(self):
        self._жива = False
        self.seconds_done = 3
        return self.path

    def cancel(self):
        self._жива = False
        self.forget()

    def forget(self):
        if self.path.exists():
            self.path.unlink()


recorder.Recording = ПодставнаяЗапись

app = gui.VelixApp()
app.attributes("-topmost", True)
steps = []


def step(function):
    steps.append(function)
    return function


def подписи():
    найдено = []

    def обход(widget):
        for child in widget.winfo_children():
            текст = ""
            try:
                текст = str(child.cget("text"))
            except Exception:
                текст = ""
            if текст:
                найдено.append(текст)
            обход(child)

    обход(app.messages)
    return найдено


@step
def sign_in():
    app._show_form(register=False)
    app.server_entry.insert(0, f"localhost:{PORT}")
    app.login_entry.insert(0, "gosha")
    app.password_entry.insert(0, "secret123")
    app._on_primary()


@step
def открыть():
    app._open(3, force=True)


@step
def что_в_ленте():
    строки = подписи()
    check("voice-gui-shows-duration", "0:00 / 0:07" in строки, строки)
    check("voice-gui-has-play-button", "▶" in строки, строки)
    check("voice-gui-circle-shown",
          "0:00 / 0:12" in строки and "кружочек" in строки, строки)
    check("voice-gui-not-called-attachment",
          not any("вложение" in one for one in строки), строки)


@step
def начать_запись():
    check("voice-gui-button-exists", app.voice_button.winfo_exists())
    app._start_recording("voice")


@step
def идёт_ли():
    # Разложить виджеты Tk успевает не сразу, а проверка смотрит сразу
    app.update()
    check("voice-gui-bar-shown", app.record_bar.winfo_ismapped(),
          "полоска записи не появилась")
    check("voice-gui-bar-counts", "Записываю голос" in app.record_label.cget("text"),
          app.record_label.cget("text"))
    app._finish_recording()


@step
def что_ушло():
    описания = [кадр for вид, кадр in видано if вид == "описание"]
    байты = [сколько for вид, сколько in видано if вид == "байты"]
    check("voice-gui-header-sent", bool(описания), видано)
    if описания:
        check("voice-gui-kind-is-voice", описания[-1].get("kind") == "voice",
              описания[-1])
        check("voice-gui-seconds-sent", описания[-1].get("seconds") == 3,
              описания[-1])
    check("voice-gui-bytes-sent", байты and байты[-1] == len(ЗАПИСЬ), байты)
    app.update()
    check("voice-gui-bar-hidden", not app.record_bar.winfo_ismapped(),
          "полоска записи осталась")
    check("voice-gui-composer-back", app.composer.winfo_ismapped(),
          "строка ввода не вернулась")


@step
def отмена():
    app._start_recording("voice")


@step
def проверить_отмену():
    было = len([кадр for вид, кадр in видано if вид == "описание"])
    app._cancel_recording()
    после = len([кадр for вид, кадр in видано if вид == "описание"])
    check("voice-gui-cancel-sends-nothing", было == после, (было, после))
    check("voice-gui-cancel-hides-bar", not app.record_bar.winfo_ismapped())
    check("voice-gui-nothing-left-recording", app.recording is None)


@step
def finish():
    app.destroy()


delay = 900
for function in steps:
    app.after(delay, function)
    delay += 1500

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
