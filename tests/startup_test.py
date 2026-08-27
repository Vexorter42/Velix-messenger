"""Сколько времени уходит на запуск — и на что именно."""

import ctypes
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXE = REPO / "dist" / "Velix.exe"
user32 = ctypes.windll.user32


def замер(что, команда, повторов=3):
    времена = []
    for _ in range(повторов):
        начало = time.perf_counter()
        subprocess.run(команда, cwd=REPO, capture_output=True)
        времена.append(time.perf_counter() - начало)
    print(f"{что:<40} {min(времена):5.2f} с", flush=True)
    return min(времена)


def до_окна(что, команда, повторов=3):
    """Время от запуска до появления окна на экране."""
    времена = []
    for _ in range(повторов):
        начало = time.perf_counter()
        process = subprocess.Popen(команда, cwd=REPO)
        окно = 0
        while not окно and time.perf_counter() - начало < 120:
            окно = user32.FindWindowW(None, "Velix")
        времена.append(time.perf_counter() - начало)
        process.terminate()
        process.wait(timeout=10)
        time.sleep(1.0)
    print(f"{что:<40} {min(времена):5.2f} с", flush=True)
    return min(времена)


print("== по кусочкам, из исходников ==", flush=True)
замер("пустой python", [sys.executable, "-c", "pass"])
замер("+ tkinter", [sys.executable, "-c", "import tkinter"])
замер("+ customtkinter", [sys.executable, "-c", "import customtkinter"])
замер("+ PIL.Image", [sys.executable, "-c", "import PIL.Image"])
замер("+ websockets", [sys.executable, "-c", "import websockets"])
замер("весь gui.py, без окна", [sys.executable, "-c", "import gui"])

print("\n== до появления окна ==", flush=True)
до_окна("из исходников (python gui.py)", [sys.executable, "gui.py"])
if EXE.exists():
    до_окна("собранный Velix.exe (onefile)", [str(EXE)])
