"""Приглашения и защита от перебора пароля."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("invitesandbox")
URI = "ws://localhost:8768"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", VELIX_PORT="8768")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
ENV.pop("VELIX_OPEN_REGISTRATION", None)

sys.path.insert(0, str(REPO))
import accounts  # noqa: E402
import protocol  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------------- сами коды

code = accounts.new_invite()
check("invite-format", len(code) == 19 and code.count("-") == 3, code)
check("invite-unique", accounts.new_invite() != accounts.new_invite())
check("invite-no-lookalikes", not set("O0I1") & set(code.replace("-", "")), code)
check("invite-clean-lowercase", accounts.clean_invite(code.lower()) == code)
check("invite-clean-spaces", accounts.clean_invite(f" {code.replace('-', ' ')} ") == code,
      accounts.clean_invite(code.replace("-", " ")))
check("invite-clean-empty", accounts.clean_invite("") == "")

# -------------------------------------------------------------- сервер

if SANDBOX.exists():
    shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py", "mediatools.py",
             "invite.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)


async def attempt(frame, timeout=20):
    async with websockets.connect(URI, max_size=protocol.MAX_FRAME_SIZE) as ws:
        await ws.send(frame)
        return protocol.decode(await asyncio.wait_for(ws.recv(), timeout=timeout))


try:
    # --- без кода не пускают
    answer = asyncio.run(attempt(protocol.register_message("gosha", "пароль123", "Гоша")))
    check("register-needs-invite", answer["type"] == "authfail"
          and "приглашения" in answer["text"], answer)

    # --- выдуманный код не подходит
    answer = asyncio.run(attempt(
        protocol.register_message("gosha", "пароль123", "Гоша", "AAAA-BBBB-CCCC-DDDD")))
    check("register-rejects-fake", answer["type"] == "authfail"
          and "не подошёл" in answer["text"], answer)

    # --- выдаём настоящий код утилитой
    minted = subprocess.run([sys.executable, "invite.py", "для Гоши"], cwd=SANDBOX,
                            env=ENV, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    printed = [line for line in minted.stdout.splitlines() if "Код приглашения:" in line]
    check("invite-tool-prints-code", bool(printed), minted.stdout + minted.stderr)
    real_code = printed[0].split(":")[1].strip() if printed else ""

    listing = subprocess.run([sys.executable, "invite.py", "--list"], cwd=SANDBOX,
                             env=ENV, capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    check("invite-tool-lists", real_code in listing.stdout and "свободен" in listing.stdout,
          listing.stdout)

    # --- по настоящему коду регистрация проходит
    answer = asyncio.run(attempt(
        protocol.register_message("gosha", "пароль123", "Гоша", real_code)))
    check("register-with-invite", answer["type"] == "welcome", answer)

    # --- второй раз тот же код не сработает
    answer = asyncio.run(attempt(
        protocol.register_message("lena", "пароль123", "Лена", real_code)))
    check("invite-single-use", answer["type"] == "authfail", answer)

    listing = subprocess.run([sys.executable, "invite.py", "--list"], cwd=SANDBOX,
                             env=ENV, capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    check("invite-marked-used", "использован" in listing.stdout, listing.stdout)

    # --- код принимается в любом виде: строчными и без дефисов
    second = subprocess.run([sys.executable, "invite.py"], cwd=SANDBOX, env=ENV,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    loose = second.stdout.split("Код приглашения:")[1].split("\n")[0].strip()
    answer = asyncio.run(attempt(
        protocol.register_message("lena", "пароль123", "Лена",
                                  loose.lower().replace("-", " "))))
    check("invite-accepts-loose-format", answer["type"] == "welcome", answer)

    # --- перебор пароля
    async def brute_force():
        outcomes = []
        for _ in range(6):
            outcomes.append(await attempt(protocol.login_message("gosha", "не тот")))
        return outcomes

    outcomes = asyncio.run(brute_force())
    check("bruteforce-first-attempts",
          all("Неверный логин" in item["text"] for item in outcomes[:5]),
          [item["text"] for item in outcomes[:5]])
    check("bruteforce-locks-out", "Слишком много" in outcomes[5]["text"], outcomes[5])

    # --- правильный пароль тоже не пускают, пока дверь заперта
    answer = asyncio.run(attempt(protocol.login_message("gosha", "пароль123")))
    check("bruteforce-blocks-even-correct", "Слишком много" in answer["text"], answer)

    # --- другой аккаунт при этом работает
    answer = asyncio.run(attempt(protocol.login_message("lena", "пароль123")))
    check("bruteforce-other-user-fine", answer["type"] == "welcome", answer)
finally:
    server.terminate()
    server.wait(timeout=5)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
