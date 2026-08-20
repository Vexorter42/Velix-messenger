"""Коды приглашений: выдать новый или посмотреть выданные.

Запускается на сервере, рядом с базой:

    python invite.py            — выдать код
    python invite.py Диме       — выдать код с пометкой, кому он предназначен
    python invite.py --list     — показать все коды и кто ими воспользовался
"""

import asyncio
import sys

import accounts
import storage


async def issue(note):
    await storage.init()
    try:
        code = accounts.new_invite()
        await storage.add_invite(code, note)
        print(f"Код приглашения: {code}")
        if note:
            print(f"Помечен как: {note}")
        print("Он одноразовый: после регистрации перестанет работать.")
    finally:
        await storage.close()


async def show():
    await storage.init()
    try:
        rows = await storage.list_invites()
        if not rows:
            print("Кодов пока нет.")
            return
        for code, created_at, note, used_by, used_at in rows:
            state = "использован" if used_by else "свободен"
            when = storage.format_time(created_at)
            tail = f", помечен: {note}" if note else ""
            print(f"{code}  {state:<12} выдан {when}{tail}")
    finally:
        await storage.close()


def main():
    arguments = sys.argv[1:]
    if arguments and arguments[0] in ("--list", "-l"):
        asyncio.run(show())
    else:
        asyncio.run(issue(" ".join(arguments)))


if __name__ == "__main__":
    main()
