"""Окно двигается плавно: цвета перетекают, прокрутка доезжает."""

# Эту проверку гоняем в одиночку: она меряет длительность движений,
# а под нагрузкой от соседок часы врут
ПООДИНОЧКЕ = True


import os
import sys
import tempfile
import time
from pathlib import Path

import harness

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-motion-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------- чистая арифметика

check("mix-start", gui.mix("#000000", "#ffffff", 0.0) == "#000000",
      gui.mix("#000000", "#ffffff", 0.0))
check("mix-end", gui.mix("#000000", "#ffffff", 1.0) == "#ffffff",
      gui.mix("#000000", "#ffffff", 1.0))
check("mix-middle", gui.mix("#000000", "#ffffff", 0.5) == "#7f7f7f",
      gui.mix("#000000", "#ffffff", 0.5))
check("mix-pairs", gui.mix(("#000000", "#202020"), ("#ffffff", "#404040"), 0.0)
      == ("#000000", "#202020"),
      gui.mix(("#000000", "#202020"), ("#ffffff", "#404040"), 0.0))
check("mix-transparent-ok", gui.mix("transparent", "#ffffff", 0.5) == "#ffffff",
      gui.mix("transparent", "#ffffff", 0.5))
check("ease-ends", abs(gui.ease(0)) < 1e-9 and abs(gui.ease(1) - 1) < 1e-9,
      (gui.ease(0), gui.ease(1)))

app = gui.VelixApp()
harness.тихое_окно(app)
app.geometry("1040x680")
steps = []
запомнили = {}


def step(function):
    steps.append(function)
    return function


class Колесо:
    def __init__(self, вверх=False):
        self.delta = 120 if вверх else -120
        self.x_root = app.winfo_rootx() + 650
        self.y_root = app.winfo_rooty() + 300
        self.widget = None


@step
def открыть():
    app._show_chat()
    for номер in range(60):
        gui.ctk.CTkLabel(app.messages, text=f"строка {номер}",
                         font=app.font_body).pack(anchor="w", pady=2)
    app.update_idletasks()
    app.update()


# ----------------------------------------------------- пузырь проявляется

@step
def новый_пузырь():
    пузырь, _ = app._new_bubble("Лена", own=False)
    запомнили["пузырь"] = пузырь
    app.update()
    начало = пузырь.cget("fg_color")
    запомнили["начало"] = начало
    check("fade-starts-from-background", начало != gui.BUBBLE_IN, начало)


@step
def пузырь_доцвёл():
    пузырь = запомнили["пузырь"]
    check("fade-ends-at-colour", пузырь.cget("fg_color") == gui.BUBBLE_IN,
          пузырь.cget("fg_color"))


@step
def лента_целиком():
    # Историю рисуем разом: пузыри не должны проявляться по одному
    app.drawing_history = True
    пузырь, _ = app._new_bubble("Лена", own=False)
    app.drawing_history = False
    app.update()
    check("fade-history-instant", пузырь.cget("fg_color") == gui.BUBBLE_IN,
          пузырь.cget("fg_color"))


# --------------------------------------------------------- поездка колеса

@step
def два_щелчка():
    запомнили["до"] = app.messages._parent_canvas.yview()[0]
    app._on_wheel(Колесо())
    app._on_wheel(Колесо())
    app.update()
    check("glide-running", bool(app.glides), "поездка не началась")


@step
def доехали():
    полотно = app.messages._parent_canvas
    высота = полотно.bbox("all")[3]
    прошли = (полотно.yview()[0] - запомнили["до"]) * высота
    check("glide-sums-clicks", 190 <= прошли <= 250,
          f"за два щелчка {прошли:.0f} пикселей")
    check("glide-finished", not app.glides, app.glides)


# ------------------------------------------------------ строчка светлеет

@step
def навели():
    row = gui.ctk.CTkFrame(app.side_list, fg_color="transparent", height=40)
    row.pack(fill="x")
    app._make_hoverable(row)
    app.update_idletasks()
    запомнили["строчка"] = row
    # CustomTkinter вешает привязки не на сам кадр, а на холст внутри него:
    # указатель в жизни попадает именно туда
    getattr(row, "_canvas", row).event_generate("<Enter>", x=5, y=5)
    app.update()


@step
def светится():
    row = запомнили["строчка"]
    check("hover-lights-up", row.cget("fg_color") == gui.SIDEBAR_HOVER,
          row.cget("fg_color"))


# ------------------------------------------------------ точки в «печатает»

@step
def печатает():
    app.conversations = [{"id": 3, "kind": "direct", "title": "Руслан", "user": 2}]
    app.conversation = 3
    app.user = {"id": 1, "name": "Гоша"}
    app._on_typing({"conversation": 3, "user": 2, "nick": "Руслан"})
    запомнили["видано"] = {app.header_subtitle.cget("text")}


@step
def точки_бегут():
    for _ in range(6):
        app.update()
        запомнили["видано"].add(app.header_subtitle.cget("text"))
        time.sleep(0.25)
        app.update()
    check("typing-dots-move", len(запомнили["видано"]) > 1, запомнили["видано"])
    check("typing-dots-are-dots",
          all("печатает" in one for one in запомнили["видано"]),
          запомнили["видано"])


@step
def finish():
    app.destroy()


delay = 700
паузы = {"новый_пузырь": 500, "два_щелчка": 600, "навели": 400}
for function in steps:
    app.after(delay, function)
    delay += паузы.get(function.__name__, 1000)

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
