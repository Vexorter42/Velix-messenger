"""Короткий звук о новом сообщении.

Файла со звуком в программе нет: он считается на месте — две мягкие ноты
с затуханием, полторы десятых секунды. Так не нужно ни носить с собой wav,
ни объяснять сборщику, куда его класть.

Играет winsound — он есть в стандартной поставке Windows. На других
системах звука пока нет, и это не беда: сообщение всё равно видно.
"""

import io
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

try:
    import winsound
except ImportError:                     # pragma: no cover — не Windows
    winsound = None

ЧАСТОТА = 22050
НОТЫ = (784, 1046)          # соль и до октавой выше: коротко и не резко
ДЛИНА = 0.075               # каждая нота


def _собрать():
    """Считает волну: две ноты подряд, каждая с затуханием к концу."""
    кадры = bytearray()
    for нота in НОТЫ:
        сколько = int(ЧАСТОТА * ДЛИНА)
        for место in range(сколько):
            доля = место / сколько
            # Затухание по краям: без него в начале и конце слышен щелчок
            громкость = min(доля * 12, 1.0) * (1.0 - доля) ** 1.5
            значение = math.sin(2 * math.pi * нота * место / ЧАСТОТА)
            кадры += struct.pack("<h", int(значение * громкость * 12000))

    холст = io.BytesIO()
    with wave.open(холст, "wb") as файл:
        файл.setnchannels(1)
        файл.setsampwidth(2)
        файл.setframerate(ЧАСТОТА)
        файл.writeframes(bytes(кадры))
    return холст.getvalue()


_файл = None


def available():
    """Умеет ли эта система звучать."""
    return winsound is not None and sys.platform == "win32"


def _где_лежит():
    """Кладёт волну во временный файл — из памяти winsound играет только
    с ожиданием, а ждать полторы десятых секунды окну нельзя."""
    global _файл
    if _файл is None or not Path(_файл).exists():
        путь = Path(tempfile.gettempdir()) / "velix-chime.wav"
        путь.write_bytes(_собрать())
        _файл = str(путь)
    return _файл


def play():
    """Играет звук о новом сообщении. Тишина — не ошибка."""
    if not available():
        return False
    try:
        winsound.PlaySound(_где_лежит(),
                           winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:                   # pragma: no cover — звук не главное
        return False
    return True
