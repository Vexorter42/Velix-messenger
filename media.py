"""Сжатие вложений на стороне сервера.

Фотография с телефона весит несколько мегабайт, а в чате её всё равно никто
не разглядывает попиксельно. Поэтому сервер один раз уменьшает картинку до
разумного размера и перекодирует — на глаз разницы нет, а места на диске
уходит в разы меньше. Оригинал не сохраняется.

Гифки, видео и прочие файлы не трогаем: гифку перекодирование лишило бы
анимации, а для видео нужен ffmpeg, которого на малине нет.
"""

import io
from pathlib import Path

# Больше этого по длинной стороне картинку не держим
MAX_SIDE = 1600

# Качество JPEG: 85 — та граница, где артефактов ещё не видно
JPEG_QUALITY = 85

try:
    from PIL import Image
except ImportError:  # без Pillow сервер просто не будет сжимать
    Image = None


def _encode(image, has_alpha):
    """Кодирует картинку: с прозрачностью — в PNG, иначе в JPEG."""
    buffer = io.BytesIO()
    if has_alpha:
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), ".png"

    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY,
                              optimize=True, progressive=True)
    return buffer.getvalue(), ".jpg"


def compress(kind, name, data):
    """Возвращает (имя, байты) — по возможности меньшего объёма.

    Если сжатие не помогло или картинка не читается, возвращаем как было:
    испортить вложение хуже, чем потратить лишние килобайты.
    """
    if kind != "image" or Image is None:
        return name, data

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
            picture = image.convert("RGBA") if has_alpha else image.convert("RGB")

            if max(picture.size) > MAX_SIDE:
                picture.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

            packed, suffix = _encode(picture, has_alpha)
    except Exception:
        # Битый файл, экзотический формат, нехватка памяти — отдаём исходник
        return name, data

    if len(packed) >= len(data):
        return name, data

    return Path(name).with_suffix(suffix).name, packed


def describe(before, after):
    """Строка для лога: насколько ужалось."""
    if after >= before:
        return "без сжатия"
    return f"{before} -> {after} байт, минус {100 - after * 100 // before}%"
