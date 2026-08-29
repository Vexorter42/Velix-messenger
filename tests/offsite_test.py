"""Копия уезжает с малины на домашний сервер — и приезжает целой.

Копии лежали на той же карте, что и переписка: карта умирает — уходит и то и
другое разом. Теперь домашний сервер раз в сутки тянет свежую к себе. Тянет,
а не малина толкает: у малины ключей от сервера копий нет.

Проверка гоняет ровно те же два скрипта, только вместо ssh между ними стоит
заглушка: она зовёт serve-backup.sh на этой же машине.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


ОБОЛОЧКА = shutil.which("sh") or shutil.which("bash")
if ОБОЛОЧКА is None or shutil.which("tar") is None:
    print("TEST offsite-needs-shell: SKIP — нет sh или tar, проверять нечем")
    print("\nИТОГО: 0/0 проверок прошли")
    sys.exit(0)

песочница = Path(tempfile.mkdtemp(prefix="velix-offsite-"))
МАЛИНА = песочница / "малина"
КОПИИ = МАЛИНА / "velix-backups"
ДОМА = песочница / "дома" / "backups" / "velix"
(МАЛИНА / "velix").mkdir(parents=True)
КОПИИ.mkdir(parents=True)


def завести_копию(имя, сообщений, вложения=()):
    куда = КОПИИ / имя
    (куда / "media").mkdir(parents=True)
    база = sqlite3.connect(куда / "velix.db")
    try:
        база.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
        база.executemany("INSERT INTO messages (text) VALUES (?)",
                         [(f"слово {номер}",) for номер in range(сообщений)])
        база.commit()
    finally:
        база.close()
    for имя_файла, содержимое in вложения:
        (куда / "media" / имя_файла).write_bytes(содержимое)
    return куда


завести_копию("2026-08-01_04-30", 3, [("старое.png", b"a" * 5000)])
завести_копию("2026-08-02_04-30", 7, [("старое.png", b"a" * 5000),
                                      ("новое.png", b"b" * 5000)])

# ------------------------------------------------- заглушка вместо ssh
#
# Настоящий ssh запирает ключ на serve-backup.sh: что бы ни попросили,
# выполнится только он. Заглушка делает ровно это же, без сети.

ЗАГЛУШКА = песочница / "ssh-заглушка"
ЗАГЛУШКА.write_text(
    "#!/bin/sh\n"
    "# Последний довод — то, что попросил тот, кто пришёл по ключу\n"
    "for ONE; do LAST=$ONE; done\n"
    f'HOME="{МАЛИНА}" BACKUP_DIR="{КОПИИ}" SSH_ORIGINAL_COMMAND="$LAST" '
    f'exec "{ОБОЛОЧКА}" "{REPO / "serve-backup.sh"}"\n',
    encoding="utf-8", newline="\n")
os.chmod(ЗАГЛУШКА, 0o755)


def отдать(что):
    return subprocess.run(
        [ОБОЛОЧКА, str(REPO / "serve-backup.sh")],
        env=dict(os.environ, HOME=str(МАЛИНА), BACKUP_DIR=str(КОПИИ),
                 SSH_ORIGINAL_COMMAND=что),
        capture_output=True, timeout=120)


# ------------------------------------------------------- что умеет малина

список = отдать("list")
имена = (список.stdout or b"").decode("utf-8", "replace").split()
check("serve-lists-backups", имена == ["2026-08-01_04-30", "2026-08-02_04-30"], имена)

наружу = отдать("../../etc")
check("serve-refuses-way-out", наружу.returncode != 0,
      (наружу.stderr or b"").decode("utf-8", "replace"))

свежая = отдать("latest")
check("serve-gives-a-tar", свежая.returncode == 0 and свежая.stdout[:100],
      len(свежая.stdout or b""))

# ---------------------------------------------------------- забираем к себе

def забрать():
    return subprocess.run(
        [ОБОЛОЧКА, str(REPO / "pull-backup.sh")],
        env=dict(os.environ, FROM="кто-нибудь@куда-нибудь",
                 KEY=str(песочница / "ключа-нет"), INTO=str(ДОМА), KEEP="2",
                 SSH=str(ЗАГЛУШКА)),
        capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace")


if shutil.which("sqlite3") is None:
    print("TEST offsite-pull: SKIP — нет sqlite3, копию проверять нечем")
else:
    привезли = забрать()
    check("pull-runs", привезли.returncode == 0,
          (привезли.stdout or "") + (привезли.stderr or ""))
    check("pull-took-the-newest", (ДОМА / "2026-08-02_04-30").is_dir(),
          [one.name for one in ДОМА.glob("*")] if ДОМА.exists() else "пусто")

    if (ДОМА / "2026-08-02_04-30" / "velix.db").exists():
        база = sqlite3.connect(ДОМА / "2026-08-02_04-30" / "velix.db")
        try:
            сколько = база.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            база.close()
        check("pull-brought-whole-base", сколько == 7, сколько)
    else:
        check("pull-brought-whole-base", False, "базы нет")

    check("pull-brought-media",
          (ДОМА / "2026-08-02_04-30" / "media" / "новое.png").exists())

    # Малина должна была отметить у себя, что копия уехала: иначе молчащий
    # сервер копий однажды перестанет их забирать, и никто не заметит
    отметка = (МАЛИНА / "velix" / "backup-pull.log")
    check("pull-reports-back", отметка.exists()
          and "2026-08-02_04-30" in отметка.read_text(encoding="utf-8"),
          отметка.read_text(encoding="utf-8") if отметка.exists() else "нет журнала")

    # ------------------------------------- вложения не лежат дважды
    завести_копию("2026-08-03_04-30", 9, [("старое.png", b"a" * 5000),
                                          ("новое.png", b"b" * 5000)])
    ещё = забрать()
    check("pull-runs-again", ещё.returncode == 0,
          (ещё.stdout or "") + (ещё.stderr or ""))

    первый = ДОМА / "2026-08-02_04-30" / "media" / "новое.png"
    второй = ДОМА / "2026-08-03_04-30" / "media" / "новое.png"
    if первый.exists() and второй.exists():
        check("pull-keeps-one-copy-of-media",
              первый.stat().st_ino == второй.stat().st_ino,
              (первый.stat().st_ino, второй.stat().st_ino))
    else:
        check("pull-keeps-one-copy-of-media", False, "вложений нет")

    # ------------------------------------- старые копии не копятся без края
    завести_копию("2026-08-04_04-30", 11)
    забрать()
    сколько_лежит = sorted(one.name for one in ДОМА.glob("*") if one.is_dir())
    check("pull-keeps-only-the-last-few", len(сколько_лежит) == 2, сколько_лежит)

shutil.rmtree(песочница, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
