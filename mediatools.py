"""Что сервер делает с голосом и кружочком, пока они не легли на диск.

У голоса считаем волну — те самые столбики, по которым видно, где говорят,
а где молчат. У кружочка обрезаем кадр в квадрат, впекаем поворот и снимаем
обложку: тогда клиентам не нужно знать ничего про камеры, которыми его
снимали, а телефоны у всех разные.

Всё это делает ffmpeg. Если его рядом нет — а на машине без него проверки
как раз и идут, — молча возвращаем «ничего не вышло», и голос с кружочком
живут как раньше: без волны, без обложки и как записаны.
"""

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _где_ffmpeg():
    """На малине он в PATH, а в проверках его показывают переменной."""
    свой = os.environ.get("VELIX_FFMPEG")
    if свой and Path(свой).exists():
        return свой
    return shutil.which("ffmpeg")


FFMPEG = _где_ffmpeg()

# Столбиков в волне. Полсотни — это и на телефоне видно, и в строку влезает
СТОЛБИКОВ = 48

# Сторона кружочка после приведения к порядку
КРУЖОК = 480

# Дольше этого не возимся: лучше положить как есть, чем заставить человека
# ждать неизвестно сколько
ЖДЁМ_ОБЛОЖКУ = 20
ЖДЁМ_ПЕРЕСБОРКУ = 90

# Обрезать по короткой стороне и вписать в квадрат — вот и весь кружочек
ОБРЕЗКА = f"crop='min(iw,ih)':'min(iw,ih)',scale={КРУЖОК}:{КРУЖОК},setsar=1"


def available():
    """Умеет ли эта машина приводить вложения в порядок."""
    return FFMPEG is not None


def _во_временный(данные, suffix):
    файл = tempfile.NamedTemporaryFile(prefix="velix-", suffix=suffix,
                                       delete=False)
    try:
        файл.write(данные)
    finally:
        файл.close()
    return Path(файл.name)


def _прибрать(*пути):
    for один in пути:
        try:
            if один is not None and Path(один).exists():
                Path(один).unlink()
        except OSError:                 # pragma: no cover — файл ещё занят
            pass


def waveform(данные, suffix=".m4a"):
    """Волна голосового: строка из столбиков или пустая строка.

    Столбики считаем по громкости, а не по частоте: нам не спектр рисовать,
    а показать, где в записи речь. Сама запись для этого разжимается в самый
    простой звук — восемь тысяч отсчётов в секунду, этого с запасом.
    """
    if FFMPEG is None or not данные:
        return ""

    файл = _во_временный(данные, suffix)
    try:
        готово = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(файл),
             "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            capture_output=True, timeout=ЖДЁМ_ОБЛОЖКУ)
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        _прибрать(файл)

    сырое = готово.stdout or b""
    if готово.returncode != 0 or len(сырое) < СТОЛБИКОВ * 2:
        return ""

    отсчётов = len(сырое) // 2
    шаг = отсчётов // СТОЛБИКОВ
    столбики = []
    for номер in range(СТОЛБИКОВ):
        кусок = сырое[номер * шаг * 2:(номер + 1) * шаг * 2]
        громче = 0
        # Берём не каждый отсчёт: на минуте записи их полмиллиона, а разницы
        # в картинке от этого никакой
        for место in range(0, len(кусок) - 1, 32):
            значение = int.from_bytes(кусок[место:место + 2], "little",
                                      signed=True)
            громче = max(громче, abs(значение))
        столбики.append(громче)

    предел = max(столбики) or 1
    ровные = bytes(min(255, значение * 255 // предел) for значение in столбики)
    return base64.b64encode(ровные).decode("ascii")


def read_waveform(строка):
    """Обратно из строки в числа — этим пользуются проверки."""
    try:
        return list(base64.b64decode(строка or ""))
    except Exception:
        return []


def circle_poster(данные, suffix=".mp4"):
    """Первый кадр кружочка, уже круглым размером. Байты jpeg или None."""
    if FFMPEG is None or not данные:
        return None

    файл = _во_временный(данные, suffix)
    куда = файл.with_suffix(".poster.jpg")
    try:
        готово = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(файл),
             "-frames:v", "1", "-vf", ОБРЕЗКА, "-q:v", "4", "-y", str(куда)],
            capture_output=True, timeout=ЖДЁМ_ОБЛОЖКУ)
        if готово.returncode == 0 and куда.exists() and куда.stat().st_size:
            return куда.read_bytes()
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        _прибрать(файл, куда)


def tidy_circle(данные, suffix=".mp4"):
    """Приводит кружочек к общему виду: квадрат, поворот впечён, звук как был.

    Телефоны пишут кто во что горазд: то кадр лёжа с пометкой «поверни», то
    формат с неквадратным пикселем — и каждый клиент разбирается с этим сам,
    кто как умеет. Проще разобраться один раз здесь.

    Возвращает новые байты или None, если ничего не вышло: тогда кладём то,
    что прислали, — потерять запись хуже, чем показать её неровной.
    """
    if FFMPEG is None or not данные:
        return None

    файл = _во_временный(данные, suffix)
    куда = файл.with_suffix(".tidy.mp4")
    try:
        готово = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(файл),
             "-vf", ОБРЕЗКА, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
             "-movflags", "+faststart", "-y", str(куда)],
            capture_output=True, timeout=ЖДЁМ_ПЕРЕСБОРКУ)
        if готово.returncode != 0 or not куда.exists() or not куда.stat().st_size:
            return None
        return куда.read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        _прибрать(файл, куда)
