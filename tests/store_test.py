"""Хранилище аккаунтов на стороне клиента."""
import json, os, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import store

results = []
def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")

folder = Path(tempfile.mkdtemp(prefix="velix-store-"))
path = folder / "velix.json"

# --- пустое состояние
data = store.load(path)
check("store-empty-defaults", data["accounts"] == [] and data["last"] is None, data)

# --- запоминаем аккаунт
store.remember_account(data, "gosha", "Гоша", "velix.example.org", "токен-1")
store.save(data, path)
again = store.load(path)
check("store-saves-account", again["accounts"][0]["login"] == "gosha", again)
check("store-saves-token", again["accounts"][0]["token"] == "токен-1", again)
check("store-marks-last", again["last"] == "gosha@velix.example.org", again["last"])
check("store-no-passwords", "password" not in json.dumps(again), "в файле есть пароль")

# --- второй аккаунт становится первым в списке
store.remember_account(again, "lena", "Лена", "velix.example.org", "токен-2")
check("store-latest-first", again["accounts"][0]["login"] == "lena",
      [a["login"] for a in again["accounts"]])
check("store-keeps-both", len(again["accounts"]) == 2, again["accounts"])

# --- повторный вход тем же аккаунтом не плодит записи
store.remember_account(again, "gosha", "Гоша", "velix.example.org", "токен-3")
check("store-no-duplicates", len(again["accounts"]) == 2,
      [a["login"] for a in again["accounts"]])
check("store-updates-token", again["accounts"][0]["token"] == "токен-3", again["accounts"][0])

# --- один логин на разных серверах — это разные записи
store.remember_account(again, "gosha", "Гоша", "192.168.0.225", "токен-4")
check("store-server-matters", len(again["accounts"]) == 3,
      [store.key_of(a) for a in again["accounts"]])

# --- переименование подтягивается
store.update_name(again, "lena", "velix.example.org", "Елена")
lena = [a for a in again["accounts"] if a["login"] == "lena"][0]
check("store-renames", lena["name"] == "Елена", lena)

# --- выход убирает запись
store.forget_account(again, {"login": "lena", "server": "velix.example.org"})
check("store-forgets", all(a["login"] != "lena" for a in again["accounts"]),
      [a["login"] for a in again["accounts"]])

# --- битый файл не роняет клиент
path.write_text("{это не json", encoding="utf-8")
check("store-survives-garbage", store.load(path)["accounts"] == [])

# --- недоступный путь тоже переживаем
store.save({"accounts": []}, Path("Z:/нет-такого-диска/velix.json"))
check("store-survives-bad-path", True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
