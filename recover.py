"""Выдача кода восстановления с сервера.

Код выдаётся человеку при регистрации, но его теряют. Тогда тот, кто держит
чат, выписывает новый этой утилитой и передаёт лично — так же, как передавал
код приглашения.

    python recover.py gosha     # выписать новый код
    python recover.py --list    # у кого код есть, а у кого нет

Сам код нигде не сохраняется: на сервере остаётся только его хеш, поэтому
показать его можно ровно один раз — сейчас.
"""

import asyncio
import sys

import accounts
import storage


async def issue(login):
    await storage.init()
    user_id, _ = await storage.recovery_row(login)
    if user_id is None:
        print(f"Нет такого логина: {login}")
        await storage.close()
        return 1

    code = accounts.new_recovery()
    await storage.set_recovery(
        user_id, await asyncio.to_thread(accounts.hash_password, code))
    await storage.close()

    print(f"Код восстановления для {login}: {code}")
    print("Передайте его лично: по нему меняют пароль, войти в чат он не даёт.")
    return 0


async def listing():
    await storage.init()
    people = await storage.people()
    for person in people:
        _, stored = await storage.recovery_row(person["login"])
        mark = "код есть" if stored else "кода нет"
        print(f"{person['login']:<20} {person['name']:<20} {mark}")
    await storage.close()
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        return asyncio.run(listing())
    if len(sys.argv) > 1:
        return asyncio.run(issue(sys.argv[1].strip()))

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
