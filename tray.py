"""Значок Velix в области уведомлений.

pystray держит свой цикл событий, поэтому значок живёт в отдельном потоке, а
нажатия в меню передаются в окно через ту же очередь, что и сетевые события —
трогать Tkinter из чужого потока нельзя.
"""

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # без pystray просто не будет значка
    pystray = None


def make_icon_image(path=None, side=64):
    """Картинка для значка: файл иконки, а если его нет — синий кружок с V."""
    if path is not None:
        try:
            with Image.open(path) as source:
                return source.convert("RGBA").resize((side, side))
        except Exception:
            pass

    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, side - 1, side - 1), fill=(47, 107, 255, 255))
    draw.polygon([(side * 0.28, side * 0.3), (side * 0.5, side * 0.72),
                  (side * 0.72, side * 0.3)], fill=(255, 255, 255, 255))
    return image


class Tray:
    """Значок в трее с меню «Открыть» и «Выйти»."""

    def __init__(self, on_open, on_quit, icon_path=None):
        self.on_open = on_open
        self.on_quit = on_quit
        self.icon_path = icon_path
        self.icon = None
        self._thread = None

    @property
    def available(self):
        return pystray is not None

    def show(self):
        """Показывает значок. Повторный вызов ничего не ломает."""
        if not self.available or self.icon is not None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Открыть Velix", lambda *_: self.on_open(), default=True),
            pystray.MenuItem("Выйти", lambda *_: self.on_quit()),
        )
        self.icon = pystray.Icon("velix", make_icon_image(self.icon_path),
                                 "Velix", menu)
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def hide(self):
        """Убирает значок."""
        if self.icon is None:
            return
        try:
            self.icon.stop()
        except Exception:
            pass
        self.icon = None

    def notify(self, title, message):
        """Всплывающее уведомление у значка, если система его умеет."""
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass  # не все системы это поддерживают
