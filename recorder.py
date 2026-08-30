"""Запись голоса и кружочка в окне.

Пишет тот же ffmpeg, что уже лежит в сборке ради видео: он умеет и взять
звук с микрофона, и картинку с камеры, и сразу же сжать — голос в opus,
кружочек в h264 со звуком. Делать это своими руками значило бы тащить ещё
одну библиотеку ради того, что и так есть.

Камеры и микрофоны перечисляет ffpyplayer: у него для этого готовый
list_dshow_devices, он же даёт устройствам понятные имена. Обращаемся к ним
по длинному системному имени, а не по понятному: понятное бывает и с
кириллицей, и с двумя одинаковыми на одну машину.

Если ffmpeg в сборке не нашёлся, available() честно скажет об этом, и окно
просто не покажет кнопок записи.
"""

import os
import subprocess
import sys
import sysconfig
import shutil
import tempfile
import time
import uuid
from pathlib import Path

try:
    from ffpyplayer.tools import list_dshow_devices
except Exception:                       # pragma: no cover — сборка без видео
    list_dshow_devices = None

# Дольше не пишем: голос — не подкаст, кружочек — не кино
MAX_VOICE = 300
MAX_CIRCLE = 60

# Сторона кружочка. 480 — то, что видно на любом экране и весит копейки
CIRCLE_SIDE = 480

# Окно консоли при запуске ffmpeg показывать незачем
БЕЗ_ОКНА = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _где_ffmpeg():
    """Ищет ffmpeg там, где он может лежать."""
    свой = os.environ.get("VELIX_FFMPEG")
    if свой and Path(свой).exists():
        return Path(свой)

    места = []
    внутри = getattr(sys, "_MEIPASS", None)
    if внутри:
        # В собранном exe он лежит рядом с остальным содержимым
        места.append(Path(внутри) / "ffmpeg.exe")

    # Колесо ffpyplayer кладёт ffmpeg в share рядом с самим Python
    for корень in (sysconfig.get_path("data"), sys.prefix, sys.base_prefix):
        if корень:
            места.append(Path(корень) / "share" / "ffpyplayer" / "ffmpeg"
                         / "bin" / "ffmpeg.exe")
    try:
        import site
        места.append(Path(site.getuserbase()) / "share" / "ffpyplayer"
                     / "ffmpeg" / "bin" / "ffmpeg.exe")
    except Exception:                   # pragma: no cover
        pass

    for место in места:
        if место.exists():
            return место

    найдено = shutil.which("ffmpeg")
    return Path(найдено) if найдено else None


FFMPEG = _где_ffmpeg()


def available():
    """Умеет ли эта сборка записывать голос и кружочки."""
    return FFMPEG is not None and list_dshow_devices is not None


def _устройства():
    if list_dshow_devices is None:
        return {}, {}, {}
    try:
        return list_dshow_devices()
    except Exception:                   # pragma: no cover — нет DirectShow
        return {}, {}, {}


def microphones():
    """Микрофоны: [{'id': …, 'name': …}] в том порядке, в каком их видит система."""
    _, звук, имена = _устройства()
    return [{"id": айди, "name": имена.get(айди, айди)} for айди in звук]


def cameras():
    """Камеры — тем же списком."""
    видео, _, имена = _устройства()
    return [{"id": айди, "name": имена.get(айди, айди)} for айди in видео]


def _выбрать(список, запомненное):
    """Устройство из настроек, а если его нет — первое попавшееся."""
    if not список:
        return None
    for одно in список:
        if одно["id"] == запомненное or одно["name"] == запомненное:
            return одно["id"]
    return список[0]["id"]


def pick_microphone(запомненное=None):
    return _выбрать(microphones(), запомненное)


def pick_camera(запомненное=None):
    return _выбрать(cameras(), запомненное)


class Recording:
    """Одна запись: началась, идёт, закончилась файлом.

    Останавливаем не убийством, а буквой q в ffmpeg: иначе mp4 останется без
    заголовка в конце и не откроется ни у кого.
    """

    def __init__(self, kind, microphone, camera=None, folder=None):
        self.kind = kind
        self.path = Path(folder or tempfile.gettempdir()) / (
            f"velix-{kind}-{uuid.uuid4().hex[:8]}"
            + (".ogg" if kind == "voice" else ".mp4"))
        self.started = time.monotonic()
        self.error = None
        self.process = None

        if FFMPEG is None:
            self.error = "нет ffmpeg"
            return
        if not microphone:
            self.error = "нет микрофона"
            return
        if kind == "circle" and not camera:
            self.error = "нет камеры"
            return

        try:
            self.process = subprocess.Popen(
                self._команда(microphone, camera),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=БЕЗ_ОКНА)
        except OSError as беда:         # pragma: no cover — занятое устройство
            self.error = str(беда)

    def _команда(self, microphone, camera):
        если_голос = self.kind == "voice"
        команда = [str(FFMPEG), "-hide_banner", "-loglevel", "error"]

        if not если_голос:
            команда += ["-f", "dshow", "-video_size", "640x480",
                        "-framerate", "25", "-i", f"video={camera}"]
        команда += ["-f", "dshow", "-i", f"audio={microphone}"]

        if если_голос:
            # Опус на 24 килобитах — это разборчивая речь и три килобайта в
            # секунду: минута голоса весит меньше одной фотографии
            команда += ["-t", str(MAX_VOICE), "-ac", "1", "-ar", "48000",
                        "-c:a", "libopus", "-b:a", "24k"]
        else:
            # Кружочек и есть кружочек: берём из кадра квадрат по середине,
            # круглым его сделает уже клиент — так файл остаётся обычным mp4
            команда += [
                "-t", str(MAX_CIRCLE),
                "-vf", (f"crop='min(iw,ih)':'min(iw,ih)',"
                        f"scale={CIRCLE_SIDE}:{CIRCLE_SIDE}"),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
            ]

        return команда + ["-y", str(self.path)]

    # ---------------------------------------------------------------- ход

    @property
    def seconds(self):
        """Сколько уже пишем."""
        return time.monotonic() - self.started

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """Заканчивает запись по-хорошему и возвращает путь к файлу."""
        if self.process is None:
            return None

        сколько = self.seconds
        try:
            if self.process.poll() is None:
                self.process.stdin.write(b"q\n")
                self.process.stdin.flush()
        except OSError:                 # pragma: no cover — уже закрылся сам
            pass

        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:    # pragma: no cover
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self._закрыть_вход()

        if not self.path.exists() or self.path.stat().st_size < 512:
            self.error = self.error or "запись не получилась"
            self.forget()
            return None

        self.seconds_done = max(1, round(сколько))
        return self.path

    def cancel(self):
        """Бросает запись и убирает файл."""
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except OSError:             # pragma: no cover
                pass
        self._закрыть_вход()
        self.forget()

    def forget(self):
        """Убирает временный файл, если он остался."""
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:                 # pragma: no cover — файл ещё занят
            pass

    def _закрыть_вход(self):
        if self.process is not None and self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:             # pragma: no cover
                pass
