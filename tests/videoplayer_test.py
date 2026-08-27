"""Проигрыватель: кадры идут, пауза держит, перемотка сдвигает."""

import os
import sys
import tempfile
import tkinter
from pathlib import Path

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import videoplayer  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


def снять_кино(путь, секунд=4, ширина=320, высота=240, частота=15):
    """Небольшое кино: меняющийся цвет, чтобы кадры отличались друг от друга."""
    from ffpyplayer.writer import MediaWriter
    from ffpyplayer.pic import Image as FFImage

    писарь = MediaWriter(str(путь), [{
        "pix_fmt_in": "rgb24", "pix_fmt_out": "yuv420p",
        "width_in": ширина, "height_in": высота,
        "codec": "mpeg4", "frame_rate": (частота, 1)}])
    for номер in range(секунд * частота):
        цвет = bytes([(номер * 4) % 256, 90, 200] * (ширина * высота))
        писарь.write_frame(img=FFImage(plane_buffers=[цвет], pix_fmt="rgb24",
                                       size=(ширина, высота)),
                           pts=номер / частота, stream=0)
    писарь.close()


check("video-available", videoplayer.available(), "ffpyplayer не подхватился")

кино = Path(tempfile.mkdtemp(prefix="velix-kino-")) / "proba.mp4"
снять_кино(кино)
check("video-file-made", кино.exists() and кино.stat().st_size > 1000,
      кино.stat().st_size if кино.exists() else "нет файла")

root = tkinter.Tk()
root.geometry("480x360")
поверхность = tkinter.Label(root, background="#101820")
поверхность.pack(fill="both", expand=True)

счёт = {"кадров": 0}
box = VideoBox = videoplayer.VideoBox(
    поверхность, кино, (400, 300),
    on_tick=lambda игрок: счёт.__setitem__("кадров", счёт["кадров"] + 1))

шаги = []


def шаг(function):
    шаги.append(function)
    return function


@шаг
def играет():
    check("video-no-error", box.error is None, box.error)
    check("video-frames-flow", счёт["кадров"] > 5, счёт["кадров"])
    check("video-knows-duration", 3.5 < box.duration < 4.5, box.duration)
    check("video-moves", box.position > 0.2, box.position)
    check("video-scaled", box.size == (400, 300), box.size)


@шаг
def пауза():
    box.toggle()
    счёт["на паузе"] = box.position


@шаг
def стоит():
    check("video-pause-holds", abs(box.position - счёт["на паузе"]) < 0.3,
          (счёт["на паузе"], box.position))


@шаг
def перемотка():
    box.toggle()                       # снимаем с паузы
    box.seek_to(0.75)


@шаг
def перемотал():
    check("video-seek-jumps", box.position > 2.0, box.position)


@шаг
def конец():
    box.close()
    check("video-closed", box.player is None)
    root.destroy()


задержка = 1200
for функция in шаги:
    root.after(задержка, функция)
    задержка += 1200

root.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
