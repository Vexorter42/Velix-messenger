"""Аккаунты: пароли, регистрация, вход, токены, профиль, аватарка."""

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
SANDBOX = Path(__file__).with_name("authsandbox")
URI = "ws://localhost:8765"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import accounts  # noqa: E402
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ---------------------------------------------------------- чистые функции

stored = accounts.hash_password("правильный-пароль")
check("hash-not-plaintext", "правильный-пароль" not in stored, stored[:40])
check("hash-verifies", accounts.verify_password("правильный-пароль", stored))
check("hash-rejects-wrong", not accounts.verify_password("другой", stored))
check("hash-salted", accounts.hash_password("а") != accounts.hash_password("а"),
      "две соли совпали")
check("hash-survives-garbage", not accounts.verify_password("а", "мусор"))

check("login-too-short", accounts.check_login("ab") is not None)
check("login-bad-chars", accounts.check_login("вася") is not None)
check("login-ok", accounts.check_login("vasya_92") is None)
check("password-too-short", accounts.check_password("12345") is not None)
check("password-ok", accounts.check_password("123456") is None)
check("name-strips-brackets", accounts.clean_name("[Вася]", "x") == "Вася",
      accounts.clean_name("[Вася]", "x"))
check("name-falls-back", accounts.clean_name("   ", "vasya") == "vasya")
check("bio-trimmed", len(accounts.clean_bio("а" * 400)) == accounts.MAX_BIO)
check("token-unique", accounts.new_token() != accounts.new_token())


# ------------------------------------------------------------------ сервер

def fresh_sandbox():
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir()
    for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py"):
        shutil.copy(REPO / name, SANDBOX / name)


def picture(color=(90, 160, 220), size=(300, 300)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


async def connect():
    return await websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE)


async def read(websocket, timeout=10):
    return protocol.decode(await asyncio.wait_for(websocket.recv(), timeout=timeout))


async def read_until(websocket, kind, timeout=15):
    """Ждёт нужный кадр: после входа их прилетает несколько подряд."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(websocket.recv(),
                                                       timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def swallow_welcome(websocket):
    """Съедает список переписок, историю и участников после входа."""
    for _ in range(3):
        await websocket.recv()


async def read_until(websocket, kind, timeout=15):
    """Ждёт нужный кадр: после входа их прилетает несколько подряд."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(websocket.recv(),
                                                       timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def swallow_welcome(websocket):
    """Съедает список переписок и участников после входа.

    Истории в приветствии больше нет: общего чата не стало, а в группы
    человека ещё не позвали.
    """
    await read_until(websocket, "people")


async def make_group(websocket, member_ids, title="Общая"):
    """Заводит группу: писать теперь можно только в неё."""
    await websocket.send(protocol.group_request(title, member_ids))
    return (await read_until(websocket, "conversation"))["item"]["id"]


async def scenario():
    # --- без входа в чат не пускают
    async with await connect() as ws:
        await ws.send(protocol.text_message("Самозванец", "я просто зайду"))
        answer = await read(ws)
        check("auth-required", answer["type"] == "authfail", answer)

    # --- регистрация
    async with await connect() as ws:
        await ws.send(protocol.register_message("gosha", "секрет123", "Гоша"))
        answer = await read(ws)
        check("register-welcome", answer["type"] == "welcome"
              and answer["user"]["name"] == "Гоша"
              and answer["user"]["login"] == "gosha"
              and answer.get("token"), answer)
        token = answer["token"]

        listing = await read_until(ws, "conversations")
        check("register-has-no-chats", listing["items"] == [], listing["items"])
        await read_until(ws, "people")

    # --- логин занят
    async with await connect() as ws:
        await ws.send(protocol.register_message("GOSHA", "другой123", "Двойник"))
        answer = await read(ws)
        check("register-login-taken", answer["type"] == "authfail"
              and "занят" in answer["text"], answer)

    # --- слабый пароль и кривой логин
    async with await connect() as ws:
        await ws.send(protocol.register_message("ok_login", "123", "Кто-то"))
        answer = await read(ws)
        check("register-weak-password", answer["type"] == "authfail"
              and "Пароль" in answer["text"], answer)

        await ws.send(protocol.register_message("я", "нормальныйпароль", "Кто-то"))
        answer = await read(ws)
        check("register-bad-login", answer["type"] == "authfail"
              and "Логин" in answer["text"], answer)

    # --- вход паролем
    async with await connect() as ws:
        await ws.send(protocol.login_message("gosha", "неверный"))
        answer = await read(ws)
        check("login-wrong-password", answer["type"] == "authfail", answer)

        await ws.send(protocol.login_message("нет-такого", "пароль123"))
        answer = await read(ws)
        check("login-unknown-user", answer["type"] == "authfail"
              and "Неверный логин или пароль" in answer["text"], answer)

        await ws.send(protocol.login_message("GoShA", "секрет123"))
        answer = await read(ws)
        check("login-case-insensitive", answer["type"] == "welcome", answer)
        token = answer["token"]
        await swallow_welcome(ws)

    # --- вход по токену, без пароля
    async with await connect() as ws:
        await ws.send(protocol.auth_message(token))
        answer = await read(ws)
        check("token-login", answer["type"] == "welcome"
              and answer["user"]["login"] == "gosha", answer)
        await swallow_welcome(ws)

    async with await connect() as ws:
        await ws.send(protocol.auth_message("вымышленный-токен"))
        answer = await read(ws)
        check("token-bad", answer["type"] == "authfail", answer)

    # --- имя в сообщении берётся из профиля, а не из того, что прислал клиент
    async with await connect() as first, await connect() as second:
        await first.send(protocol.auth_message(token))
        await read(first)
        await swallow_welcome(first)

        await second.send(protocol.register_message("lena", "пароль123", "Лена"))
        welcome = await read(second)
        lena_token = welcome["token"]
        await swallow_welcome(second)

        group = await make_group(first, [welcome["user"]["id"]])
        await read_until(second, "conversation")

        await first.send(protocol.text_message("АДМИНИСТРАТОР", "я тут главный",
                                               group))
        got = await read_until(second, "text")
        check("nick-from-profile", got["nick"] == "Гоша", got)

    # --- профиль: имя и о себе
    async with await connect() as ws:
        await ws.send(protocol.auth_message(token))
        await read(ws)
        await swallow_welcome(ws)

        await ws.send(protocol.profile_message("Гоша Петров", "люблю малину\nи чай"))
        answer = await read(ws)
        check("profile-updated", answer["type"] == "profile"
              and answer["user"]["name"] == "Гоша Петров"
              and "малину" in answer["user"]["bio"], answer)

        # --- аватарка
        avatar = picture()
        await ws.send(protocol.avatar_header("моя-морда.png", len(avatar)))
        await ws.send(avatar)
        answer = await read_until(ws, "profile")
        check("avatar-saved", answer["type"] == "profile"
              and answer["user"].get("avatar"), answer)
        avatar_id = answer["user"]["avatar"]

        # аватарка забирается тем же запросом, что и вложения
        await ws.send(protocol.fetch_request(avatar_id))
        header = await read_until(ws, "blob")
        data = await asyncio.wait_for(ws.recv(), timeout=10)
        check("avatar-fetchable", header["type"] == "blob" and len(data) > 0, header)

        # новое имя видно в истории у старых сообщений
        await ws.send(protocol.text_message("", "проверка имени", group))
        await asyncio.sleep(0.5)

    async with await connect() as ws:
        await ws.send(protocol.auth_message(lena_token))
        await read(ws)
        history = await read_until(ws, "history")  # приветствие открывает группу
        names = {item["nick"] for item in history["items"]}
        check("history-uses-current-name", names == {"Гоша Петров"}, names)
        check("history-carries-avatar",
              all(item.get("avatar") == avatar_id for item in history["items"]
                  if item["nick"] == "Гоша Петров"), history["items"][:2])
        check("avatar-not-in-history",
              all(item["kind"] != "avatar" for item in history["items"]),
              [i["kind"] for i in history["items"]])

    # --- выход из аккаунта убивает токен
    async with await connect() as ws:
        await ws.send(protocol.auth_message(token))
        await read(ws)
        await swallow_welcome(ws)
        await ws.send(protocol.logout_message())
        await asyncio.sleep(0.6)

    async with await connect() as ws:
        await ws.send(protocol.auth_message(token))
        answer = await read(ws)
        check("logout-kills-token", answer["type"] == "authfail", answer)


fresh_sandbox()
server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)
try:
    asyncio.run(scenario())

    import sqlite3
    connection = sqlite3.connect(SANDBOX / "velix.db")
    stored_hash = connection.execute("SELECT password_hash FROM users WHERE login='gosha'").fetchone()[0]
    check("db-stores-hash-only", "секрет123" not in stored_hash and stored_hash.startswith("scrypt$"),
          stored_hash[:30])
    users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    check("db-two-users", users == 2, users)
    connection.close()
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
