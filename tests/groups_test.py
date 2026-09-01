"""Группы вместо общего чата и галочки о доставке."""

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("groupsandbox")
URI = "ws://localhost:8779"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8779")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import protocol  # noqa: E402

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

# --- база от прошлой версии: общий чат отдельной переписки и сообщение в нём
old = sqlite3.connect(SANDBOX / "velix.db")
old.executescript("""
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, nickname TEXT NOT NULL,
                       text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, name TEXT NOT NULL,
                    bio TEXT NOT NULL DEFAULT '', avatar_id TEXT,
                    created_at TEXT NOT NULL, last_seen TEXT);
CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                            title TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE members (conversation_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                      PRIMARY KEY (conversation_id, user_id));
INSERT INTO conversations (id, kind, title, created_at)
     VALUES (1, 'room', 'Общий чат', '2026-01-01T00:00:00+00:00');
INSERT INTO users (id, login, password_hash, name, created_at)
     VALUES (1, 'старожил', 'scrypt$x$y$z', 'Старожил', '2026-01-01T00:00:00+00:00');
INSERT INTO messages (id, nickname, text, created_at)
     VALUES (1, 'Старожил', 'сообщение из прошлой версии', '2026-01-01T00:00:00+00:00');
""")
old.commit()
old.close()

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)


async def read_until(ws, kind, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(), timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def connect():
    return await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)


async def sign_in(ws, login):
    await ws.send(protocol.register_message(login, "пароль123", login.title()))
    welcome = await read_until(ws, "welcome")
    return welcome["user"]


async def scenario():
    # --- миграция: общий чат стал обычной группой
    connection = sqlite3.connect(SANDBOX / "velix.db")
    kind, title = connection.execute(
        "SELECT kind, title FROM conversations WHERE id = 1").fetchone()
    check("migration-room-becomes-group", kind == "group", kind)
    check("migration-group-renamed", title == "Velix", title)
    check("migration-old-user-inside",
          connection.execute("SELECT COUNT(*) FROM members WHERE conversation_id = 1"
                             " AND user_id = 1").fetchone()[0] == 1)
    check("migration-message-kept",
          connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = 1"
                             ).fetchone()[0] == 1)
    connection.close()

    # --- новичок не попадает никуда сам
    async with await connect() as gosha:
        user = await sign_in(gosha, "gosha")
        gosha_id = user["id"]
        listing = await read_until(gosha, "conversations")
        check("new-user-has-no-chats", listing["items"] == [], listing["items"])

        async with await connect() as lena:
            lena_user = await sign_in(lena, "lena")
            lena_id = lena_user["id"]
            await read_until(lena, "conversations")
            await read_until(lena, "people")
            await read_until(gosha, "people")

            # --- Гоша заводит группу и зовёт Лену
            await gosha.send(protocol.group_request("Поход", [lena_id]))
            mine = await read_until(gosha, "conversation")
            hers = await read_until(lena, "conversation")
            group = mine["item"]["id"]
            check("group-created", mine["item"]["title"] == "Поход"
                  and mine["item"]["kind"] == "group", mine)
            check("group-shown-to-member", hers["item"]["id"] == group, hers)

            # --- пустое название и пустой состав не проходят
            await gosha.send(protocol.group_request("   ", [lena_id]))
            answer = await read_until(gosha, "error")
            check("group-needs-title", answer.get("code") == "group_needs_title", answer)
            await gosha.send(protocol.group_request("Один", []))
            answer = await read_until(gosha, "error")
            check("group-needs-members",
                  answer.get("code") == "group_needs_members", answer)

            # --- сообщение в группу: ack отправителю, кадр участнице
            await gosha.send(protocol.text_message("Гоша", "выходим в семь",
                                                   group, None, "l1"))
            ack = await read_until(gosha, "ack")
            check("ack-returns-local", ack.get("local") == "l1" and ack.get("id"), ack)
            message_id = ack["id"]

            got = await read_until(lena, "text")
            check("group-message-delivered", got.get("text") == "выходим в семь"
                  and got.get("conversation") == group, got)

            # --- Лена в сети, значит доставлено
            receipts = await read_until(gosha, "receipts")
            check("receipt-delivered",
                  receipts["items"].get(str(message_id)) == "delivered", receipts)

            # --- прочитано, когда она об этом сказала
            await lena.send(protocol.read_request(group, [message_id]))
            receipts = await read_until(gosha, "receipts")
            check("receipt-read",
                  receipts["items"].get(str(message_id)) == "read", receipts)

            # --- закрепление
            await gosha.send(protocol.pin_request(group, message_id))
            mine = await read_until(gosha, "pinned")
            hers = await read_until(lena, "pinned")
            check("pin-confirmed", mine["item"]["id"] == message_id
                  and mine["conversation"] == group, mine)
            check("pin-seen-by-member", hers["item"]["id"] == message_id, hers)

            await gosha.send(protocol.open_request(group))
            page = await read_until(gosha, "history")
            check("pin-history-fine", page["conversation"] == group, page)

            await gosha.send(protocol.pin_request(group, None))
            gone = await read_until(gosha, "pinned")
            check("unpin-clears", gone["item"] is None, gone)

            # --- пересылка в другую группу
            await gosha.send(protocol.group_request("Работа", [lena_id]))
            second = (await read_until(gosha, "conversation"))["item"]["id"]
            await read_until(lena, "conversation")

            await gosha.send(protocol.forward_request(message_id, second))
            copy = await read_until(lena, "text")
            check("forward-delivered", copy["text"] == "выходим в семь"
                  and copy["conversation"] == second, copy)
            check("forward-marks-author", copy.get("forwarded") == "Gosha", copy)

            # В очереди лежит пустая история новой группы — ждём ту, где
            # пересланное уже есть
            forwarded = []
            for _ in range(4):
                await gosha.send(protocol.open_request(second))
                page = await read_until(gosha, "history")
                forwarded = [item for item in page["items"]
                             if item.get("forwarded")]
                if forwarded:
                    break
                await asyncio.sleep(0.4)
            check("forward-mark-in-history", bool(forwarded), page["items"])

        # --- третий человек группы не видит и в неё не пишет
        async with await connect() as dima:
            await sign_in(dima, "dima")
            listing = await read_until(dima, "conversations")
            check("outsider-sees-nothing", listing["items"] == [], listing["items"])

            await dima.send(protocol.open_request(group))
            answer = await read_until(dima, "error")
            check("outsider-cannot-open", answer.get("code") == "no_access", answer)

            await dima.send(protocol.text_message("Дима", "я тоже пойду", group))
            answer = await read_until(dima, "error")
            check("outsider-cannot-write", answer.get("code") == "no_access", answer)

        # --- офлайн-получатель: галочка одна, пока он не зайдёт
        await gosha.send(protocol.text_message("Гоша", "ты тут?", group, None, "l2"))
        ack = await read_until(gosha, "ack")
        second = ack["id"]
        await asyncio.sleep(0.8)
        state = sqlite3.connect(SANDBOX / "velix.db").execute(
            "SELECT COUNT(*) FROM receipts WHERE message_id = ?", (second,)).fetchone()[0]
        check("offline-not-delivered", state == 0, state)

        async with await connect() as lena_again:
            await lena_again.send(protocol.login_message("lena", "пароль123"))
            await read_until(lena_again, "welcome")
            await read_until(lena_again, "history")
            # Отправка офлайн уже прислала галочку «sent» — ждём именно
            # «delivered», которое придёт, когда Лена заберёт историю
            state = None
            for _ in range(6):
                receipts = await read_until(gosha, "receipts")
                state = receipts["items"].get(str(second), state)
                if state == "delivered":
                    break
            check("delivered-when-back", state == "delivered", state)

        return gosha_id, lena_id, group


try:
    asyncio.run(scenario())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
