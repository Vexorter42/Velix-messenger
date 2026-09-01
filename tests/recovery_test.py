"""Восстановление пароля: код при регистрации, смена, защита от подбора."""

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
SANDBOX = Path(__file__).with_name("recoverysandbox")
URI = "ws://localhost:8787"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_PORT="8787")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV["VELIX_OPEN_REGISTRATION"] = "1"

sys.path.insert(0, str(REPO))
import accounts  # noqa: E402
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# --------------------------------------------------------- сам код

code = accounts.new_recovery()
check("recovery-format", len(code) == 19 and code.count("-") == 3, code)
check("recovery-unique", accounts.new_recovery() != accounts.new_recovery())
check("recovery-no-lookalikes", not set("O0I1") & set(code.replace("-", "")), code)

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py", "recover.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.2)


async def read_until(ws, kind, timeout=20):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_running_loop().time()
        frame = protocol.decode(await asyncio.wait_for(ws.recv(),
                                                       timeout=max(left, 0.1)))
        if frame.get("type") == kind:
            return frame


async def talk(frame, kind, timeout=20):
    """Одно подключение: отправили кадр — дождались нужного ответа."""
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
        await ws.send(frame)
        return await read_until(ws, kind, timeout)


async def scenario():
    # --- код выдаётся при регистрации, ровно один раз
    welcome = await talk(protocol.register_message("gosha", "пароль123", "Гоша"),
                         "welcome")
    first_code = welcome.get("recovery")
    check("register-gives-recovery", bool(first_code) and len(first_code) == 19,
          welcome)

    later = await talk(protocol.login_message("gosha", "пароль123"), "welcome")
    check("login-keeps-code-secret", "recovery" not in later, later)

    connection = sqlite3.connect(SANDBOX / "velix.db")
    stored = connection.execute(
        "SELECT recovery_hash FROM users WHERE login='gosha'").fetchone()[0]
    check("recovery-stored-as-hash",
          first_code not in stored and stored.startswith("scrypt$"), stored[:24])
    connection.close()

    # --- чужой код не подходит
    answer = await talk(protocol.recover_request("gosha", "AAAA-BBBB-CCCC-DDDD",
                                                 "новыйпароль1"), "authfail")
    check("recovery-rejects-wrong", answer.get("code") == "recovery_bad", answer)

    # --- неизвестный логин отвечает так же, ничего не подсказывая
    answer = await talk(protocol.recover_request("нет-такого", first_code,
                                                 "новыйпароль1"), "authfail")
    check("recovery-hides-unknown-login",
          answer.get("code") == "recovery_bad", answer)

    # --- слабый пароль не примут
    answer = await talk(protocol.recover_request("gosha", first_code, "123"),
                        "authfail")
    check("recovery-checks-password",
          answer.get("code") == "short_password", answer)

    # --- настоящий код меняет пароль и сразу впускает
    welcome = await talk(protocol.recover_request("gosha", first_code.lower(),
                                                  "новыйпароль1"), "welcome")
    second_code = welcome.get("recovery")
    check("recovery-lets-in", welcome["user"]["login"] == "gosha", welcome)
    check("recovery-gives-fresh-code",
          bool(second_code) and second_code != first_code, second_code)

    # --- старый пароль больше не работает, новый работает
    answer = await talk(protocol.login_message("gosha", "пароль123"), "authfail")
    check("recovery-old-password-dead",
          answer.get("code") == "bad_credentials", answer)
    answer = await talk(protocol.login_message("gosha", "новыйпароль1"), "welcome")
    check("recovery-new-password-works", answer["type"] == "welcome", answer)

    # --- прежний код одноразовый
    answer = await talk(protocol.recover_request("gosha", first_code, "ещёпароль1"),
                        "authfail")
    check("recovery-code-single-use", answer.get("code") == "recovery_bad", answer)

    # --- смена пароля гасит выданные раньше токены
    stale = later.get("token")
    answer = await talk(protocol.auth_message(stale), "authfail")
    check("recovery-drops-sessions", answer.get("code") == "session_expired", answer)

    # --- перебор кода запирает логин, как и перебор пароля
    for _ in range(5):
        await talk(protocol.recover_request("gosha", "ZZZZ-ZZZZ-ZZZZ-ZZZZ",
                                            "ещёпароль1"), "authfail")
    answer = await talk(protocol.recover_request("gosha", second_code, "ещёпароль1"),
                        "authfail")
    check("recovery-bruteforce-locks", answer.get("code") == "locked_out", answer)

    return second_code


try:
    asyncio.run(scenario())

    # --- владелец сервера выписывает новый код утилитой
    minted = subprocess.run([sys.executable, "recover.py", "gosha"], cwd=SANDBOX,
                            env=ENV, capture_output=True, text=True,
                            encoding="utf-8", timeout=90)
    printed = [line for line in minted.stdout.splitlines()
               if "Код восстановления" in line]
    check("recover-tool-issues", bool(printed), minted.stdout + minted.stderr)
    fresh = printed[0].split(":")[1].strip() if printed else ""

    listing = subprocess.run([sys.executable, "recover.py", "--list"], cwd=SANDBOX,
                             env=ENV, capture_output=True, text=True,
                             encoding="utf-8", timeout=90)
    check("recover-tool-lists", "gosha" in listing.stdout
          and "код есть" in listing.stdout, listing.stdout)

    missing = subprocess.run([sys.executable, "recover.py", "нет-такого"],
                             cwd=SANDBOX, env=ENV, capture_output=True, text=True,
                             encoding="utf-8", timeout=90)
    check("recover-tool-unknown-login", "Нет такого логина" in missing.stdout,
          missing.stdout)

    # выписанный код работает: запертый логин к этому времени освободится нескоро,
    # поэтому проверяем на другом человеке
    async def outsider():
        welcome = await talk(protocol.register_message("lena", "пароль123", "Лена"),
                             "welcome")
        code = welcome["recovery"]
        answer = await talk(protocol.recover_request("lena", code, "другойпароль1"),
                            "welcome")
        check("recovery-works-for-second-person",
              answer["user"]["login"] == "lena", answer)

    asyncio.run(outsider())
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
