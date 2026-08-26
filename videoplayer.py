"""Видео прямо в окне, без внешнего проигрывателя.

Tkinter не умеет видео вовсе, поэтому делаем это сами: ffpyplayer разбирает
файл своими потоками и отдаёт готовые кадры, звук он же играет через SDL.
Наше дело — вовремя забрать кадр, превратить его в картинку и положить на
метку. Уменьшает кадры тот же ffpyplayer (set_size), в си-коде: растягивать
кадр средствами Python значило бы ронять частоту вдвое.

Если библиотеки в сборке не оказалось, VideoBox честно об этом скажет, а
клиент предложит открыть файл системным проигрывателем, как и раньше.
"""

from PIL import Image, ImageTk

try:
    from ffpyplayer.player import MediaPlayer
except Exception:                       # pragma: no cover — сборка без видео
    MediaPlayer = None


def available():
    """Умеет ли эта сборка показывать видео сама."""
    return MediaPlayer is not None


def fit(size, box):
    """Во что вписать кадр, не искажая пропорций."""
    ширина, высота = size
    коробка_ш, коробка_в = box
    if ширина <= 0 or высота <= 0:
        return коробка_ш, коробка_в
    # Видео, в отличие от снимка, растягиваем и вверх: смотреть кино в
    # почтовую марку посреди пустого экрана незачем
    доля = min(коробка_ш / ширина, коробка_в / высота)
    return max(int(ширина * доля), 16), max(int(высота * доля), 16)


class VideoBox:
    """Проигрывание одного файла на одну метку.

    surface — обычная tkinter-метка: у неё нет лишних слоёв, а кадры идут
    десятками в секунду, и каждый лишний слой на этом пути виден глазу.
    """

    def __init__(self, surface, path, box, on_tick=None, on_end=None):
        self.surface = surface
        self.box = box
        self.on_tick = on_tick
        self.on_end = on_end

        self.player = None
        self.photo = None              # ссылку держим, иначе Tk сотрёт картинку
        self.job = None
        self.size = None               # какого размера просим кадры
        self.paused = False
        self.finished = False
        self.duration = 0.0
        self.position = 0.0
        self.error = None

        if MediaPlayer is None:
            self.error = "нет библиотеки"
            return

        try:
            self.player = MediaPlayer(str(path), ff_opts={
                "out_fmt": "rgb24",
                # Звук ведёт счёт времени: так картинка не убегает от голоса
                "sync": "audio",
            })
        except Exception as беда:                     # pragma: no cover
            self.error = str(беда)
            return

        self.job = self.surface.after(30, self._tick)

    # ------------------------------------------------------------ управление

    def toggle(self):
        """Пауза и продолжение — по щелчку и по пробелу."""
        if self.player is None:
            return
        if self.finished:
            self.seek_to(0.0)
            self.paused = False
        else:
            self.paused = not self.paused
        self.player.set_pause(self.paused)
        if not self.paused and self.job is None:
            self.job = self.surface.after(10, self._tick)

    def seek_to(self, доля):
        """Перемотка: доля от начала, от нуля до единицы."""
        if self.player is None or self.duration <= 0:
            return
        куда = max(0.0, min(доля, 0.999)) * self.duration
        try:
            self.player.seek(куда, relative=False, accurate=False)
        except Exception:                             # pragma: no cover
            return
        self.finished = False
        if self.paused:
            # На паузе кадр всё равно нужно обновить, иначе экран не сдвинется
            self.player.set_pause(False)
            self.surface.after(120, self._pause_again)
        if self.job is None:
            self.job = self.surface.after(10, self._tick)

    def _pause_again(self):
        if self.player is not None and self.paused:
            self.player.set_pause(True)

    def set_volume(self, громкость):
        if self.player is not None:
            self.player.set_volume(max(0.0, min(float(громкость), 1.0)))

    def close(self):
        """Останавливает всё. Звать обязательно: SDL держит звук открытым."""
        if self.job is not None:
            try:
                self.surface.after_cancel(self.job)
            except Exception:                         # pragma: no cover
                pass
            self.job = None
        if self.player is not None:
            try:
                self.player.close_player()
            except Exception:                         # pragma: no cover
                pass
            self.player = None
        self.photo = None

    # ------------------------------------------------------------- показ

    def _tick(self):
        self.job = None
        if self.player is None or not self.surface.winfo_exists():
            return

        try:
            кадр, состояние = self.player.get_frame()
        except Exception as беда:                     # pragma: no cover
            self.error = str(беда)
            return

        if состояние == "eof":
            self.finished = True
            self.position = self.duration
            if self.on_tick:
                self.on_tick(self)
            if self.on_end:
                self.on_end(self)
            return

        if self.duration <= 0:
            сведения = self.player.get_metadata() or {}
            длина = сведения.get("duration") or 0
            self.duration = float(длина) if длина else 0.0

        if кадр is not None:
            картинка, метка_времени = кадр
            self.position = float(метка_времени or 0)
            self._draw(картинка)
            if self.on_tick:
                self.on_tick(self)

        # Сколько ждать до следующего кадра, ffpyplayer считает сам
        пауза = 0.03 if состояние == "paused" or not состояние else float(состояние)
        self.job = self.surface.after(max(int(пауза * 1000), 4), self._tick)

    def _draw(self, картинка):
        if self.size is None:
            self.size = fit(картинка.get_size(), self.box)
            if self.size != картинка.get_size():
                # Уменьшает ffmpeg, в си-коде: дальше кадры приходят готовыми
                self.player.set_size(*self.size)
                return

        ширина, высота = картинка.get_size()
        полосы = картинка.get_linesizes()
        данные = картинка.to_bytearray()[0]
        шаг = полосы[0] if полосы else ширина * 3
        try:
            готовое = Image.frombuffer("RGB", (ширина, высота), bytes(данные),
                                       "raw", "RGB", шаг, 1)
        except (ValueError, TypeError):               # pragma: no cover
            return

        self.photo = ImageTk.PhotoImage(готовое)
        self.surface.configure(image=self.photo)
