"""Окно: приближение открытой фотографии."""

import io as bytes_io
import os
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab

REPO = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).with_name("shots")
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

уголок = Path(tempfile.mkdtemp(prefix="velix-zoom-"))
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


SHOTS.mkdir(exist_ok=True)

# Картинка с мелкими подробностями: на ней видно, что приближение честное
картинка = Image.new("RGB", (1400, 1000), (24, 30, 42))
рисунок = ImageDraw.Draw(картинка)
for номер in range(0, 1400, 40):
    рисунок.line([(номер, 0), (номер, 1000)], fill=(70, 120, 190), width=2)
for номер in range(0, 1000, 40):
    рисунок.line([(0, номер), (1400, номер)], fill=(70, 120, 190), width=2)
рисунок.ellipse((600, 400, 800, 600), fill=(230, 120, 60))
рисунок.text((610, 480), "VELIX", fill=(255, 255, 255))
holder = bytes_io.BytesIO()
картинка.save(holder, "PNG")
ФОТО = holder.getvalue()

app = gui.VelixApp()
app.attributes("-topmost", True)
app.geometry("1040x680")

steps = []


def step(function):
    steps.append(function)
    return function


def grab(name):
    app.lift()
    app.update_idletasks()
    app.update()
    time.sleep(0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()),
                   all_screens=True).save(SHOTS / name)
    print(f"снят {name}")


def размер_картинки():
    """Размер того, что сейчас показано в просмотрщике."""
    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("image"):
                return child.cget("image").cget("size")
            найдено = обход(child)
            if найдено:
                return найдено
        return None
    return обход(app.viewer) if app.viewer is not None else None


def колесом(сколько, вверх=True):
    """Крутит колесо над картинкой столько-то раз."""
    цель = картинка_виджет()._label
    for _ in range(сколько):
        цель.event_generate("<MouseWheel>", delta=120 if вверх else -120,
                            x=200, y=150)
    app.update()


def картинка_виджет():
    def обход(widget):
        for child in widget.winfo_children():
            if isinstance(child, gui.ctk.CTkLabel) and child.cget("image"):
                return child
            найдено = обход(child)
            if найдено:
                return найдено
        return None
    return обход(app.viewer)


@step
def open_viewer():
    app._show_full(ФОТО, "image")


@step
def check_open():
    check("zoom-viewer-open", app.viewer is not None, "просмотр не открылся")
    check("zoom-state-ready", app.zoom is not None and app.zoom["scale"] == 1.0,
          app.zoom)
    сперва = размер_картинки()
    app._zoom_start = сперва
    check("zoom-fits-window", сперва is not None and сперва[0] <= app.winfo_width(),
          сперва)
    grab("zoom-1-open.png")


@step
def zoom_in():
    колесом(3)


@step
def check_zoomed():
    check("zoom-scale-grew", app.zoom["scale"] > 1.5, app.zoom["scale"])
    стало = размер_картинки()
    check("zoom-picture-grew", стало[0] > app._zoom_start[0], (app._zoom_start, стало))
    grab("zoom-2-in.png")


@step
def zoom_out():
    колесом(6, вверх=False)


@step
def check_zoomed_out():
    check("zoom-scale-shrank", app.zoom["scale"] < 1.0, app.zoom["scale"])
    grab("zoom-3-out.png")


@step
def limits():
    колесом(40)          # выше предела не пустит
    check("zoom-upper-limit", app.zoom["scale"] == 8.0, app.zoom["scale"])
    большая = размер_картинки()
    check("zoom-draws-only-visible",
          большая[0] <= app.winfo_width() and большая[1] <= app.winfo_height(),
          f"нарисовано {большая} при окне "
          f"{app.winfo_width()}x{app.winfo_height()}")
    grab("zoom-4-limit.png")


@step
def drag_around():
    # Тянем картинку вбок: смотрим на другой её край
    было = app.zoom["cx"]
    цель = картинка_виджет()._label
    цель.event_generate("<Button-1>", x=400, y=300, rootx=800, rooty=500)
    for шаг in range(1, 6):
        цель.event_generate("<B1-Motion>", x=400 - шаг * 30, y=300,
                            rootx=800 - шаг * 30, rooty=500)
    цель.event_generate("<ButtonRelease-1>", x=250, y=300, rootx=650, rooty=500)
    app.update()
    check("zoom-drag-moves", app.zoom["cx"] > было, (было, app.zoom["cx"]))
    check("zoom-drag-stays-open", app.viewer is not None, "просмотр закрылся")
    grab("zoom-5-drag.png")


@step
def back_to_one():
    колесом(60, вверх=False)
    check("zoom-lower-limit", app.zoom["scale"] == 0.25, app.zoom["scale"])


@step
def close_viewer():
    app.event_generate("<Escape>")
    app.update()
    check("zoom-closes", app.viewer is None and app.zoom is None,
          (app.viewer, app.zoom))


@step
def finish():
    app.destroy()


delay = 700
for function in steps:
    app.after(delay, function)
    delay += 1200

app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
