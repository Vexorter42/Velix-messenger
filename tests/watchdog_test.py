"""Сторож замечает, что сервер молчит, и не бьётся в стену без конца.

Restart=on-failure поднимает упавший процесс, но повисший процесс не падает:
служба остаётся active, а на приветствие никто не отвечает. Сторож стучится
как обычный клиент и ждёт ответа — и вот это здесь и проверяется, вместе с
двумя вещами, о которых легко забыть: что перезапуски не идут по кругу и что
молчащий сервер копий не останется незамеченным.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("watchsandbox")

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


песочница = Path(tempfile.mkdtemp(prefix="velix-watch-"))
ПАМЯТЬ = песочница / "память.json"
ЖУРНАЛ = песочница / "backup-pull.log"

# Сторож читает настройки при ввозе, поэтому раскладываем их заранее
os.environ["VELIX_PORT"] = "8847"
os.environ["VELIX_UNIT"] = "velix-которой-нет"
os.environ["VELIX_WATCHDOG_STATE"] = str(ПАМЯТЬ)
os.environ["VELIX_PULL_LOG"] = str(ЖУРНАЛ)
os.environ["VELIX_WATCHDOG_TIMEOUT"] = "4"
# Писать некуда: проверка не должна никого будить
os.environ["VELIX_TG_CONFIG"] = str(песочница / "настроек-нет.json")
os.environ["VELIX_TG_TARGET"] = str(песочница / "кому-нет.txt")

sys.path.insert(0, str(REPO))
import notify  # noqa: E402
import watchdog  # noqa: E402

# ------------------------------------------------- молчим, когда писать некому

check("notify-silent-without-settings", notify.настройки() == (None, None),
      notify.настройки()[1])
check("notify-says-no", notify.say("этого никто не увидит") is False)

# --------------------------------------------------------- поднимаем сервер

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py", "linkpreview.py"):
    shutil.copy(REPO / name, SANDBOX / name)

СРЕДА = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
             VELIX_PORT="8847", VELIX_OPEN_REGISTRATION="1")
СРЕДА.pop("VELIX_ALLOWED_HOSTS", None)
служба = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=СРЕДА,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.4)

check("watchdog-sees-a-live-server", asyncio.run(watchdog.отзывается()))

служба.terminate()
try:
    служба.wait(timeout=15)
except subprocess.TimeoutExpired:
    служба.kill()
time.sleep(0.6)

check("watchdog-sees-silence", asyncio.run(watchdog.отзывается()) is False)

# ------------------------------------------- по кругу перезапускать не будем

ПАМЯТЬ.write_text(json.dumps({"перезапуски": [time.time() - 60] * 3}),
                  encoding="utf-8")
код = asyncio.run(watchdog.main())
осталось = json.loads(ПАМЯТЬ.read_text(encoding="utf-8"))
check("watchdog-gives-up-after-three", код == 1
      and len(осталось.get("перезапуски", [])) == 3,
      осталось.get("перезапуски"))
check("watchdog-remembers-it-gave-up", "ныл_о_кругах" in осталось, осталось)

# --------------------------------------------- сторож замечает, что копий нет

свежо = time.strftime("%Y-%m-%d %H:%M")
ЖУРНАЛ.write_text(f"{свежо} привезли 2026-08-29_04-30, сообщений 36\n",
                  encoding="utf-8")
check("watchdog-quiet-when-backups-arrive",
      watchdog.копии_приходят({}) is None, watchdog.копии_приходят({}))

ЖУРНАЛ.write_text("2026-08-01 04:31 привезли 2026-08-01_04-30, сообщений 12\n",
                  encoding="utf-8")
память = {}
жалоба = watchdog.копии_приходят(память)
check("watchdog-notices-backups-stopped", bool(жалоба), жалоба)
check("watchdog-does-not-nag-twice",
      watchdog.копии_приходят(память) is None, "пожаловался дважды подряд")

shutil.rmtree(SANDBOX, ignore_errors=True)
shutil.rmtree(песочница, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
