"""Клиент на Kivy: окно и сеть в одном цикле asyncio.

Проверка не про красоту, а про то, ради чего он затевался: обработчик
нажатия пишет await прямо в том же цикле, в котором рисуется окно, — без
отдельного потока и без очереди, как приходится в окне на Tk.

Окно тут настоящее — без него Kivy просто завершает работу, — но уехавшее
за край экрана, как и у остальных оконных проверок.
"""

# Эту проверку гоняем в одиночку: Kivy поднимает настоящее окно с графическим
# слоем, и оно забирает себе ввод — а соседки-оконные ищут виджет под мышью
# и нашли бы чужое окно
ПООДИНОЧКЕ = True


import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import harness

os.environ["KIVY_NO_ARGS"] = "1"
harness.тихое_окно_kivy()

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
SANDBOX = Path(__file__).with_name("kivysandbox")
PORT = 8851

sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

import tempfile  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-kivy-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})

import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if SANDBOX.exists():
    shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
    shutil.copy(REPO / name, SANDBOX / name)

ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT=str(PORT), VELIX_OPEN_REGISTRATION="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
сервер = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
harness.дождаться(PORT)

import kivyclient  # noqa: E402


async def завести_собеседника():
    """Второй человек нужен, чтобы было с кем переписываться."""
    import websockets
    ws = await websockets.connect(f"ws://localhost:{PORT}",
                                  max_size=protocol.MAX_FRAME_SIZE)
    await ws.send(protocol.register_message("lena", "parol12345", "Лена"))
    while True:
        кадр = protocol.decode(await ws.recv())
        if кадр and кадр.get("type") == "welcome":
            return ws, кадр["user"]["id"]


async def дождаться(условие, предел=20):
    конец = asyncio.get_running_loop().time() + предел
    while asyncio.get_running_loop().time() < конец:
        if условие():
            return True
        await asyncio.sleep(0.05)
    return False


async def проверки(приложение):
    # Даём окну собраться
    await дождаться(lambda: приложение.подпись is not None)

    лена, номер_лены = await завести_собеседника()

    приложение.поле_сервера.text = f"localhost:{PORT}"
    приложение.поле_имени.text = "gosha"
    приложение.поле_пароля.text = "parol12345"

    # Регистрации в этом клиенте пока нет: заводим Гошу отдельно, а входим
    # уже через кнопку — тем же путём, что и человек
    import websockets
    первый = await websockets.connect(f"ws://localhost:{PORT}",
                                      max_size=protocol.MAX_FRAME_SIZE)
    await первый.send(protocol.register_message("gosha", "parol12345", "Гоша"))
    while True:
        кадр = protocol.decode(await первый.recv())
        if кадр and кадр.get("type") == "welcome":
            break
    await первый.send(protocol.direct_request(номер_лены))
    await asyncio.sleep(0.6)
    await первый.close()

    await приложение.войти()
    вошли = await дождаться(lambda: приложение.я.get("id") is not None)
    check("kivy-logged-in", вошли, приложение.подпись.text)
    check("kivy-chat-shown", приложение.лента is not None)

    есть = await дождаться(lambda: bool(приложение.переписки))
    check("kivy-rooms-listed", есть, приложение.переписки)

    открылась = await дождаться(lambda: приложение.открыта is not None)
    check("kivy-room-opened", открылась, приложение.открыта)

    # --- пишем сами
    приложение.строка.text = "привет из кивви"
    await приложение.отправить()
    дошло = False
    конец = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < конец:
        кадр = protocol.decode(await asyncio.wait_for(лена.recv(), timeout=20))
        if кадр and кадр.get("type") == "text":
            дошло = кадр.get("text") == "привет из кивви"
            break
    check("kivy-message-sent", дошло)

    # --- и получаем
    было = len(приложение.лента.children)
    await лена.send(protocol.text_message("Лена", "и тебе привет",
                                          приложение.открыта))
    пришло = await дождаться(lambda: len(приложение.лента.children) > было)
    check("kivy-message-received", пришло,
          (было, len(приложение.лента.children)))

    подписи = []
    for пузырь in приложение.лента.children:
        for внутри in пузырь.children:
            текст = getattr(внутри, "text", "")
            if текст:
                подписи.append(текст)
    check("kivy-message-drawn", "и тебе привет" in подписи, подписи)

    # --- и всё это — в одном цикле с рисованием
    #
    # Спрашиваем не себя, а саму рисовалку: Clock тикает изнутри Kivy, и
    # если бы окно жило отдельным потоком, там оказался бы либо чужой цикл,
    # либо никакого — get_running_loop сказал бы RuntimeError
    from kivy.clock import Clock

    из_рисовалки = {}

    def кто_рисует(_):
        try:
            из_рисовалки["цикл"] = asyncio.get_running_loop()
        except RuntimeError:
            из_рисовалки["цикл"] = None

    Clock.schedule_once(кто_рисует, 0)
    ответила = await дождаться(lambda: "цикл" in из_рисовалки)
    check("kivy-one-loop",
          ответила and из_рисовалки["цикл"] is asyncio.get_running_loop(),
          из_рисовалки.get("цикл", "рисовалка не отозвалась"))

    await лена.close()


async def главное():
    приложение = kivyclient.Velix()
    работа = asyncio.ensure_future(приложение.async_run(async_lib="asyncio"))
    try:
        await проверки(приложение)
    finally:
        приложение.stop()
        try:
            await asyncio.wait_for(работа, timeout=10)
        except Exception:
            pass


try:
    asyncio.run(главное())
finally:
    сервер.terminate()
    try:
        сервер.wait(timeout=15)
    except subprocess.TimeoutExpired:
        сервер.kill()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    shutil.rmtree(уголок, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
