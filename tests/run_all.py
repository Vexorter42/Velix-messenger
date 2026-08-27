"""Прогон всех проверок подряд.

    python tests/run_all.py           — всё
    python tests/run_all.py --quick   — без оконных: они требуют экрана

Оконные проверки поднимают настоящее окно Tk и потому не годятся для
машины без монитора; на своей машине гонять стоит всё.
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP = {"run_all.py"}

быстро = "--quick" in sys.argv

# Оконные узнаём по тому, что они ввозят: имя файла врёт (проверка про
# запоздавшую картинку окно поднимает, а «gui» в названии не имеет)
ОКОННОЕ = ("import gui", "customtkinter", "import videoplayer",
           "ffpyplayer", "pystray", "ImageGrab")


def нужен_экран(path):
    текст = path.read_text(encoding="utf-8", errors="replace")
    return any(признак in текст for признак in ОКОННОЕ)


tests = sorted(path for path in HERE.glob("*_test*.py")
               if path.name not in SKIP and "retired" not in path.name
               and not path.name.startswith("patch_"))
if быстро:
    tests = [path for path in tests if not нужен_экран(path)]

print(f"проверок: {len(tests)}{' (без оконных)' if быстро else ''}\n")
bad = []
for path in tests:
    started = time.time()
    result = subprocess.run([sys.executable, "-X", "utf8", path.name], cwd=HERE,
                            capture_output=True, text=True, timeout=900,
                            encoding="utf-8", errors="replace")
    tail = [line for line in (result.stdout or "").splitlines()
            if line.startswith("ИТОГО")]
    mark = "OK  " if result.returncode == 0 else "ПЛОХО"
    print(f"{mark} {path.name:<28} {tail[-1] if tail else '(нет итога)'}"
          f"  {time.time() - started:.0f}с")
    if result.returncode != 0:
        bad.append(path.name)
        for line in (result.stdout or "").splitlines():
            if "FAIL" in line:
                print(f"      {line}")
        error = (result.stderr or "").strip().splitlines()
        if error:
            print(f"      {error[-1][:160]}")

print(f"\nсорвалось: {len(bad)} из {len(tests)}")
for name in bad:
    print(" ", name)
sys.exit(1 if bad else 0)
