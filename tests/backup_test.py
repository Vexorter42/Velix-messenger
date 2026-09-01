"""Копия снимается и — главное — восстанавливается.

Копия, из которой ни разу не восстанавливались, — это не копия, а надежда.
Проверка проходит весь путь: заводит переписку с вложением, снимает копию
настоящим backup.sh, портит «боевой» каталог и поднимает всё обратно
настоящим restore.sh.
"""

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import storage  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


ОБОЛОЧКА = shutil.which("sh") or shutil.which("bash")
if ОБОЛОЧКА is None:
    print("TEST backup-needs-shell: SKIP — нет sh, проверять нечем")
    print("\nИТОГО: 0/0 проверок прошли")
    sys.exit(0)

песочница = Path(tempfile.mkdtemp(prefix="velix-backup-"))
БОЕВОЕ = песочница / "velix"
КОПИИ = песочница / "velix-backups"
БОЕВОЕ.mkdir(parents=True)


def запустить(скрипт, *доводы):
    return subprocess.run(
        [ОБОЛОЧКА, str(REPO / скрипт), *доводы],
        env=dict(os.environ, VELIX_DIR=str(БОЕВОЕ), BACKUP_DIR=str(КОПИИ),
                 PYTHON=sys.executable, PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)


# ------------------------------------------------------- заводим переписку

async def набить():
    await storage.init(БОЕВОЕ / "velix.db", БОЕВОЕ / "media")
    гоша = await storage.create_user("gosha", "хеш", "Гоша")
    лена = await storage.create_user("lena", "хеш", "Лена")
    беседа = await storage.direct_id(гоша["id"], лена["id"])
    await storage.save_message(гоша["id"], "Гоша", "привет", беседа)
    номер, media_id, когда, _ = await storage.save_media(
        лена["id"], "Лена", "image", "кот.png", b"\x89PNG" + bytes(2048), беседа)
    await storage.close()
    return media_id


media_id = asyncio.run(набить())
check("backup-sandbox-ready", (БОЕВОЕ / "velix.db").exists()
      and list((БОЕВОЕ / "media").glob(media_id + "*")),
      list((БОЕВОЕ / "media").glob("*")))

# ------------------------------------------------------------ снимаем копию

снято = запустить("backup.sh")
check("backup-runs", снято.returncode == 0,
      (снято.stdout or "") + (снято.stderr or ""))

копии = sorted(one for one in КОПИИ.glob("*") if one.is_dir())
check("backup-made-folder", len(копии) == 1, [one.name for one in копии])
if not копии:
    print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
    sys.exit(1)

копия = копии[0]
check("backup-has-db", (копия / "velix.db").exists())
check("backup-has-media", list((копия / "media").glob(media_id + "*")),
      list((копия / "media").glob("*")))

# --------------------------------- боевая база после копии стоит сама по себе
#
# В режиме WAL переписка копится в velix.db-wal, а velix.db остаётся
# заготовкой: скопируешь руками один файл — увезёшь пустоту. Копия читает и
# журнал, но backup.sh заодно сливает его, чтобы база была правдой сама по себе.

одна = песочница / "одна-база"
одна.mkdir()
shutil.copy(БОЕВОЕ / "velix.db", одна / "velix.db")
проверяем = sqlite3.connect(одна / "velix.db")
try:
    сама = проверяем.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
except sqlite3.OperationalError as беда:
    сама = str(беда)
finally:
    проверяем.close()
check("backup-checkpoints-live-base", сама == 2, сама)

# --------------------------------------------------- копию можно проверить

осмотр = запустить("restore.sh", "--check", копия.name)
check("restore-check-passes", осмотр.returncode == 0,
      (осмотр.stdout or "") + (осмотр.stderr or ""))
check("restore-check-counts", "сообщений: 2" in (осмотр.stdout or ""),
      осмотр.stdout)
check("restore-check-media-present", "все на месте" in (осмотр.stdout or ""),
      осмотр.stdout)

# ----------------------------------------- подложная копия должна не пройти
кривая = КОПИИ / "порченая"
shutil.copytree(копия, кривая)
for файл in (кривая / "media").glob("*"):
    файл.unlink()
осмотр = запустить("restore.sh", "--check", "порченая")
check("restore-check-notices-loss", осмотр.returncode != 0
      and "ПРОПАЛО" in (осмотр.stdout or ""), осмотр.stdout)

# ------------------------------------------------------- восстанавливаем

куда = песочница / "поднятое"
поднято = запустить("restore.sh", "--into", str(куда), копия.name)
check("restore-runs", поднято.returncode == 0,
      (поднято.stdout or "") + (поднято.stderr or ""))
check("restore-put-db", (куда / "velix.db").exists())
check("restore-put-media", list((куда / "media").glob(media_id + "*")),
      list((куда / "media").glob("*")) if (куда / "media").exists() else "нет папки")

if (куда / "velix.db").exists():
    # Закрываем явно: «with» у sqlite3 закрывает сделку, а не соединение, и
    # Windows потом не даст переложить занятый файл
    поднятая = sqlite3.connect(куда / "velix.db")
    try:
        сообщений = поднятая.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        текст = поднятая.execute(
            "SELECT text FROM messages WHERE kind='text'").fetchone()
        целость = поднятая.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        поднятая.close()
    check("restore-messages-alive", сообщений == 2, сообщений)
    check("restore-text-intact", текст and текст[0] == "привет", текст)
    check("restore-integrity-ok", целость == "ok", целость)

# ------------------------------------------- прежнее не затирается молча
ещё_раз = запустить("restore.sh", "--into", str(куда), копия.name)
check("restore-keeps-previous", ещё_раз.returncode == 0
      and any(one.name.startswith("velix.db.before-") for one in куда.glob("*")),
      [one.name for one in куда.glob("*")])

shutil.rmtree(песочница, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
