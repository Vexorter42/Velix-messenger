"""Переписки: личные, присутствие, страницы, цитаты, удаление, поиск."""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("roomsandbox")
URI = "ws://localhost:8771"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8771")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402
import storage  # noqa: E402

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

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)


async def read(ws, timeout=15):
    return protocol.decode(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def sign_in(ws, login):
    """Регистрирует и вычитывает приветственный набор кадров.

    У новичка переписок нет, поэтому истории в приветствии тоже нет.
    """
    await ws.send(protocol.register_message(login, "пароль123", login))
    welcome = await read(ws)
    assert welcome["type"] == "welcome", welcome
    conversations = await read(ws)
    people = await read(ws)
    return welcome, conversations, people


async def read_until(ws, kind, timeout=15):
    """Читает кадры, пока не попадётся нужный: в очереди могут лежать
    служебные вроде присутствия или обновлённого списка переписок."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def collect(ws, timeout=0.8):
    frames = []
    try:
        while True:
            frames.append(protocol.decode(await asyncio.wait_for(ws.recv(), timeout=timeout)))
    except asyncio.TimeoutError:
        pass
    return frames


async def scenario():
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as gosha:
        welcome, conversations, people = await sign_in(gosha, "gosha")
        gosha_id = welcome["user"]["id"]

        check("welcome-brings-conversations", conversations["type"] == "conversations"
              and conversations["items"] == [], conversations)
        check("welcome-brings-people", people["type"] == "people"
              and people["items"][0]["login"] == "gosha"
              and gosha_id in people["online"], people)

        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as lena:
            welcome2, _, people2 = await sign_in(lena, "lena")
            lena_id = welcome2["user"]["id"]

            check("people-lists-everyone", len(people2["items"]) == 2, people2["items"])
            check("presence-both-online",
                  set(people2["online"]) == {gosha_id, lena_id}, people2["online"])

            # Гоша узнал о Лене: при появлении нового человека уходит весь
            # список участников, иначе только что зарегистрировавшийся никому
            # не виден по имени
            frames = await collect(gosha)
            check("presence-announced",
                  any(f["type"] == "people" and lena_id in f["online"]
                      and any(i["id"] == lena_id for i in f["items"])
                      for f in frames), [f["type"] for f in frames])

            # --- группа вместо общего чата
            await gosha.send(protocol.group_request("Общая", [lena_id]))
            mine = await read_until(gosha, "conversation")
            hers = await read_until(lena, "conversation")
            room = mine["item"]["id"]
            check("group-created", mine["item"]["title"] == "Общая", mine)
            check("group-shown-to-member", hers["item"]["id"] == room, hers)

            await gosha.send(protocol.text_message("Гоша", "всем привет", room))
            frame = await read_until(lena, "text")
            check("room-message-delivered", frame["type"] == "text"
                  and frame["conversation"] == room and frame["text"] == "всем привет"
                  and frame.get("id"), frame)
            room_message_id = frame["id"]

            # --- личная переписка
            await gosha.send(protocol.direct_request(lena_id))
            # В очереди ещё лежит история только что созданной группы
            conversations = await read_until(gosha, "conversations")
            direct = [item for item in conversations["items"] if item["kind"] == "direct"]
            check("direct-created", len(direct) == 1 and direct[0]["title"] == "lena",
                  conversations["items"])
            direct_id = direct[0]["id"]
            await read_until(gosha, "history")  # история личной переписки

            # у Лены список тоже обновился
            frames = await collect(lena)
            check("direct-shown-to-other",
                  any(f["type"] == "conversations"
                      and any(i["kind"] == "direct" for i in f["items"])
                      for f in frames), [f["type"] for f in frames])

            # повторный запрос не плодит переписки
            await gosha.send(protocol.direct_request(lena_id))
            again = await read(gosha)
            await read(gosha)
            check("direct-not-duplicated",
                  len([i for i in again["items"] if i["kind"] == "direct"]) == 1,
                  again["items"])

            # --- личное сообщение уходит только собеседнику
            await gosha.send(protocol.text_message("Гоша", "секрет", direct_id))
            frame = await read_until(lena, "text")
            check("direct-message-delivered", frame["conversation"] == direct_id
                  and frame["text"] == "секрет", frame)

        # --- уход отмечается отдельным кадром
        frames = await collect(gosha, 1.2)
        check("presence-offline",
              any(f["type"] == "presence" and f["user"] == lena_id
                  and not f["online"] for f in frames),
              [f["type"] for f in frames])

        # --- третий участник личное не видит
        async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as dima:
            await sign_in(dima, "dima")
            await dima.send(protocol.open_request(direct_id))
            frame = await read_until(dima, "error")
            check("direct-closed-for-others", frame["type"] == "error"
                  and "недоступна" in frame["text"], frame)

            await dima.send(protocol.text_message("dima", "подслушаю", direct_id))
            frame = await read_until(dima, "error")
            check("direct-write-blocked", frame["type"] == "error", frame)

            # и в списке переписок чужой личной нет
            await dima.send(protocol.open_request(room))
            frame = await read_until(dima, "error")
            check("direct-not-listed-for-others",
                  frame.get("code") == "no_access", frame)

        # --- ответ цитатой
        await gosha.send(protocol.text_message("Гоша", "это ответ", room, room_message_id))
        await asyncio.sleep(0.4)

        # --- удаление своего сообщения
        await gosha.send(protocol.delete_request(room_message_id))
        frame = await read_until(gosha, "deleted")
        check("delete-confirmed", frame["type"] == "deleted"
              and frame["id"] == room_message_id, frame)

        # --- поиск
        await gosha.send(protocol.text_message("Гоша", "малина на подоконнике", room))
        await asyncio.sleep(0.4)
        await gosha.send(protocol.search_request("малина"))
        frame = await read_until(gosha, "search")
        check("search-finds", frame["type"] == "search" and len(frame["items"]) == 1
              and "малина" in frame["items"][0]["text"], frame)

        await gosha.send(protocol.search_request("такого-точно-нет"))
        frame = await read_until(gosha, "search")
        check("search-empty", frame["items"] == [], frame)

        await gosha.send(protocol.search_request("м"))
        frame = await read_until(gosha, "search")
        check("search-ignores-short", frame["items"] == [], frame)

    # --- история: страницы, цитаты, удалённое
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as reader:
        await sign_in(reader, "reader")
        await reader.send(protocol.group_request("Длинная", [gosha_id]))
        long_room = (await read_until(reader, "conversation"))["item"]["id"]

        # Первые строки отправляем по одной: их номера нужны для цитаты и
        # удаления, а сервер возвращает номер в ack
        early = []
        for index in range(8):
            await reader.send(protocol.text_message("reader", f"строка {index}",
                                                    long_room, None, f"l{index}"))
            early.append((await read_until(reader, "ack"))["id"])

        await reader.send(protocol.text_message("reader", "это ответ", long_room,
                                                early[0]))
        await reader.send(protocol.delete_request(early[1]))
        await read_until(reader, "deleted")

        for index in range(8, 60):
            await reader.send(protocol.text_message("reader", f"строка {index}",
                                                    long_room))
        await asyncio.sleep(2.5)

        await reader.send(protocol.open_request(long_room))
        page = await read_until(reader, "history")
        check("history-page-size", len(page["items"]) == storage.HISTORY_LIMIT,
              len(page["items"]))
        check("history-has-more", page["more"] is True, page["more"])
        check("history-newest-last", page["items"][-1]["text"] == "строка 59",
              page["items"][-1])

        oldest = page["items"][0]["id"]
        await reader.send(protocol.open_request(long_room, before=oldest))
        older = await read_until(reader, "history")
        check("history-older-page", older["items"]
              and older["items"][-1]["id"] < oldest, older["items"][-1:])
        check("history-marks-before", older["before"] == oldest, older["before"])

        texts = [item.get("text") for item in older["items"]]
        check("history-shows-reply",
              any(item.get("reply_to") for item in older["items"]), texts[-6:])
        check("history-quotes-attached", bool(older.get("quotes")), older.get("quotes"))
        check("history-hides-deleted",
              any(item["kind"] == "deleted" and "text" not in item
                  for item in older["items"]),
              [i["kind"] for i in older["items"]][:6])

server_ok = True
try:
    asyncio.run(scenario())
except Exception as error:
    server_ok = False
    print("СЦЕНАРИЙ УПАЛ:", error.__class__.__name__, error)
finally:
    server.terminate()
    server.wait(timeout=5)

check("scenario-completed", server_ok)
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
