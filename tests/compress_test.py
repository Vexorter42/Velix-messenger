"""Сжатие вложений: что ужимается, что остаётся нетронутым."""

import io
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import media  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


def photo(size=(4000, 3000)):
    """Похоже на снимок с телефона: шум сенсора поверх плавных переходов.

    Ровные градиенты PNG ужимает почти в ноль, а настоящая фотография так не
    жмётся — поэтому подмешиваем шум и отдаём JPEG высокого качества, как
    отдал бы телефон.
    """
    noise = Image.effect_noise(size, 48).convert("RGB")
    gradient = Image.new("RGB", size)
    draw = ImageDraw.Draw(gradient)
    for y in range(0, size[1], 4):
        shade = int(255 * y / size[1])
        draw.rectangle([0, y, size[0], y + 4], fill=(shade, 120, 255 - shade))
    image = Image.blend(gradient, noise, 0.45)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def transparent_png():
    image = Image.new("RGBA", (900, 700), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([50, 50, 850, 650], fill=(240, 90, 120, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def animation():
    frames = [Image.new("RGB", (200, 150), (i * 40, 60, 200)) for i in range(5)]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=90)
    return buffer.getvalue()


def tiny_png():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# --- большая фотография ужимается и теряет лишние пиксели
big = photo()
name, packed = media.compress("image", "снимок.jpg", big)
check("compress-shrinks", len(packed) < len(big) // 4,
      f"{len(big)} -> {len(packed)} байт")
check("compress-keeps-name", name == "снимок.jpg", name)
with Image.open(io.BytesIO(packed)) as result:
    check("compress-caps-side", max(result.size) == media.MAX_SIDE, result.size)
    check("compress-keeps-proportions",
          abs(result.size[0] / result.size[1] - 4000 / 3000) < 0.01, result.size)
    check("compress-still-readable", result.mode == "RGB", result.mode)
print(f"   для справки: {len(big)} -> {len(packed)} байт, {media.describe(len(big), len(packed))}")

# --- непрозрачный PNG переезжает в JPEG вместе с именем
# берём шумную картинку: сплошную заливку PNG ужимает лучше JPEG,
# и сервер справедливо оставил бы оригинал вместе с именем
opaque = Image.effect_noise((2200, 1600), 60).convert("RGB")
buffer = io.BytesIO()
opaque.save(buffer, "PNG")
name, packed = media.compress("image", "плакат.png", buffer.getvalue())
check("compress-renames-to-jpg", name == "плакат.jpg", name)

# --- прозрачность не теряется: остаётся PNG
name, packed = media.compress("image", "стикер.png", transparent_png())
check("compress-alpha-stays-png", name == "стикер.png", name)
with Image.open(io.BytesIO(packed)) as result:
    check("compress-alpha-preserved", result.mode in ("RGBA", "LA", "P"), result.mode)
    check("compress-alpha-corner-transparent",
          result.convert("RGBA").getpixel((2, 2))[3] == 0,
          result.convert("RGBA").getpixel((2, 2)))

# --- гифка не трогается вообще
gif = animation()
name, packed = media.compress("gif", "пляска.gif", gif)
check("compress-gif-untouched", packed == gif and name == "пляска.gif", name)

# --- видео и файлы мимо
blob = b"\x00\x01\x02" * 5000
check("compress-video-untouched", media.compress("video", "ролик.mp4", blob) == ("ролик.mp4", blob))
check("compress-file-untouched", media.compress("file", "архив.zip", blob) == ("архив.zip", blob))

# --- крошечная картинка, которую сжатие только раздуло бы
small = tiny_png()
name, packed = media.compress("image", "точка.png", small)
check("compress-keeps-smaller-original", packed == small and name == "точка.png",
      f"{len(small)} -> {len(packed)}")

# --- битые данные отдаются как есть
broken = "это точно не картинка".encode("utf-8")
check("compress-broken-untouched",
      media.compress("image", "битое.png", broken) == ("битое.png", broken))

# --- описание для лога
check("compress-describe", "минус" in media.describe(1000, 250),
      media.describe(1000, 250))
check("compress-describe-nogain", media.describe(100, 100) == "без сжатия",
      media.describe(100, 100))

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
