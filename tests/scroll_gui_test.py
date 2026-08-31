"""Окно: колесо листает крупным шагом и доезжает плавно.

Прокрутка не прыгает на месте: щелчок запускает короткую поездку с
замедлением, и мерить сдвиг нужно, когда она закончилась.
"""

# Эту проверку гоняем в одиночку: она меряет, сколько проехала лента за щелчок,
# а под нагрузкой от соседок часы врут
ПООДИНОЧКЕ = True


import os
import sys
import tempfile
import time
from pathlib import Path

import harness

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-scroll-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


app = gui.VelixApp()
harness.тихое_окно(app)
app.geometry("1040x680")
steps = []


def step(function):
    steps.append(function)
    return function


class Колесо:
    """Событие колеса над серединой ленты."""

    def __init__(self, вверх=False):
        self.delta = 120 if вверх else -120
        self.x_root = app.winfo_rootx() + 650
        self.y_root = app.winfo_rooty() + 300
        self.widget = None


@step
def fill_feed():
    app._show_chat()
    for номер in range(60):
        gui.ctk.CTkLabel(app.messages, text=f"строка {номер}",
                         font=app.font_body).pack(anchor="w", pady=2)
    app.update_idletasks()
    app.update()


@step
def scroll_down():
    app._scroll_from = app.messages._parent_canvas.yview()[0]
    app._on_wheel(Колесо())
    app.update()


@step
def check_step():
    было = app._scroll_from
    стало = app.messages._parent_canvas.yview()[0]
    app._scroll_marks = (было, стало)
    check("scroll-moves", стало > было, (было, стало))
    check("scroll-glided", not app.glides, "поездка не закончилась")
    высота = app.messages._parent_canvas.bbox("all")[3]
    пикселей = (стало - было) * высота
    check("scroll-big-enough", пикселей >= 90,
          f"за щелчок сдвинулось {пикселей:.0f} пикселей")
    check("scroll-not-crazy", пикселей <= 260,
          f"за щелчок сдвинулось {пикселей:.0f} пикселей")
    print(f"      шаг колеса: {пикселей:.0f} пикселей")


@step
def scroll_up():
    app._scroll_from = app.messages._parent_canvas.yview()[0]
    app._on_wheel(Колесо(вверх=True))
    app.update()


@step
def check_back_up():
    стало = app.messages._parent_canvas.yview()[0]
    check("scroll-back-up", стало < app._scroll_from,
          (app._scroll_from, стало))


@step
def outside():
    # Колесо над пустым местом ничего не ломает
    событие = Колесо()
    событие.x_root, событие.y_root = -5000, -5000
    check("scroll-outside-safe", app._on_wheel(событие) is None)


@step
def finish():
    app.destroy()


delay = 700
for function in steps:
    app.after(delay, function)
    delay += 900

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
