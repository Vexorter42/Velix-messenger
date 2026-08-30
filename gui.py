"""Графический клиент Velix на CustomTkinter, оформленный в духе Telegram.

Сетевая часть живёт в отдельном потоке со своим циклом asyncio, а с
интерфейсом общается через очередь: Tkinter нельзя трогать из чужого потока,
поэтому окно само раз в несколько десятков миллисекунд забирает накопившиеся
события.
"""

import asyncio
import io
import os
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
import time
import tkinter
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import websockets
from PIL import Image, ImageDraw, ImageSequence

import autostart
import i18n
import chime
import localcache
import mediacache
import recorder
import videoplayer
import protocol
import store
import tray as tray_module
import updates
import version
from i18n import t

PORT = 8765

# Картинку в чате шире этого не показываем — иначе пузырь распирает окно
MAX_PICTURE = (360, 360)

# Кадров в гифке берём не больше: длинные ролики иначе съедают память
# Насколько листать за один щелчок колеса. CustomTkinter двигает на
# двадцать пикселей — за такой прокруткой не поспеть
WHEEL_STEP = 110

# Плавность. Tkinter не знает ни прозрачности, ни переходов, поэтому
# «плавно» здесь — это несколько шагов по таймеру: цвет перетекает из
# одного в другой, прокрутка доезжает с замедлением. Шаг в 16 миллисекунд
# — это шестьдесят кадров в секунду, к которым привык глаз
STEP_MS = 16
GLIDE_MS = 190          # сколько едет прокрутка до места
FADE_MS = 200           # сколько проявляется пузырь
HOVER_MS = 120          # сколько разгорается строчка под указателем
SIDEBAR_HOVER = ("#eef2f6", "#1f2c3a")


def ease(доля):
    """Замедление к концу: так останавливаются вещи в жизни."""
    return 1 - (1 - доля) ** 3


def _rgb(цвет):
    цвет = str(цвет).lstrip("#")
    if len(цвет) == 3:
        цвет = "".join(буква * 2 for буква in цвет)
    return tuple(int(цвет[место:место + 2], 16) for место in (0, 2, 4))


def mix(первый, второй, доля):
    """Цвет между двумя. Пара «светлый, тёмный» смешивается по частям."""
    if isinstance(первый, (tuple, list)) or isinstance(второй, (tuple, list)):
        левые = первый if isinstance(первый, (tuple, list)) else (первый, первый)
        правые = второй if isinstance(второй, (tuple, list)) else (второй, второй)
        return tuple(mix(один, другой, доля)
                     for один, другой in zip(левые, правые))
    try:
        было, стало = _rgb(первый), _rgb(второй)
    except (ValueError, IndexError):
        return второй               # «transparent» и прочее не смешиваем
    смесь = tuple(int(один + (другой - один) * доля)
                  for один, другой in zip(было, стало))
    return "#%02x%02x%02x" % смесь

# Сколько вложений просим одновременно. Двадцать фотографий, запрошенных
# разом, забивают канал: ответ на «покажи переписку» ждёт своей очереди за
# мегабайтами, и лента стоит пустая — именно так пропадали личные
# переписки, где фотографий нет вовсе
FETCH_WINDOW = 2

# Сколько картинок разбираем за один заход. Каждая — это распаковка,
# уменьшение и запись на диск, десятки миллисекунд. Два десятка подряд
# заняли бы окно на секунды, и пришедшая тем временем переписка
# показалась бы только в конце
BLOBS_PER_TURN = 2

MAX_GIF_FRAMES = 120

AVATAR_SMALL = 36
AVATAR_LARGE = 96

# Скругления. Одна лесенка на весь интерфейс, чтобы углы не спорили друг с
# другом: мелочь внутри строки, сама строка, карточка, пузырь, лист. Круглое
# (аватарки, кнопки композера) считается от размера и в лесенку не входит.
R_SMALL = 12       # крестики, значки, плашки внутри строки
R_ITEM = 14        # поля ввода, строки списка, обычные кнопки
R_CARD = 16        # меню, карточки внутри списков
R_BUBBLE = 18      # пузыри сообщений
R_SHEET = 20       # большие карточки: вход, профиль, настройки

# Палитра снята с Telegram Desktop. Пары — (светлая тема, тёмная тема),
# CustomTkinter сам подставит нужную половину.
SIDEBAR = ("#ffffff", "#17212b")
SIDEBAR_ACTIVE = ("#419fd9", "#2b5278")
CHAT_BG = ("#e9eef3", "#101a24")
COMPOSER = ("#ffffff", "#17212b")
INPUT_BG = ("#f2f4f7", "#26333f")
BUBBLE_IN = ("#ffffff", "#1b2836")
BUBBLE_OUT = ("#effdde", "#2b5278")
TEXT = ("#000000", "#ffffff")
TEXT_OUT = ("#000000", "#ffffff")
MUTED = ("#707579", "#708499")
TIME_IN = ("#a1aab3", "#6d7f8f")
TIME_OUT = ("#62ad5a", "#7da8d3")
ACCENT = ("#3390ec", "#5288c1")
ACCENT_HOVER = ("#2b7fd4", "#3f6d9e")
SEPARATOR = ("#e3e8ed", "#1f2c3a")
SERVICE_BG = ("#ffffff", "#1b2836")
ON_ACCENT = "#ffffff"
ONLINE = ("#31a24c", "#4dc866")
OFFLINE = ("#d1435b", "#ec5f75")

# Всплывающее меню сообщения: карточка поверх переписки
MENU_BG = ("#ffffff", "#1f2c3a")
MENU_HOVER = ("#f1f3f5", "#2a3a4b")

# Галочки о доставке: серые, пока сообщение не прочитали, и голубые после
TICK_SENT = ("#8a9aa9", "#7da8d3")
TICK_READ = ("#34b7f1", "#7ee2ff")

AVATAR_COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862",
                 "#6ec9cb", "#65aadd", "#ee7aae"]

# Что можно поставить на сообщение — короткий набор, как в Telegram
EMOJI = ["👍", "❤", "😂", "🔥", "😢", "👎"]

DEFAULT_SETTINGS = {
    "language": i18n.DEFAULT,  # английский, пока не выбрали другой
    "theme": "dark",
    "tray": True,        # закрытие окна прячет его в трей, а не выходит
    "autostart": False,  # запуск вместе с Windows
    "sound": True,       # короткий звук о новом сообщении
}


def avatar_color(nickname):
    """Цвет аватарки закреплён за именем, чтобы не прыгал между запусками."""
    return AVATAR_COLORS[sum(map(ord, nickname or "?")) % len(AVATAR_COLORS)]


def host_and_port(address):
    """Разбирает то, что ввёл пользователь, на хост и порт.

    Порт можно дописать через двоеточие — "vexorter.duckdns.org:9000".
    Без него подставляется стандартный 8765.
    """
    address = address.strip() or "localhost"

    if address.startswith("["):  # IPv6 в скобках: [::1] или [::1]:8765
        host, _, rest = address.partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return f"{host}]", rest[1:]
        return f"{host}]", str(PORT)

    if address.count(":") == 1:
        host, _, port = address.partition(":")
        if port.isdigit() and host:
            return host, port

    if address.count(":") > 1:  # голый IPv6 без порта
        return f"[{address}]", str(PORT)

    return address, str(PORT)


def build_uri(address):
    """Один адрес подключения — на случай, когда схема указана явно."""
    return connection_uris(address)[0]


def connection_uris(address):
    """Адреса для попыток подключения, от защищённого к обычному.

    Сначала пробуем wss:// — это тот же TLS, что у банковских сайтов.
    Если сервер его не умеет, откатываемся на ws://, но пользователю об
    этом честно говорим.
    """
    address = address.strip() or "localhost"

    for scheme in ("wss://", "ws://"):
        if address.lower().startswith(scheme):
            host, port = host_and_port(address[len(scheme):])
            return [f"{scheme}{host}:{port}"]

    host, port = host_and_port(address)
    return [f"wss://{host}:{port}", f"ws://{host}:{port}"]


def resource_path(name):
    """Путь к файлу рядом с программой.

    В собранном PyInstaller'ом exe ресурсы распаковываются во временный
    каталог, путь к которому лежит в sys._MEIPASS.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def local_time(moment):
    """Время сообщения из строки UTC в местном поясе."""
    try:
        return datetime.fromisoformat(moment).astimezone()
    except (TypeError, ValueError):
        return datetime.now()


def open_in_system(path):
    """Открывает файл тем, чем его открывает система."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def circular(data, side):
    """Вырезает из картинки круг нужного размера — так рисует аватарки Telegram."""
    with Image.open(io.BytesIO(data)) as source:
        picture = source.convert("RGBA")

    # Берём квадрат по центру, чтобы лицо не растянулось
    smallest = min(picture.size)
    left = (picture.width - smallest) // 2
    top = (picture.height - smallest) // 2
    picture = picture.crop((left, top, left + smallest, top + smallest))
    picture = picture.resize((side, side), Image.LANCZOS)

    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    picture.putalpha(mask)
    return picture


ICON_SIDE = 20


def draw_icon(name, colour):
    """Рисует значок меню линиями.

    Готовых значков под рукой нет, а шрифтовые стрелки в Tk выходят
    то крошечными, то квадратами — рисуем сами и получаем одинаковые.
    """
    picture = Image.new("RGBA", (ICON_SIDE * 2, ICON_SIDE * 2), (0, 0, 0, 0))
    pen = ImageDraw.Draw(picture)
    line = 3
    box = ICON_SIDE * 2

    if name in ("reply", "forward"):
        # Стрелка с загибом: остриё слева, хвост уходит вниз
        points = [(16, 12), (8, 20), (16, 28)]
        shaft = [(8, 20), (26, 20), (26, 32)]
        if name == "forward":
            points = [(box - x, y) for x, y in points]
            shaft = [(box - x, y) for x, y in shaft]
        pen.line(points, fill=colour, width=line, joint="curve")
        pen.line(shaft, fill=colour, width=line, joint="curve")

    elif name == "pin":
        pen.line([(20, 8), (20, 24)], fill=colour, width=line)
        pen.line([(12, 24), (28, 24)], fill=colour, width=line)
        pen.line([(20, 24), (20, 32)], fill=colour, width=line)
        pen.ellipse([15, 5, 25, 15], outline=colour, width=line)

    elif name == "copy":
        pen.rounded_rectangle([8, 8, 26, 28], radius=4, outline=colour, width=line)
        pen.rounded_rectangle([14, 14, 32, 34], radius=4, outline=colour, width=line)

    elif name == "pencil":
        # Карандаш наискосок: черта грифеля, тело и остриё
        pen.line([(10, 30), (28, 12)], fill=colour, width=line + 3)
        pen.line([(26, 8), (32, 14)], fill=colour, width=line)
        pen.line([(28, 10), (30, 12)], fill=colour, width=line)
        pen.polygon([(8, 32), (9, 26), (14, 31)], fill=colour)

    elif name == "trash":
        pen.line([(8, 12), (32, 12)], fill=colour, width=line)
        pen.line([(16, 8), (24, 8)], fill=colour, width=line)
        pen.rounded_rectangle([11, 12, 29, 33], radius=4, outline=colour, width=line)
        pen.line([(17, 18), (17, 28)], fill=colour, width=line)
        pen.line([(23, 18), (23, 28)], fill=colour, width=line)

    return picture.resize((ICON_SIDE, ICON_SIDE), Image.LANCZOS)


_icons = {}


def menu_icon(name):
    """Значок для светлой и тёмной темы, посчитанный один раз."""
    if name not in _icons:
        _icons[name] = ctk.CTkImage(
            light_image=draw_icon(name, "#4a5964"),
            dark_image=draw_icon(name, "#aebac4"),
            size=(ICON_SIDE, ICON_SIDE))
    return _icons[name]


def short(text, limit):
    """Укорачивает строку для списка чатов, как в Telegram.

    Заодно склеивает переводы строк: в строке списка помещается одна,
    а многострочное сообщение растянуло бы её по высоте.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def drop_placeholder(entry):
    """Гасит подсказку в поле, куда мы вставляем текст сами.

    CustomTkinter убирает подсказку по щелчку в поле. Мы же пишем прямо во
    внутреннее поле Tk, и обёртка об этом не узнаёт: на экране текст есть, а
    get() возвращает пустую строку — и на сервер уходит пустой адрес.
    """
    field = getattr(entry, "master", None)
    if getattr(field, "_placeholder_text_active", False):
        field._deactivate_placeholder()


class Network:
    """Подключение к серверу в фоновом потоке."""

    def __init__(self, events):
        self.events = events
        self.loop = None
        self.pen = None                # замок на отправку, заводится в цикле
        self.websocket = None

    def connect(self, uris):
        if isinstance(uris, str):
            uris = [uris]
        threading.Thread(target=self._run, args=(list(uris),), daemon=True).start()

    def send(self, frame, payload=None, wait=False):
        """Отправляет кадр, при необходимости следом двоичный.

        wait заставляет дождаться отправки. Это нужно тому, кто льёт файл
        кусками: иначе он свалит в очередь весь гигабайт разом, и память
        кончится ровно так же, как если бы мы слали одним куском.
        """
        if self.websocket is None or self.loop is None:
            return False

        async def deliver(websocket):
            # Кадр и его содержимое должны уйти подряд. Без замка две
            # отправки перемешались бы, и сервер принял бы чужой заголовок
            # за содержимое файла
            async with self.pen:
                await websocket.send(frame)
                if payload is not None:
                    await websocket.send(payload)

        задача = asyncio.run_coroutine_threadsafe(deliver(self.websocket),
                                                  self.loop)
        if not wait:
            return True
        try:
            задача.result(timeout=120)
            return True
        except Exception:
            return False

    def disconnect(self):
        if self.websocket is not None and self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.websocket.close(), self.loop)

    def _run(self, uris):
        # Цикл держим в локальной переменной и только потом публикуем в self:
        # если пользователь успел переподключиться, старый поток не должен
        # закрыть цикл нового — тот ещё работает.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.pen = asyncio.Lock()      # очередь на отправку, одна на всех
        self.loop = loop
        try:
            loop.run_until_complete(self._try_all(uris))
        finally:
            loop.close()
            if self.loop is loop:
                self.loop = None

    async def _try_all(self, uris):
        """Пробует адреса по очереди: сначала защищённый, потом обычный."""
        problems = []
        for uri in uris:
            handled, problem = await self._session(uri)
            if handled:
                return
            if problem is not None:
                problems.append(problem)

        if problems:
            # Показываем самую внятную причину, а не последнюю: защищённая
            # попытка обычно уже объяснила, что не так с адресом, а обычная
            # спотыкается о невнятное «сервер ответил не по-человечески»
            problems.sort(key=lambda problem: problem[0])
            self.events.put(("error", problems[0][1]))

    async def _session(self, uri):
        """Одна попытка. Возвращает (достучались ли, причину отказа).

        Причина — пара «насколько понятная, текст»: из нескольких неудач
        показываем ту, что объясняет человеку больше.
        """
        connection = None
        secure = uri.startswith("wss://")
        try:
            async with websockets.connect(uri, max_size=protocol.MAX_FRAME_SIZE) as websocket:
                connection = websocket
                self.websocket = websocket
                self.events.put(("opened", secure))

                while True:
                    message = protocol.decode(await websocket.recv())
                    if message is None:
                        continue
                    if message.get("type") in ("blob", "update_blob"):
                        # За описанием идут кадры с содержимым: большое
                        # вложение приезжает не одним куском
                        куски = []
                        сколько = max(1, int(message.get("parts") or 1))
                        while len(куски) < сколько:
                            кадр = await websocket.recv()
                            if isinstance(кадр, (bytes, bytearray)):
                                куски.append(кадр)
                                continue

                            # Между кусками вложения может влезть обычный
                            # кадр — например, история переписки. Считать
                            # его куском нельзя: так пропадала вся лента,
                            # и переписка оставалась пустой.
                            другое = protocol.decode(кадр)
                            if другое is not None:
                                self.events.put(("message", другое))
                        message["data"] = b"".join(куски)
                    self.events.put(("message", message))

        except ConnectionRefusedError:
            return False, (0, t("Сервер недоступен. Проверьте, запущен ли он."))
        except ssl.SSLCertVerificationError:
            return False, (0, t("Сертификат сервера выписан на другое имя. "
                                "Проверьте, правильно ли введён адрес."))
        except ssl.SSLError:
            return False, (1, t("Сервер не принял защищённое соединение."))
        except socket.gaierror:
            return False, (0, t("Не удалось найти сервер по этому адресу."))
        except OSError as error:
            return False, (2, t("Не удалось подключиться: {error}", error=error))
        except websockets.exceptions.ConnectionClosed:
            pass
        except websockets.exceptions.InvalidStatus as error:
            # Сервер может пускать только по определённому имени и отвечать 403
            if error.response.status_code == 403:
                self.events.put(("error", t("Сервер не принимает подключение по этому "
                                            "адресу. Проверьте, что он введён точно.")))
            else:
                self.events.put(("error", t("Сервер ответил кодом {code}.",
                                            code=error.response.status_code)))
            return True, None
        except websockets.exceptions.InvalidMessage:
            return False, (1, t("По этому адресу отвечает не Velix. "
                                "Проверьте адрес и порт."))
        except websockets.exceptions.WebSocketException as error:
            return False, (2, t("Ошибка соединения: {error}", error=error))
        finally:
            # Сбрасываем только своё подключение, чужое не трогаем
            if self.websocket is connection:
                self.websocket = None

        self.events.put(("disconnected", None))
        return True, None


class VelixApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Velix")
        self.geometry("1040x680")
        self.minsize(880, 560)
        self.configure(fg_color=CHAT_BG)

        self.events = queue.Queue()
        self.network = Network(self.events)
        self.config_data = store.load()
        self.settings = dict(DEFAULT_SETTINGS, **self.config_data.get("settings", {}))
        # Язык и тему поднимаем из настроек до того, как построим экраны
        i18n.set_language(self.settings.get("language", i18n.DEFAULT))
        ctk.set_appearance_mode(self.settings.get("theme", "dark"))

        self.tray = tray_module.Tray(on_open=lambda: self.events.put(("tray_open", None)),
                                     on_quit=lambda: self.events.put(("tray_quit", None)),
                                     icon_path=str(resource_path("icon.ico")))
        self.hidden_notice_shown = False
        self.available_update = None   # что сервер предлагает поставить
        self.secure = False            # идёт ли соединение по TLS

        self.conversation = None       # какая переписка открыта
        self.conversations = []        # что показывать в списке слева
        self.people = []               # все участники
        self.online = set()            # кто сейчас в сети
        self.seen = {}                 # кто когда был в сети последний раз
        self.reply_to = None           # на какое сообщение отвечаем
        self.editing = None            # какое своё сообщение правим
        self.drafts = {}               # недописанное по переписке
        self.outbox = []               # написанное, пока не было связи
        self.from_cache = False        # показываем сохранённое, связи ещё нет
        self.cache_pending = False     # сохранение ленты уже назначено
        self.quotes = {}               # выжимки цитируемых сообщений
        self.rows = {}                 # номер сообщения -> его ряд в ленте
        self.oldest = None             # самое старое загруженное сообщение
        self.has_older = False         # есть ли что подгружать выше
        self.typing_until = 0          # до какого времени показывать «печатает»
        self.typing_who = None         # кто именно печатает
        self.pending_direct = False    # ждём номер только что созданной личной
        self.reactions = {}            # номер сообщения -> {смайлик: [кто]}
        self.reaction_rows = {}        # где рисовать реакции у сообщения
        self.loaded_items = []         # что сейчас показано в ленте
        self.ticks = {}                # номер сообщения -> надпись с галочками
        self.states = {}               # номер сообщения -> sent/delivered/read
        self.local_number = 0          # свои сообщения до ответа сервера
        self.pending_group = False     # ждём номер только что созданной группы
        self.kept_media = {}           # содержимое картинок для копирования
        self.viewer = None             # открытый просмотр картинки
        self.gallery = []              # что можно листать в этой переписке
        self.viewer_items = []         # что листаем в открытом просмотре
        self.viewer_at = 0             # какое вложение сейчас на экране
        self.viewer_stage = None       # где рисуется само вложение
        self.viewer_counter = None     # «3 / 12» в углу просмотра
        self.video = None              # проигрыватель, если открыт ролик
        self.recording = None          # идущая запись голоса или кружочка
        self.recording_job = None      # отсчёт секунд у этой записи
        self.voices = {}               # вложение -> проигрыватель голоса
        self.circles = {}              # вложение -> проигрыватель кружочка
        self.glides = {}               # какие списки сейчас доезжают
        self.drawing_history = False   # рисуем ленту целиком, не по одному
        self.typing_dots = None        # мигание точек в «печатает…»
        self.menu = None               # открытое меню сообщения
        self.pinned = {}               # переписка -> закреплённое сообщение
        self.unread = {}               # переписка -> сколько пришло без нас
        self.stats = None              # последняя сводка для панели
        self.zoom = None               # приближение открытой фотографии
        self.was_open = None           # где человек был до обрыва связи
        self.waiting_for = None        # чью историю ждём прямо сейчас
        self.open_token = 0            # какой заход в переписку нынешний
        self.fetch_queue = []          # вложения, которые ждут своей очереди
        self.fetch_flight = set()      # вложения, о которых уже спросили
        self.retry_at = 0              # какая по счёту попытка вернуться
        self.retry_job = None          # отложенная попытка, чтобы отменить
        self.limits = {}               # пределы вложений, их называет сервер
        self.sending = {}              # что сейчас уходит на сервер
        self.is_admin = False          # хозяин чата: решает сервер

        # Файл, оставшийся от прошлого обновления, больше не нужен
        if updates.running_as_exe():
            updates.cleanup()

        self.server = ""
        self.user = {}
        self.token = None
        self.pending_login = None   # чем входим, когда соединение откроется

        self.wrap_length = 420
        self.last_sender = None
        self.current_date = None
        self.empty_hint = None

        # Вложения, содержимое которых мы ждём от сервера
        self.pending_media = {}
        # Аватарки: готовые картинки и виджеты, которые их ждут
        self.avatar_cache = {}
        self.avatar_waiters = {}
        # Ссылки на картинки: без них Tkinter выбрасывает их сборщиком мусора
        self.images = []
        self.animations = {}

        self.font_title = ctk.CTkFont(family="Segoe UI Semibold", size=26)
        self.font_name = ctk.CTkFont(family="Segoe UI Semibold", size=14)
        self.font_sender = ctk.CTkFont(family="Segoe UI Semibold", size=13)
        self.font_body = ctk.CTkFont(family="Segoe UI", size=14)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=11)
        self.font_avatar = ctk.CTkFont(family="Segoe UI Semibold", size=16)
        self.font_big_avatar = ctk.CTkFont(family="Segoe UI Semibold", size=34)
        self.font_button = ctk.CTkFont(family="Segoe UI Semibold", size=14)

        self._apply_icon()
        self._build_auth_view()
        self._build_chat_view()
        self._build_profile_view()
        self._build_settings_view()
        self._show_auth()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_resize)
        self.after(60, self._pump_events)
        self.after(60000, self._keep_subtitle_fresh)

    def _apply_icon(self):
        """Ставит иконку окна, если файл на месте."""
        icon = resource_path("icon.ico")
        if not icon.exists():
            return
        self.iconbitmap(str(icon))
        # CustomTkinter возвращает свою иконку через пару сотен миллисекунд
        # после создания окна, поэтому ставим ещё раз следом за ним
        self.after(300, lambda: self.iconbitmap(str(icon)))

    # ---------------------------------------------------------- экран входа

    def _build_auth_view(self):
        self.auth_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.auth_view, fg_color=SIDEBAR, corner_radius=R_SHEET)
        card.place(relx=0.5, rely=0.5, anchor="center")
        self.auth_card = card

        ctk.CTkLabel(card, text="V", font=self.font_big_avatar,
                     text_color=ON_ACCENT, fg_color=ACCENT, corner_radius=40,
                     width=80, height=80).pack(padx=48, pady=(36, 16))

        ctk.CTkLabel(card, text="Velix", font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(0, 4))
        self.auth_subtitle = ctk.CTkLabel(card, text="", font=self.font_small,
                                          text_color=MUTED)
        self.auth_subtitle.pack(padx=48, pady=(0, 20))

        # Список сохранённых аккаунтов
        self.saved_box = ctk.CTkFrame(card, fg_color="transparent")
        self.saved_box.pack(padx=48, fill="x")

        # Форма входа
        self.form = ctk.CTkFrame(card, fg_color="transparent")
        self.form.pack(padx=48, fill="x")

        self.server_entry = self._entry(self.form, t("Адрес сервера"))
        self.login_entry = self._entry(self.form, t("Логин"))
        self.password_entry = self._entry(self.form, t("Пароль"), show="•")
        self.name_entry = self._entry(self.form, t("Как вас зовут"))
        self.invite_entry = self._entry(self.form, t("Код приглашения"))
        self.code_entry = self._entry(self.form, t("Код восстановления"))

        self.primary_button = ctk.CTkButton(
            self.form, text=t("ВОЙТИ"), width=300, height=46, corner_radius=R_ITEM,
            font=self.font_button, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ON_ACCENT, command=self._on_primary)

        self.switch_button = ctk.CTkButton(
            self.form, text=t("Создать аккаунт"), width=300, height=32,
            corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
            hover_color=INPUT_BG, text_color=ACCENT, command=self._toggle_mode)

        self.forgot_button = ctk.CTkButton(
            self.form, text=t("Забыли пароль?"), width=300, height=28,
            corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
            hover_color=INPUT_BG, text_color=MUTED,
            command=lambda: self._show_form(recover=True))

        self.back_button = ctk.CTkButton(
            card, text=t("К списку аккаунтов"), width=300, height=32,
            corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
            hover_color=INPUT_BG, text_color=MUTED,
            command=lambda: self._show_auth())

        self.auth_error = ctk.CTkLabel(card, text="", font=self.font_small,
                                       text_color=OFFLINE, wraplength=300)
        self.auth_error.pack(padx=48, pady=(8, 24))

        for entry in (self.server_entry, self.login_entry, self.password_entry,
                      self.name_entry, self.invite_entry):
            entry.bind("<Return>", lambda event: self._on_primary())
            entry.bind("<Control-KeyPress>", self._on_entry_shortcut)

        self.register_mode = False
        self.recover_mode = False

    def _entry(self, master, placeholder, show=None):
        entry = ctk.CTkEntry(
            master, placeholder_text=placeholder, width=300, height=46,
            corner_radius=R_ITEM, border_width=1, border_color=SEPARATOR,
            fg_color=INPUT_BG, text_color=TEXT, placeholder_text_color=MUTED,
            font=self.font_body)
        if show:
            entry.configure(show=show)
        return entry

    def _show_auth(self):
        """Список сохранённых аккаунтов, если они есть, иначе сразу форма."""
        self.chat_view.pack_forget()
        self.profile_view.pack_forget()
        self.settings_view.pack_forget()
        self.auth_view.pack(fill="both", expand=True)

        for widget in self.saved_box.winfo_children():
            widget.destroy()

        accounts = self.config_data.get("accounts", [])
        if not accounts:
            self._show_form(register=False)
            return

        self.form.pack_forget()
        self.back_button.pack_forget()
        # before= обязателен: повторный pack иначе отправляет рамку в конец
        # очереди, ниже пустой строки ошибки, и в карточке зияет дыра
        self.saved_box.pack(padx=48, fill="x", before=self.auth_error)
        self.auth_subtitle.configure(text=t("Выберите аккаунт"))

        for account in accounts[:6]:
            self._account_row(account)

        ctk.CTkButton(self.saved_box, text=t("Войти в другой аккаунт"), width=300,
                      height=38, corner_radius=R_ITEM, font=self.font_small,
                      fg_color="transparent", hover_color=INPUT_BG,
                      text_color=ACCENT,
                      command=lambda: self._show_form(register=False)).pack(pady=(6, 0))

    def _account_row(self, account):
        row = ctk.CTkFrame(self.saved_box, fg_color=INPUT_BG, corner_radius=R_ITEM,
                           height=58)
        row.pack(fill="x", pady=4)
        row.pack_propagate(False)

        name = account.get("name") or account.get("login", "?")
        ctk.CTkLabel(row, text=name[0].upper(), font=self.font_avatar,
                     text_color=ON_ACCENT, fg_color=avatar_color(name),
                     corner_radius=20, width=40, height=40).pack(
            side="left", padx=(9, 10), pady=9)

        lines = ctk.CTkFrame(row, fg_color="transparent")
        lines.pack(side="left", fill="both", expand=True, pady=10)
        ctk.CTkLabel(lines, text=name, font=self.font_name, text_color=TEXT,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(lines, text=f"{account.get('login')} · {account.get('server')}",
                     font=self.font_small, text_color=MUTED, anchor="w").pack(fill="x")

        ctk.CTkButton(row, text="✕", width=28, height=28, corner_radius=R_SMALL,
                      font=self.font_small, fg_color="transparent",
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=lambda: self._forget(account)).pack(side="right", padx=(0, 8))

        for widget in (row, lines):
            widget.bind("<Button-1>", lambda event, item=account: self._enter_saved(item))
        for child in lines.winfo_children():
            child.bind("<Button-1>", lambda event, item=account: self._enter_saved(item))

    def _show_form(self, register=False, recover=False):
        """Показывает форму входа, регистрации или восстановления."""
        self.register_mode = register
        self.recover_mode = recover
        self.saved_box.pack_forget()
        self.form.pack(padx=48, fill="x", before=self.auth_error)

        for entry in (self.server_entry, self.login_entry, self.password_entry,
                      self.name_entry, self.invite_entry, self.code_entry):
            entry.pack_forget()
        self.primary_button.pack_forget()
        self.switch_button.pack_forget()
        self.forgot_button.pack_forget()

        self.server_entry.pack(pady=(0, 10))
        self.login_entry.pack(pady=(0, 10))
        if recover:
            self.code_entry.pack(pady=(0, 10))
        self.password_entry.pack(pady=(0, 10))
        if register:
            self.name_entry.pack(pady=(0, 10))
            self.invite_entry.pack(pady=(0, 10))

        # В восстановлении просят не старый пароль, а новый
        self.password_entry.configure(
            placeholder_text=t("Новый пароль") if recover else t("Пароль"))

        if recover:
            self.primary_button.configure(text=t("СМЕНИТЬ ПАРОЛЬ"))
        else:
            self.primary_button.configure(
                text=t("СОЗДАТЬ АККАУНТ") if register else t("ВОЙТИ"))
        self.primary_button.pack(pady=(6, 6))

        if recover:
            self.switch_button.configure(text=t("Вернуться ко входу"),
                                         command=lambda: self._show_form())
        else:
            self.switch_button.configure(
                text=t("У меня уже есть аккаунт") if register
                else t("Создать аккаунт"), command=self._toggle_mode)
        self.switch_button.pack()

        if not register and not recover:
            self.forgot_button.pack(pady=(2, 0))

        if recover:
            subtitle = t("Восстановление пароля")
        elif register:
            subtitle = t("Нужен код приглашения")
        else:
            subtitle = t("Вход в аккаунт")
        self.auth_subtitle.configure(text=subtitle)
        self.auth_error.configure(text="")

        if self.config_data.get("accounts"):
            self.back_button.pack(padx=48, pady=(10, 0), before=self.auth_error)
        else:
            self.back_button.pack_forget()

        if not self.server_entry.get():
            last = self.config_data.get("accounts")
            if last:
                self.server_entry.insert(0, last[0].get("server", ""))
        self.login_entry.focus_set()

    def _toggle_mode(self):
        self._show_form(register=not self.register_mode)

    def _forget(self, account):
        store.forget_account(self.config_data, account)
        store.save(self.config_data)
        self._show_auth()

    # -------------------------------------------------------------- экраны

    def _build_chat_view(self):
        self.chat_view = ctk.CTkFrame(self, fg_color="transparent")
        self.chat_view.grid_rowconfigure(0, weight=1)
        self.chat_view.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_conversation()

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self.chat_view, fg_color=SIDEBAR, corner_radius=0,
                               width=292)
        sidebar.grid(row=0, column=0, sticky="nsew")
        # Внутри панели всё разложено через pack, поэтому и запрет на
        # подстройку размера под детей нужен именно pack_propagate —
        # grid_propagate тут не сработает, и панель схлопнется по содержимому.
        sidebar.pack_propagate(False)

        # --- своя карточка: аватарка, имя, вход в профиль
        me = ctk.CTkFrame(sidebar, fg_color="transparent")
        me.pack(fill="x", padx=14, pady=(14, 8))

        self.my_avatar = ctk.CTkLabel(me, text="?", font=self.font_avatar,
                                      text_color=ON_ACCENT, fg_color=ACCENT,
                                      corner_radius=20, width=40, height=40)
        self.my_avatar.pack(side="left", padx=(0, 10))

        names = ctk.CTkFrame(me, fg_color="transparent")
        names.pack(side="left", fill="both", expand=True)
        self.my_name = ctk.CTkLabel(names, text="", font=self.font_name,
                                    text_color=TEXT, anchor="w")
        self.my_name.pack(fill="x")
        self.my_login = ctk.CTkLabel(names, text="", font=self.font_small,
                                     text_color=MUTED, anchor="w")
        self.my_login.pack(fill="x")

        buttons = ctk.CTkFrame(sidebar, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(0, 10))

        self.profile_button = ctk.CTkButton(
            buttons, text=t("Профиль"), width=70, height=30, corner_radius=R_SMALL,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._show_profile)
        self.profile_button.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.settings_button = ctk.CTkButton(
            buttons, text=t("Настройки"), width=70, height=30, corner_radius=R_SMALL,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._show_settings)
        self.settings_button.pack(side="left", expand=True, fill="x", padx=4)

        self.leave_button = ctk.CTkButton(
            buttons, text=t("Сменить"), width=70, height=30, corner_radius=R_SMALL,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._on_leave)
        self.leave_button.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.search_entry = ctk.CTkEntry(
            sidebar, placeholder_text=t("Поиск: @username или слово"), height=34,
            corner_radius=R_ITEM, border_width=0, fg_color=INPUT_BG, text_color=TEXT,
            placeholder_text_color=MUTED, font=self.font_small)
        self.search_entry.pack(fill="x", padx=14, pady=(0, 8))

        ctk.CTkButton(sidebar, text="＋ " + t("Новая группа"), height=30,
                      corner_radius=R_SMALL, font=self.font_small, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=self._new_group).pack(fill="x", padx=14, pady=(0, 8))
        self.search_entry.bind("<Return>", lambda event: self._on_search())
        # Людей показываем по мере набора: искать по списку глазами незачем
        self.search_entry.bind("<KeyRelease>", self._on_search_typing)
        self.search_entry.bind("<Control-KeyPress>", self._on_entry_shortcut)

        # Список переписок и участников: и то и другое живёт в одной колонке
        self.side_list = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=SEPARATOR, scrollbar_button_hover_color=MUTED)
        self.side_list.pack(fill="both", expand=True, padx=4)

    def _build_conversation(self):
        main = ctk.CTkFrame(self.chat_view, fg_color=CHAT_BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        # Растягивается только лента: шапка, закреплённое, полоска ответа
        # и поле ввода занимают ровно столько, сколько им нужно
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0, height=62)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        self.header_avatar = ctk.CTkLabel(header, text="V", font=self.font_sender,
                                          text_color=ON_ACCENT,
                                          fg_color=avatar_color("Velix"),
                                          corner_radius=20, width=40, height=40)
        self.header_avatar.grid(row=0, column=0, padx=(18, 12), pady=11)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w")
        self.header_title = ctk.CTkLabel(titles, text=t("Общий чат"), font=self.font_name,
                                         text_color=TEXT)
        self.header_title.pack(anchor="w")
        self.header_subtitle = ctk.CTkLabel(titles, text="", font=self.font_small,
                                            text_color=MUTED)
        self.header_subtitle.pack(anchor="w")

        # Вложения переписки: их ищут не листанием вверх, а вот этой кнопкой
        self.gallery_button = ctk.CTkButton(
            header, text=t("Медиа"), width=72, height=30, corner_radius=R_ITEM,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=TEXT, command=self._ask_gallery)
        self.gallery_button.grid(row=0, column=2, padx=(0, 12))

        self.status_dot = ctk.CTkLabel(header, text="●", font=self.font_small,
                                       text_color=ONLINE, width=14)
        self.status_dot.grid(row=0, column=3, padx=(0, 20))

        # Полоска с закреплённым сообщением: появляется, когда есть что показать
        self.pin_bar = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0,
                                    height=44)
        self.pin_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.pin_bar, text="📌", font=self.font_small,
                     width=24).grid(row=0, column=0, padx=(18, 6), pady=8)
        self.pin_label = ctk.CTkLabel(self.pin_bar, text="", font=self.font_small,
                                      text_color=MUTED, anchor="w")
        self.pin_label.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(self.pin_bar, text="✕", width=28, height=24, corner_radius=R_SMALL,
                      font=self.font_small, fg_color="transparent",
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=self._unpin).grid(row=0, column=2, padx=(6, 14))

        self.messages = ctk.CTkScrollableFrame(
            main, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=SEPARATOR, scrollbar_button_hover_color=MUTED)
        self.messages.grid(row=2, column=0, sticky="nsew")

        # Заменяем общую привязку колеса на свою: CustomTkinter вешает её
        # на всё окно сразу, поэтому и снимаем её так же
        self.bind_all("<MouseWheel>", self._on_wheel)

        # Полоска «отвечаем на …» появляется над строкой ввода
        self.reply_bar = ctk.CTkFrame(main, fg_color=INPUT_BG, corner_radius=0)
        self.reply_label = ctk.CTkLabel(self.reply_bar, text="", font=self.font_small,
                                        text_color=MUTED, anchor="w")
        self.reply_label.pack(side="left", padx=(18, 8), pady=6)
        ctk.CTkButton(self.reply_bar, text="✕", width=28, height=24, corner_radius=R_SMALL,
                      font=self.font_small, fg_color="transparent",
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=self._cancel_reply).pack(side="right", padx=(0, 18))

        composer = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0)
        composer.grid(row=4, column=0, sticky="ew")
        composer.grid_columnconfigure(1, weight=1)
        self.composer = composer

        self.attach_button = ctk.CTkButton(
            composer, text="+", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=22), fg_color=INPUT_BG,
            hover_color=SEPARATOR, text_color=MUTED, command=self._on_attach)
        self.attach_button.grid(row=0, column=0, padx=(18, 8), pady=13)

        self.message_entry = ctk.CTkEntry(
            composer, placeholder_text=t("Написать сообщение…"), height=44,
            corner_radius=22, border_width=0, fg_color=INPUT_BG, text_color=TEXT,
            placeholder_text_color=MUTED, font=self.font_body)
        self.message_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=13)
        self.message_entry.bind("<Return>", lambda event: self._on_send())
        # Ctrl+V ловим по коду клавиши, а не по букве: при русской раскладке
        # событие <Control-v> просто не приходит
        self.message_entry.bind("<Control-KeyPress>", self._on_ctrl_key)
        self.message_entry.bind("<KeyRelease>", self._notify_typing)

        # Микрофон и кружочек. Если записывать нечем — например, сборка без
        # ffmpeg или в системе нет ни одного микрофона, — кнопок просто нет:
        # кнопка, которая всегда отвечает «не могу», хуже её отсутствия
        self.voice_button = ctk.CTkButton(
            composer, text="🎤", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=17), fg_color=INPUT_BG,
            hover_color=SEPARATOR, text_color=MUTED,
            command=lambda: self._start_recording("voice"))
        self.circle_button = ctk.CTkButton(
            composer, text="◉", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=19), fg_color=INPUT_BG,
            hover_color=SEPARATOR, text_color=MUTED,
            command=lambda: self._start_recording("circle"))
        self._place_record_buttons()

        self.send_button = ctk.CTkButton(
            composer, text="➤", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=16), fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self._on_send)
        self.send_button.grid(row=0, column=4, padx=(0, 18), pady=13)

        # Полоска записи встаёт на место композера: пока идёт запись, писать
        # всё равно нечего, а два ряда подряд только мешались бы
        self.record_bar = ctk.CTkFrame(main, fg_color=COMPOSER,
                                       corner_radius=0)
        self.record_dot = ctk.CTkLabel(self.record_bar, text="●",
                                       font=ctk.CTkFont(family="Segoe UI", size=18),
                                       text_color=OFFLINE)
        self.record_dot.pack(side="left", padx=(20, 8), pady=13)
        self.record_label = ctk.CTkLabel(self.record_bar, text="",
                                         font=self.font_body, text_color=TEXT,
                                         anchor="w")
        self.record_label.pack(side="left")
        ctk.CTkButton(self.record_bar, text="➤", width=44, height=44,
                      corner_radius=22, font=ctk.CTkFont(family="Segoe UI", size=16),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ON_ACCENT,
                      command=self._finish_recording).pack(side="right",
                                                           padx=(0, 18), pady=13)
        ctk.CTkButton(self.record_bar, text="✕", width=44, height=44,
                      corner_radius=22, font=ctk.CTkFont(family="Segoe UI", size=16),
                      fg_color=INPUT_BG, hover_color=SEPARATOR, text_color=MUTED,
                      command=self._cancel_recording).pack(side="right", padx=(0, 8))

    def _build_profile_view(self):
        self.profile_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.profile_view, fg_color=SIDEBAR, corner_radius=R_SHEET)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text=t("Профиль"), font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(32, 18))

        self.profile_avatar = ctk.CTkLabel(
            card, text="?", font=self.font_big_avatar, text_color=ON_ACCENT,
            fg_color=ACCENT, corner_radius=AVATAR_LARGE // 2,
            width=AVATAR_LARGE, height=AVATAR_LARGE)
        self.profile_avatar.pack(padx=48)
        self.profile_avatar.bind("<Button-1>", lambda event: self._choose_avatar())

        ctk.CTkButton(card, text=t("Сменить фото"), width=300, height=32,
                      corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=ACCENT,
                      command=self._choose_avatar).pack(padx=48, pady=(8, 16))

        self.profile_name = self._entry(card, t("Как вас зовут"))
        self.profile_name.pack(padx=48, pady=(0, 10))
        self.profile_name.bind("<Control-KeyPress>", self._on_entry_shortcut)

        self.profile_bio = ctk.CTkTextbox(
            card, width=300, height=90, corner_radius=R_ITEM, border_width=1,
            border_color=SEPARATOR, fg_color=INPUT_BG, text_color=TEXT,
            font=self.font_body, wrap="word")
        self.profile_bio.pack(padx=48, pady=(0, 4))

        self.profile_hint = ctk.CTkLabel(card, text=t("Пара слов о себе"),
                                         font=self.font_small, text_color=MUTED)
        self.profile_hint.pack(padx=48, pady=(0, 14))

        ctk.CTkButton(card, text=t("СОХРАНИТЬ"), width=300, height=46,
                      corner_radius=R_ITEM, font=self.font_button, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                      command=self._save_profile).pack(padx=48)

        ctk.CTkButton(card, text=t("Назад в чат"), width=300, height=32,
                      corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=MUTED,
                      command=self._show_chat).pack(padx=48, pady=(8, 30))

    def _build_settings_view(self):
        self.settings_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.settings_view, fg_color=SIDEBAR, corner_radius=R_SHEET)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text=t("Настройки"), font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(32, 22))

        language_row = ctk.CTkFrame(card, fg_color="transparent", width=300)
        language_row.pack(padx=48, pady=(0, 14), fill="x")
        ctk.CTkLabel(language_row, text=t("Язык"), font=self.font_body,
                     text_color=TEXT).pack(side="left")
        self.language_picker = ctk.CTkSegmentedButton(
            language_row, values=[i18n.NAMES[code] for code in i18n.LANGUAGES],
            font=self.font_small, height=28, corner_radius=R_SMALL,
            fg_color=INPUT_BG, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, unselected_color=INPUT_BG,
            unselected_hover_color=SEPARATOR, text_color=TEXT,
            command=self._on_language)
        self.language_picker.set(i18n.NAMES[i18n.language()])
        self.language_picker.pack(side="right")

        self.theme_switch = self._switch(card, t("Тёмное оформление"),
                                         self._on_theme_switch)
        self.tray_switch = self._switch(card, t("Прятать в трей при закрытии"),
                                        self._on_tray_switch)
        self.autostart_switch = self._switch(card, t("Запускать вместе с Windows"),
                                             self._on_autostart_switch)
        self.sound_switch = self._switch(card, t("Звук о новом сообщении"),
                                         self._on_sound_switch)

        # Микрофонов на машине бывает три, и первый попавшийся — обычно не
        # тот. Спрашиваем один раз и запоминаем
        self.microphone_picker = self._device_picker(
            card, t("Микрофон"), recorder.microphones(),
            self.settings.get("microphone"), self._on_microphone)
        self.camera_picker = self._device_picker(
            card, t("Камера"), recorder.cameras(),
            self.settings.get("camera"), self._on_camera)

        self.settings_hint = ctk.CTkLabel(card, text="", font=self.font_small,
                                          text_color=MUTED, wraplength=300,
                                          justify="left")
        self.settings_hint.pack(padx=48, pady=(6, 12))

        ctk.CTkFrame(card, height=2, corner_radius=0, fg_color=SEPARATOR).pack(
            fill="x", padx=48, pady=(0, 12))

        self.version_label = ctk.CTkLabel(card, text=t("Версия {version}", version=version.VERSION),
                                          font=self.font_small, text_color=MUTED)
        self.version_label.pack(padx=48)

        self.update_button = ctk.CTkButton(
            card, text=t("Обновлений нет"), width=300, height=38, corner_radius=R_ITEM,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, state="disabled", command=self._on_update)
        self.update_button.pack(padx=48, pady=(8, 4))

        self.admin_button = ctk.CTkButton(
            card, text=t("Панель управления"), width=300, height=38,
            corner_radius=R_ITEM, font=self.font_small, fg_color=INPUT_BG,
            hover_color=SEPARATOR, text_color=TEXT, command=self._show_admin)

        ctk.CTkButton(card, text=t("Назад в чат"), width=300, height=32,
                      corner_radius=R_SMALL, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=MUTED,
                      command=self._show_chat).pack(padx=48, pady=(0, 30))

    def _device_picker(self, master, caption, устройства, выбранное, command):
        """Выпадающий список устройств. Пусто — строчки просто нет."""
        if not устройства:
            return None

        имена = [одно["name"] for одно in устройства]
        row = ctk.CTkFrame(master, fg_color="transparent", width=300)
        row.pack(padx=48, pady=(0, 14), fill="x")
        ctk.CTkLabel(row, text=caption, font=self.font_body,
                     text_color=TEXT).pack(side="left")
        picker = ctk.CTkOptionMenu(
            row, values=имена, width=190, height=28, corner_radius=R_SMALL,
            font=self.font_small, dropdown_font=self.font_small,
            fg_color=INPUT_BG, button_color=INPUT_BG,
            button_hover_color=SEPARATOR, text_color=TEXT,
            dropdown_fg_color=MENU_BG, dropdown_hover_color=MENU_HOVER,
            dropdown_text_color=TEXT, command=command)

        сейчас = next((одно["name"] for одно in устройства
                       if одно["id"] == выбранное or одно["name"] == выбранное),
                      имена[0])
        picker.set(сейчас)
        picker.pack(side="right")
        return picker

    def _on_microphone(self, имя):
        """Запоминаем выбранный микрофон по системному имени, не по показанному."""
        for одно in recorder.microphones():
            if одно["name"] == имя:
                self.settings["microphone"] = одно["id"]
                break
        self._save_settings()

    def _on_camera(self, имя):
        for одно in recorder.cameras():
            if одно["name"] == имя:
                self.settings["camera"] = одно["id"]
                break
        self._save_settings()

    def _switch(self, master, text, command):
        row = ctk.CTkFrame(master, fg_color="transparent", width=300)
        row.pack(padx=48, pady=(0, 14), fill="x")
        switch = ctk.CTkSwitch(row, text=text, font=self.font_body, text_color=TEXT,
                               progress_color=ACCENT, command=command)
        switch.pack(anchor="w")
        return switch

    def _show_settings(self):
        self.auth_view.pack_forget()
        self.chat_view.pack_forget()
        self.profile_view.pack_forget()
        self.settings_view.pack(fill="both", expand=True)

        self._set_switch(self.theme_switch,
                         self.settings.get("theme", "dark") == "dark")
        self._set_switch(self.tray_switch, self.settings.get("tray", True))
        self._set_switch(self.sound_switch, self.settings.get("sound", True))
        # Спрашиваем реестр, а не свою память: пользователь мог убрать
        # автозапуск и мимо нас
        self._set_switch(self.autostart_switch, autostart.is_enabled())

        # Панель показываем только хозяину чата: кто это — сказал сервер
        # в приветствии, сам клиент такое решать не должен
        if getattr(self, "is_admin", False):
            self.admin_button.pack(padx=48, pady=(4, 8))
        else:
            self.admin_button.pack_forget()

        if not autostart.supported():
            self.autostart_switch.configure(state="disabled")
        if not self.tray.available:
            self.tray_switch.configure(state="disabled")
        if not chime.available():
            # На этой системе играть нечем — переключать нечего
            self.sound_switch.configure(state="disabled")
        self.settings_hint.configure(text=self._settings_hint(), text_color=MUTED)
        self._refresh_update_button()

    def _settings_hint(self):
        if not self.tray.available:
            return t("Значок в трее недоступен: не установлен pystray.")
        if not autostart.supported():
            return t("Автозапуск настраивается только в Windows.")
        return t("Настройки сохраняются сразу.")

    def _set_switch(self, switch, on):
        switch.select() if on else switch.deselect()

    # -------------------------------------------------------- обновление

    def _refresh_update_button(self):
        """Приводит кнопку к тому, что сейчас предлагает сервер."""
        offer = self.available_update
        if not offer or not version.is_newer(offer.get("version")):
            self.update_button.configure(text=t("У вас последняя версия"),
                                         state="disabled", fg_color=INPUT_BG,
                                         text_color=MUTED)
            return

        size = protocol.human_size(offer.get("size") or 0)
        if not updates.running_as_exe():
            self.update_button.configure(
                text=t("Есть версия {version}", version=offer["version"])
                     + t(" — обновитесь через git"),
                state="disabled", fg_color=INPUT_BG, text_color=MUTED)
            return

        self.update_button.configure(
            text=t("Обновить до {version}", version=offer["version"]) + f" · {size}",
                                     state="normal", fg_color=ACCENT,
                                     text_color=ON_ACCENT)

    def _on_update(self):
        """Просит у сервера свежую сборку."""
        if self.network.websocket is None:
            self.settings_hint.configure(text=t("Нет связи с сервером."),
                                         text_color=OFFLINE)
            return
        self.update_button.configure(text=t("Загружаю…"), state="disabled",
                                     fg_color=INPUT_BG, text_color=MUTED)
        self.network.send(protocol.update_request())

    def _install_update(self, message):
        """Пришла новая сборка — подменяем себя и перезапускаемся."""
        data = message.get("data") or b""
        if not data:
            self.settings_hint.configure(text=t("Сервер прислал пустой файл."),
                                         text_color=OFFLINE)
            self._refresh_update_button()
            return

        problem = updates.swap(updates.executable_path(), data)
        if problem:
            self.settings_hint.configure(text=problem, text_color=OFFLINE)
            self._refresh_update_button()
            return

        self.settings_hint.configure(text=t("Обновление установлено, перезапускаюсь…"),
                                     text_color=ONLINE)
        self.update_button.configure(text=t("Перезапуск…"), state="disabled")
        self.update()

        problem = updates.restart()
        if problem:
            self.settings_hint.configure(text=problem, text_color=OFFLINE)
            return
        self._quit()

    def _save_settings(self):
        self.config_data["settings"] = self.settings
        store.save(self.config_data)

    def _on_language(self, choice):
        """Переключает язык интерфейса и пересобирает окно."""
        code = next((item for item in i18n.LANGUAGES
                     if i18n.NAMES[item] == choice), i18n.DEFAULT)
        if code == i18n.language():
            return

        self.settings["language"] = code
        self._save_settings()
        i18n.set_language(code)
        self._rebuild()

    def _rebuild(self):
        """Собирает экраны заново — на новом языке, но с прежним состоянием.

        Так проще и надёжнее, чем помнить, у какой надписи какой ключ:
        сеть живёт отдельно от виджетов, а лента и списки рисуются из
        того, что уже загружено.
        """
        was_chat = self.chat_view.winfo_ismapped()
        was_settings = self.settings_view.winfo_ismapped()
        was_profile = self.profile_view.winfo_ismapped()
        items = self.loaded_items

        for view in (self.auth_view, self.chat_view, self.profile_view,
                     self.settings_view):
            view.destroy()

        # Всё, что помнило старые виджеты, забываем: их больше нет
        self.rows = {}
        self.reaction_rows = {}
        self.avatar_waiters = {}
        self.pending_media = {}
        self.images = []
        self.animations = {}
        self.empty_hint = None
        self.last_sender = None
        self.current_date = None
        self.loaded_items = []
        self.viewer = None

        self._build_auth_view()
        self._build_chat_view()
        self._build_profile_view()
        self._build_settings_view()

        if not (was_chat or was_settings or was_profile):
            self._show_auth()
            return

        self._show_chat()
        self._refresh_me()
        self._refresh_side_list()
        self._update_header()
        self._show_history({"conversation": self.conversation, "items": items,
                            "more": self.has_older})
        if was_settings:
            self._show_settings()
        elif was_profile:
            self._show_profile()

    def _show_admin(self):
        """Панель управления: люди, переписки и место на диске."""
        self.network.send(protocol.admin_request("stats"))

        window = ctk.CTkToplevel(self)
        window.title(t("Панель управления"))
        window.geometry("620x620")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)
        self.admin_window = window

        ctk.CTkLabel(window, text=t("Панель управления"), font=self.font_title,
                     text_color=TEXT).pack(pady=(18, 6))

        self.admin_summary = ctk.CTkLabel(window, text=t("Загружаю…"),
                                          font=self.font_small, text_color=MUTED,
                                          justify="left")
        self.admin_summary.pack(padx=24, anchor="w")

        self.admin_list = ctk.CTkScrollableFrame(window, fg_color="transparent")
        self.admin_list.pack(fill="both", expand=True, padx=16, pady=12)

        if self.stats:
            self._draw_admin(self.stats)

    def _on_admin(self, message):
        """Пришла свежая сводка — перерисовываем панель."""
        self.stats = message.get("stats") or {}
        if getattr(self, "admin_window", None) is not None \
                and self.admin_window.winfo_exists():
            self._draw_admin(self.stats)

    def _draw_admin(self, stats):
        self.admin_summary.configure(text=t(
            "Сообщений: {messages} · вложений: {files} на {media}\n"
            "База: {database} · на диске свободно {free} из {total}",
            messages=stats.get("messages", 0),
            files=stats.get("media_files", 0),
            media=protocol.human_size(stats.get("media_bytes", 0)),
            database=protocol.human_size(stats.get("database_bytes", 0)),
            free=protocol.human_size(stats.get("disk_free", 0)),
            total=protocol.human_size(stats.get("disk_total", 0))))

        for widget in self.admin_list.winfo_children():
            widget.destroy()

        self._admin_limits(stats.get("limits") or {})

        ctk.CTkLabel(self.admin_list, text=t("УЧАСТНИКИ"), font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x", pady=(4, 2))
        for person in stats.get("users", []):
            self._admin_row(
                f"{person['name']} · {person['login']}",
                t("сообщений: {count}", count=person.get("messages", 0)),
                None if person["id"] == self.user.get("id")
                else lambda p=person: self._admin_drop_user(p))

        ctk.CTkLabel(self.admin_list, text=t("ПЕРЕПИСКИ"), font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x", pady=(14, 2))
        for room in stats.get("rooms", []):
            title = room.get("title") or t("Личная переписка")
            self._admin_row(
                f"{title} · {room['kind']}",
                t("людей: {members}, сообщений: {count}",
                  members=room.get("members", 0), count=room.get("messages", 0)),
                lambda r=room: self._admin_drop_room(r))

    def _admin_limits(self, limits):
        """Пределы вложений: сколько позволено весить файлу и видео."""
        ctk.CTkLabel(self.admin_list, text=t("ПРЕДЕЛЫ ВЛОЖЕНИЙ"),
                     font=self.font_small, text_color=MUTED,
                     anchor="w").pack(fill="x", pady=(4, 2))

        карточка = ctk.CTkFrame(self.admin_list, fg_color=INPUT_BG, corner_radius=R_SMALL)
        карточка.pack(fill="x", pady=2)

        self.limit_entries = {}
        for имя, подпись, по_умолчанию in (
                ("file", t("Файлы, МБ"), protocol.DEFAULT_FILE_LIMIT),
                ("video", t("Видео, МБ"), protocol.DEFAULT_VIDEO_LIMIT)):
            строка = ctk.CTkFrame(карточка, fg_color="transparent")
            строка.pack(fill="x", padx=10, pady=6)
            ctk.CTkLabel(строка, text=подпись, font=self.font_body,
                         text_color=TEXT, anchor="w").pack(side="left")

            поле = ctk.CTkEntry(строка, width=110, height=30, corner_radius=R_SMALL,
                                font=self.font_body, fg_color=SIDEBAR,
                                border_width=0, text_color=TEXT)
            поле.insert(0, str(int(limits.get(имя, по_умолчанию)) // (1024 * 1024)))
            поле.pack(side="right")
            self.limit_entries[имя] = поле

        ctk.CTkButton(карточка, text=t("Сохранить пределы"), height=32,
                      corner_radius=R_SMALL, font=self.font_small, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                      command=self._admin_save_limits).pack(fill="x", padx=10,
                                                            pady=(2, 10))

    def _admin_save_limits(self):
        значения = {}
        for имя, поле in self.limit_entries.items():
            try:
                мегабайты = int(поле.get().strip())
            except ValueError:
                continue
            # Меньше мегабайта и больше десяти гигабайт сервер всё равно
            # не примет — незачем и спрашивать
            if 1 <= мегабайты <= 10 * 1024:
                значения[имя] = мегабайты * 1024 * 1024
        if значения:
            self.network.send(protocol.admin_request("limits", **значения))

    def _admin_row(self, title, note, on_delete):
        row = ctk.CTkFrame(self.admin_list, fg_color=INPUT_BG, corner_radius=R_SMALL)
        row.pack(fill="x", pady=2)

        lines = ctk.CTkFrame(row, fg_color="transparent")
        lines.pack(side="left", fill="x", expand=True, padx=10, pady=6)
        ctk.CTkLabel(lines, text=title, font=self.font_body, text_color=TEXT,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(lines, text=note, font=self.font_small, text_color=MUTED,
                     anchor="w").pack(fill="x")

        if on_delete is not None:
            ctk.CTkButton(row, text=t("Удалить"), width=90, height=28,
                          corner_radius=R_SMALL, font=self.font_small, fg_color=SEPARATOR,
                          hover_color=OFFLINE, text_color=TEXT,
                          command=on_delete).pack(side="right", padx=10)

    def _admin_drop_user(self, person):
        if self._confirm(t("Удалить {name}?", name=person["name"]),
                         t("Учётная запись пропадёт, сообщения останутся.")):
            self.network.send(protocol.admin_request("drop_user", user=person["id"]))

    def _admin_drop_room(self, room):
        title = room.get("title") or t("Личная переписка")
        if self._confirm(t("Удалить «{title}»?", title=title),
                         t("Переписка и вложения пропадут у всех. "
                           "Отменить это нельзя.")):
            self.network.send(protocol.admin_request("drop_room",
                                                     conversation=room["id"]))

    def _confirm(self, question, note):
        """Простое «да/нет» своим окном: системного в CustomTkinter нет."""
        window = ctk.CTkToplevel(self)
        window.title(question)
        window.geometry("420x180")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)
        window.grab_set()

        ctk.CTkLabel(window, text=question, font=self.font_name,
                     text_color=TEXT, wraplength=360).pack(pady=(20, 6))
        ctk.CTkLabel(window, text=note, font=self.font_small, text_color=MUTED,
                     wraplength=360).pack()

        answer = {"yes": False}

        def say(value):
            answer["yes"] = value
            window.destroy()

        buttons = ctk.CTkFrame(window, fg_color="transparent")
        buttons.pack(pady=16)
        ctk.CTkButton(buttons, text=t("Удалить"), width=140, height=34,
                      corner_radius=R_SMALL, font=self.font_small, fg_color=OFFLINE,
                      hover_color=SEPARATOR, text_color=TEXT,
                      command=lambda: say(True)).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text=t("Отмена"), width=140, height=34,
                      corner_radius=R_SMALL, font=self.font_small, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=TEXT,
                      command=lambda: say(False)).pack(side="left", padx=6)

        self.wait_window(window)
        return answer["yes"]

    def _on_theme_switch(self):
        theme = "dark" if self.theme_switch.get() else "light"
        self.settings["theme"] = theme
        ctk.set_appearance_mode(theme)
        self._save_settings()

    def _on_sound_switch(self):
        self.settings["sound"] = bool(self.sound_switch.get())
        self._save_settings()
        if self.settings["sound"]:
            chime.play()        # сразу слышно, о чём речь

    def _on_tray_switch(self):
        self.settings["tray"] = bool(self.tray_switch.get())
        self._save_settings()
        if not self.settings["tray"]:
            self.tray.hide()

    def _on_autostart_switch(self):
        wanted = bool(self.autostart_switch.get())
        problem = autostart.apply(wanted)
        if problem:
            self._set_switch(self.autostart_switch, autostart.is_enabled())
            self.settings_hint.configure(text=problem, text_color=OFFLINE)
            return
        self.settings["autostart"] = wanted
        self._save_settings()
        self.settings_hint.configure(
            text=t("Velix будет запускаться при входе в Windows.") if wanted
            else t("Автозапуск выключен."), text_color=MUTED)

    def _show_chat(self):
        self.auth_view.pack_forget()
        self.profile_view.pack_forget()
        self.settings_view.pack_forget()
        self.chat_view.pack(fill="both", expand=True)
        self.message_entry.focus_set()

    def _show_profile(self):
        self.auth_view.pack_forget()
        self.chat_view.pack_forget()
        self.settings_view.pack_forget()
        self.profile_view.pack(fill="both", expand=True)

        self.profile_name.delete(0, "end")
        self.profile_name.insert(0, self.user.get("name", ""))
        self.profile_bio.delete("1.0", "end")
        self.profile_bio.insert("1.0", self.user.get("bio", ""))
        self._paint_avatar(self.profile_avatar, self.user.get("name", "?"),
                           self.user.get("avatar"), AVATAR_LARGE)
        self.profile_hint.configure(text=t("Пара слов о себе"), text_color=MUTED)

    # ------------------------------------------------------------ действия

    def _on_primary(self):
        """Кнопка «Войти» или «Создать аккаунт»."""
        server = self.server_entry.get().strip() or "localhost"
        login = self.login_entry.get().strip()
        password = self.password_entry.get()

        if self.recover_mode:
            code = self.code_entry.get().strip()
            if not login or not code or not password:
                self.auth_error.configure(
                    text=t("Заполните логин, код и новый пароль."))
                return
            self.pending_login = protocol.recover_request(login, code, password)
            self.server = server
            self.login = login
            self.auth_error.configure(text="")
            self.primary_button.configure(text=t("ПОДКЛЮЧЕНИЕ…"), state="disabled")
            self.network.connect(connection_uris(server))
            return

        if not login or not password:
            self.auth_error.configure(text=t("Заполните логин и пароль."))
            return

        if self.register_mode:
            name = self.name_entry.get().strip() or login
            invite = self.invite_entry.get().strip()
            self.pending_login = protocol.register_message(login, password, name, invite)
        else:
            self.pending_login = protocol.login_message(login, password)

        self.server = server
        self.login = login
        self.auth_error.configure(text="")
        self.primary_button.configure(text=t("ПОДКЛЮЧЕНИЕ…"), state="disabled")
        self.network.connect(connection_uris(server))

    def _enter_saved(self, account):
        """Вход по сохранённому токену, без пароля."""
        self.server = account.get("server", "localhost")
        self.login = account.get("login", "")
        self.pending_login = protocol.auth_message(account.get("token", ""))
        self.auth_error.configure(text="")
        self.auth_subtitle.configure(
            text=t("Входим как {name}…", name=account.get("name")))
        self.network.connect(connection_uris(self.server))
        # Пока соединение устанавливается, показываем сохранённое: в метро
        # или в лифте это единственное, что вообще можно показать
        self._show_saved()

    def _show_saved(self):
        """Поднимает переписку с диска и показывает её до всякой связи."""
        кто, переписки = localcache.load_rooms(self.server)
        if not переписки:
            return False

        self.from_cache = True
        self.user = dict(кто)
        self.conversations = переписки
        self._load_drafts()
        self._show_chat()
        self._refresh_me()
        self.status_dot.configure(text_color=OFFLINE)
        self._refresh_side_list()

        последняя = self.config_data.get("last_room", {}).get(self.server)
        если_есть = [one["id"] for one in переписки]
        куда = последняя if последняя in если_есть else если_есть[0]
        self.conversation = куда
        self._update_header()
        self._show_cached_history(куда)
        return True

    def _show_cached_history(self, conversation_id):
        """Рисует ленту из сохранённого и честно говорит, что она такая."""
        items = localcache.load_history(self.server, conversation_id)
        self._clear_messages()
        self.loaded_items = list(items)
        self.drawing_history = True
        try:
            for item in self.loaded_items:
                self._show_item(item)
        finally:
            self.drawing_history = False
        self._service_label(t("Нет связи — показываем сохранённое.")
                            if items else t("Нет связи. Переписка откроется, "
                                            "как только она вернётся."))

    def _keep_history_later(self):
        """Откладывает запись ленты на диск.

        Писать файл на каждое сообщение — это десяток записей за минуту
        живой беседы. Полторы секунды тишины, и хватит одной.
        """
        if self.cache_pending or self.conversation is None:
            return
        self.cache_pending = True
        self.after(1500, self._keep_history_now)

    def _keep_history_now(self):
        self.cache_pending = False
        if self.conversation is None or self.from_cache:
            return
        localcache.save_history(self.server, self.conversation,
                                self.loaded_items)

    def _remember_room(self):
        """Запоминает, какая переписка была открыта последней."""
        if self.conversation is None:
            return
        try:
            self.config_data.setdefault("last_room", {})[self.server] = \
                self.conversation
            store.save(self.config_data)
        except Exception:
            pass

    def _on_send(self):
        text = self.message_entry.get().strip()
        if not text or self.conversation is None:
            return

        if self.editing is not None:
            # Правка уходит вместо нового сообщения: лента поменяется, когда
            # сервер подтвердит — так все увидят одно и то же
            self.network.send(protocol.edit_request(self.editing, text))
            self.message_entry.delete(0, "end")
            self._cancel_reply()
            return

        self.message_entry.delete(0, "end")
        self.drafts.pop(self.conversation, None)
        self._save_drafts()
        now = datetime.now()
        # Свой номер нужен, чтобы узнать сообщение в ответе сервера
        self.local_number += 1
        local = f"l{self.local_number}"
        кадр = protocol.text_message(self.user.get("name", ""), text,
                                     self.conversation, self.reply_to, local)
        if not self.network.send(кадр):
            # Связи нет: сообщение подождёт в очереди и уйдёт само, когда
            # она вернётся. Раньше оно просто не отправлялось — молча
            self.outbox.append((local, кадр))
        item = {"text": text, "kind": "text", "local": local,
                "waiting": self.network.websocket is None,
                "nick": self.user.get("name", t("Я")),
                "user": self.user.get("id"),
                "at": now.astimezone().isoformat(),
                "conversation": self.conversation, "reply_to": self.reply_to}
        # Своё сообщение сервер обратно не присылает, поэтому кладём его
        # в ленту сами: иначе оно пропадёт при следующей перерисовке
        self.loaded_items.append(item)
        self._ensure_date(now.strftime("%d.%m"))
        self._add_bubble(self.user.get("name", t("Я")), text, own=True,
                         time_text=now.strftime("%H:%M"), item=item)
        self._bump_preview({"text": text, "kind": "text", "nick": t("Вы"),
                            "at": now.astimezone().isoformat(),
                            "conversation": self.conversation}, notify=False)
        self._cancel_reply()

    def _on_attach(self):
        """Выбор файла для отправки."""
        if self.network.websocket is None:
            return

        path = filedialog.askopenfilename(
            title=t("Что отправляем?"),
            filetypes=[
                (t("Картинки и видео"), "*.png *.jpg *.jpeg *.webp *.bmp *.gif "
                                     "*.mp4 *.mov *.webm *.mkv *.avi *.m4v"),
                (t("Картинки"), "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                (t("Видео"), "*.mp4 *.mov *.webm *.mkv *.avi *.m4v"),
                (t("Все файлы"), "*.*"),
            ])
        if path:
            self._send_file(Path(path))

    def _choose_avatar(self):
        """Выбор картинки для профиля."""
        path = filedialog.askopenfilename(
            title=t("Выберите фото"),
            filetypes=[(t("Картинки"), "*.png *.jpg *.jpeg *.webp *.bmp"),
                       (t("Все файлы"), "*.*")])
        if not path:
            return

        try:
            data = Path(path).read_bytes()
        except OSError as error:
            self.profile_hint.configure(
                text=t("Не удалось прочитать файл: {error}", error=error),
                                        text_color=OFFLINE)
            return

        self.profile_hint.configure(text=t("Отправляем фото…"), text_color=MUTED)
        self.network.send(protocol.avatar_header(Path(path).name, len(data)), data)

    def _save_profile(self):
        name = self.profile_name.get().strip()
        bio = self.profile_bio.get("1.0", "end").strip()
        if not name:
            self.profile_hint.configure(text=t("Имя не может быть пустым."),
                                        text_color=OFFLINE)
            return
        self.network.send(protocol.profile_message(name, bio))
        self.profile_hint.configure(text=t("Сохраняем…"), text_color=MUTED)

    def _on_ctrl_key(self, event):
        """Ctrl+V в поле сообщения: сначала пробуем картинку из буфера."""
        if event.keycode != 86:  # клавиша V, независимо от раскладки
            return self._on_entry_shortcut(event)
        if self._paste_from_clipboard() == "break":
            return "break"
        return self._paste_text(event.widget)

    def _on_entry_shortcut(self, event):
        """Ctrl+A/C/X/V в полях ввода.

        Tkinter вешает эти сочетания на буквы, а при русской раскладке
        приходит не «v», а «м», и встроенная вставка просто не срабатывает.
        Поэтому ловим по коду клавиши — он от раскладки не зависит.
        """
        entry = event.widget

        if event.keycode == 65:  # A — выделить всё
            entry.select_range(0, "end")
            entry.icursor("end")
            return "break"

        if event.keycode in (67, 88):  # C и X — копировать и вырезать
            try:
                selection = entry.selection_get()
            except Exception:
                return "break"
            self.clipboard_clear()
            self.clipboard_append(selection)
            if event.keycode == 88:
                entry.delete("sel.first", "sel.last")
            return "break"

        if event.keycode == 86:  # V — вставить
            return self._paste_text(entry)

        return None

    def _paste_text(self, entry):
        """Вставляет текст из буфера в поле, заменяя выделенное."""
        try:
            text = self.clipboard_get()
        except Exception:
            return "break"

        drop_placeholder(entry)
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        entry.insert("insert", text.strip().replace("\n", " "))
        return "break"

    def _paste_from_clipboard(self):
        """Возвращает "break", если вставку обработали сами."""
        if self.network.websocket is None:
            return None

        try:
            from PIL import ImageGrab
            content = ImageGrab.grabclipboard()
        except Exception:
            return None

        if isinstance(content, Image.Image):
            buffer = io.BytesIO()
            content.save(buffer, "PNG")
            self._send_bytes(t("вставка.png"), buffer.getvalue())
            return "break"

        if isinstance(content, list) and content:
            for item in content[:5]:
                path = Path(item)
                if path.is_file():
                    self._send_file(path)
            return "break"

        return None  # в буфере текст — пусть вставится как обычно

    def _send_file(self, path):
        kind = protocol.kind_of(path.name)
        try:
            size = path.stat().st_size
        except OSError as error:
            self._service_label(t("Не удалось прочитать файл: {error}", error=error))
            return

        предел = protocol.limit_for(kind, self.limits)
        if size > предел:
            self._service_label(t(
                "«{name}» весит {size}, а больше {limit} сервер не принимает.",
                name=path.name, size=protocol.human_size(size),
                limit=protocol.human_size(предел)))
            return

        # Картинку отправляем целиком: сервер её ещё и ужмёт. Всё остальное
        # едет кусками — гигабайтное видео в памяти держать негде
        if kind in ("image", "gif"):
            try:
                self._send_bytes(path.name, path.read_bytes())
            except OSError as error:
                self._service_label(t("Не удалось прочитать файл: {error}",
                                      error=error))
            return

        self._start_upload(path, kind, size)

    def _start_upload(self, path, kind, size):
        """Заводит порционную отправку и показывает, как она идёт."""
        self.local_number += 1
        local = f"l{self.local_number}"

        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        self.loaded_items.append({"kind": kind, "name": path.name, "size": size,
                                  "nick": self.user.get("name", t("Я")),
                                  "user": self.user.get("id"), "local": local,
                                  "at": now.astimezone().isoformat(),
                                  "conversation": self.conversation})
        строка = self._service_label(t("Отправляю «{name}» — 0%", name=path.name))

        self.sending[local] = {"path": path, "size": size, "name": path.name,
                               "label": строка, "ticket": None}
        self.network.send(protocol.upload_request(path.name, size,
                                                  self.conversation,
                                                  self.reply_to, local))
        self._cancel_reply()

    def _on_upload_ready(self, message):
        """Сервер готов принимать — начинаем слать куски из отдельного потока."""
        ticket = message.get("ticket")
        local = message.get("local")
        отправка = self.sending.get(local) or next(
            (one for one in self.sending.values() if one["ticket"] is None), None)
        if отправка is None:
            return

        отправка["ticket"] = ticket
        размер_куска = int(message.get("chunk") or protocol.CHUNK_SIZE)

        def качать():
            # Файл читаем в своём потоке: гигабайт по кускам — это надолго,
            # а окно должно оставаться живым
            try:
                with open(отправка["path"], "rb") as файл:
                    while True:
                        кусок = файл.read(размер_куска)
                        if not кусок:
                            break
                        # Ждём, пока кусок уйдёт: иначе в очереди окажется
                        # весь файл целиком
                        if not self.network.send(protocol.chunk_header(ticket),
                                                 кусок, wait=True):
                            return
            except OSError as error:
                self.after(0, lambda: self._service_label(
                    t("Не удалось прочитать файл: {error}", error=error)))

        threading.Thread(target=качать, daemon=True).start()

    def _on_upload_progress(self, message):
        """Сколько уже дошло — подписываем это в переписке."""
        ticket = message.get("ticket")
        отправка = next((one for one in self.sending.values()
                         if one["ticket"] == ticket), None)
        if отправка is None:
            return

        доля = int(100 * int(message.get("sent") or 0)
                   / max(int(message.get("size") or 1), 1))
        строка = отправка["label"]
        if строка is not None and строка.winfo_exists():
            for widget in строка.winfo_children():
                widget.configure(text=t("Отправляю «{name}» — {percent}%",
                                        name=отправка["name"], percent=доля))

    # ------------------------------------------------- голос и кружочки

    def _place_record_buttons(self):
        """Ставит кнопки записи, если записывать есть чем."""
        есть_чем = recorder.available() and recorder.microphones()
        if not есть_чем:
            self.voice_button.grid_remove()
            self.circle_button.grid_remove()
            return
        self.voice_button.grid(row=0, column=2, padx=(0, 8), pady=13)
        if recorder.cameras():
            self.circle_button.grid(row=0, column=3, padx=(0, 8), pady=13)
        else:
            self.circle_button.grid_remove()

    def _start_recording(self, kind):
        """Начинает запись голоса или кружочка."""
        if self.recording is not None or self.conversation is None:
            return

        микрофон = recorder.pick_microphone(self.settings.get("microphone"))
        камера = (recorder.pick_camera(self.settings.get("camera"))
                  if kind == "circle" else None)

        запись = recorder.Recording(kind, микрофон, камера)
        if запись.error or not запись.running:
            запись.cancel()
            self._service_label(t("Записать не вышло: {error}",
                                  error=запись.error or t("устройство занято")))
            return

        self.recording = запись
        self.composer.grid_remove()
        self.record_bar.grid(row=4, column=0, sticky="ew")
        self.record_label.configure(text=t("Записываю голос…") if kind == "voice"
                                    else t("Записываю кружочек…"))
        self._tick_recording()

    def _tick_recording(self):
        """Считает секунды и мигает точкой, пока идёт запись."""
        if self.recording is None:
            return

        прошло = self.recording.seconds
        предел = (recorder.MAX_VOICE if self.recording.kind == "voice"
                  else recorder.MAX_CIRCLE)
        осталось = предел - прошло
        подпись = (t("Записываю голос…") if self.recording.kind == "voice"
                   else t("Записываю кружочек…"))
        self.record_label.configure(
            text=f"{подпись}  {int(прошло) // 60}:{int(прошло) % 60:02d}"
                 + (f"  ·  {t('осталось')} {int(осталось)}" if осталось < 11 else ""))
        # Точка мигает раз в секунду: так видно, что запись жива
        self.record_dot.configure(
            text_color=OFFLINE if int(прошло * 2) % 2 == 0 else SEPARATOR)

        if not self.recording.running or осталось <= 0:
            # ffmpeg остановился сам, дойдя до предела
            self._finish_recording()
            return
        self.recording_job = self.after(200, self._tick_recording)

    def _hide_record_bar(self):
        if self.recording_job is not None:
            try:
                self.after_cancel(self.recording_job)
            except Exception:
                pass
            self.recording_job = None
        self.record_bar.grid_remove()
        self.composer.grid(row=4, column=0, sticky="ew")

    def _cancel_recording(self):
        """Бросает запись, ничего не отправляя."""
        if self.recording is None:
            return
        self.recording.cancel()
        self.recording = None
        self._hide_record_bar()

    def _finish_recording(self):
        """Останавливает запись и отправляет её как сообщение."""
        запись = self.recording
        if запись is None:
            return
        self.recording = None
        self._hide_record_bar()

        файл = запись.stop()
        if файл is None:
            self._service_label(t("Записать не вышло: {error}",
                                  error=запись.error or t("пустая запись")))
            return

        try:
            данные = файл.read_bytes()
        except OSError as беда:
            self._service_label(t("Не удалось прочитать файл: {error}", error=беда))
            запись.forget()
            return
        finally:
            pass

        секунд = getattr(запись, "seconds_done", None)
        self._send_bytes(файл.name, данные, kind=запись.kind, seconds=секунд)
        запись.forget()

    def _send_bytes(self, name, data, kind=None, seconds=None):
        if len(data) > protocol.MAX_MEDIA_SIZE:
            self._service_label(t(
                "«{name}» весит {size}, а больше {limit} сервер не принимает.",
                name=name, size=protocol.human_size(len(data)),
                limit=protocol.human_size(protocol.MAX_MEDIA_SIZE)))
            return

        kind = kind or protocol.kind_of(name)
        self.local_number += 1
        local = f"l{self.local_number}"
        self.network.send(protocol.media_header(self.user.get("name", ""), kind,
                                                name, len(data), self.conversation,
                                                self.reply_to, local, seconds), data)

        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        запись = {"kind": kind, "name": name, "size": len(data),
                  "nick": self.user.get("name", t("Я")),
                  "user": self.user.get("id"), "local": local,
                  "at": datetime.now().astimezone().isoformat(),
                  "conversation": self.conversation}
        if seconds:
            запись["seconds"] = seconds
        self.loaded_items.append(запись)
        self._add_media_bubble(self.user.get("name", t("Я")), own=True, kind=kind,
                               media_id=None, name=name, size=len(data),
                               time_text=now.strftime("%H:%M"), data=data,
                               item={"reply_to": self.reply_to, "seconds": seconds})
        self._cancel_reply()

    def _on_leave(self):
        """Возврат к выбору аккаунта — сессия сохраняется."""
        self._stop_retrying()
        self.token = None
        self.network.disconnect()
        self.primary_button.configure(text=t("ВОЙТИ"), state="normal")
        self.password_entry.delete(0, "end")
        self._show_auth()

    def _toggle_theme(self):
        light = ctk.get_appearance_mode() == "Light"
        ctk.set_appearance_mode("dark" if light else "light")

    def _on_close(self):
        """Крестик окна: прячем в трей либо выходим совсем."""
        self._keep_draft()
        if self.settings.get("tray", True) and self.tray.available:
            self.withdraw()
            self.tray.show()
            if not self.hidden_notice_shown:
                self.tray.notify(
                    t("Velix свернулся в трей"),
                    t("Программа продолжает работать. "
                      "Значок рядом с часами открывает окно обратно."))
                self.hidden_notice_shown = True
            return
        self._quit()

    def _quit(self):
        """Полный выход: закрываем связь и убираем значок."""
        self.tray.hide()
        self.network.disconnect()
        self.destroy()

    def _restore_window(self):
        """Показывает спрятанное окно обратно."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.tray.hide()

    def _on_resize(self, event):
        if event.widget is not self:
            return
        # Пузырь не должен растягиваться на всю переписку — ограничиваем ширину
        wrap = max(240, int((event.width - 292) * 0.62))
        if abs(wrap - self.wrap_length) > 24:
            self.wrap_length = wrap

    # ------------------------------------------------------------- события

    def _pump_events(self):
        """Разбирает пришедшее, пропуская вперёд лёгкие кадры.

        Вложения тяжёлые: пока разбираются два десятка фотографий, ответ
        на «открой переписку» ждёт своей очереди — и лента стоит пустая.
        Поэтому сперва всё быстрое, а картинки — понемногу за раз.

        Что бы ни случилось внутри, разбор продолжается: сорвавшись один
        раз, он раньше умолкал навсегда — окно оставалось на связи, но
        переставало что-либо показывать.
        """
        try:
            self._pump_once()
        except Exception:
            # Одна беда не должна затыкать окно навсегда: раньше сорвавшийся
            # разбор больше не запускался, и клиент, оставаясь на связи,
            # переставал показывать что-либо вообще
            traceback.print_exc()
        finally:
            self.after(60, self._pump_events)

    def _pump_once(self):
        отложенные = []
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "message" and isinstance(payload, dict) \
                        and payload.get("type") == "blob":
                    отложенные.append(payload)
                    continue

                if kind == "opened":
                    self._on_opened(payload)
                elif kind == "tray_open":
                    self._restore_window()
                elif kind == "tray_quit":
                    self._quit()
                    return
                elif kind == "message":
                    self._on_message(payload)
                elif kind == "disconnected":
                    self._on_disconnected()
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass

        for вложение in отложенные[:BLOBS_PER_TURN]:
            self._on_message(вложение)
        for вложение in отложенные[BLOBS_PER_TURN:]:
            # Остальные подождут следующего захода — окно должно дышать
            self.events.put(("message", вложение))

    def _flush_outbox(self):
        """Связь вернулась — досылаем написанное, по порядку."""
        while self.outbox:
            local, кадр = self.outbox[0]
            if not self.network.send(кадр):
                return          # опять пропала: остальное подождёт
            self.outbox.pop(0)
            self._paint_tick(local, "sending")

    def _on_opened(self, secure):
        """Соединение открылось — отправляем то, чем собирались входить."""
        self.secure = bool(secure)
        if self.pending_login:
            self.network.send(self.pending_login)
        # Написанное без связи ждало в очереди: теперь ему есть куда уйти.
        # Даём входу пройти первым — сервер не примет чужое без него
        self.after(1200, self._flush_outbox)

    def _on_welcome(self, message):
        self.from_cache = False
        self._stop_retrying()
        self.recover_mode = False
        self.user = dict(message.get("user") or {})
        self.token = message.get("token")
        self.available_update = message.get("update")
        self.is_admin = bool(message.get("admin"))
        self.limits = message.get("limits") or {}
        self.pending_login = None

        store.remember_account(self.config_data, self.user.get("login", ""),
                               self.user.get("name", ""), self.server, self.token)
        store.save(self.config_data)

        # Общего чата больше нет: пока переписки не пришли, открывать нечего
        self.conversation = None
        self.conversations = []
        self.people = []
        self.online = set()
        self.seen = {}
        self.quotes = {}
        self.loaded_items = []
        self._clear_messages()
        self.pending_media.clear()
        self.avatar_waiters.clear()

        # Код приходит только при регистрации и после смены пароля
        if message.get("recovery"):
            self.after(400, lambda: self._show_recovery(message["recovery"]))
        self.images.clear()
        self.animations.clear()
        self.empty_hint = self._service_label(t("Пока тихо. Напишите первым."))

        self._refresh_me()
        self._load_drafts()
        self.status_dot.configure(text_color=ONLINE)
        self._refresh_subtitle()
        if not self.secure:
            self._service_label(
                t("Соединение без шифрования: сервер не умеет wss://. "
                  "Переписку в такой сети можно перехватить."))
        self.message_entry.configure(state="normal")
        self.send_button.configure(state="normal")
        self.attach_button.configure(state="normal")
        self.primary_button.configure(text=t("ВОЙТИ"), state="normal")
        self.password_entry.delete(0, "end")
        self._show_chat()

    def _refresh_me(self):
        """Обновляет свою карточку в панели слева."""
        name = self.user.get("name", "?")
        self.my_name.configure(text=name)
        self.my_login.configure(text=f"{self.user.get('login', '')} · {self.server}")
        self._paint_avatar(self.my_avatar, name, self.user.get("avatar"), 40)

    def _on_message(self, message):
        kind = message.get("type")

        if kind == "welcome":
            self._on_welcome(message)
        elif kind == "authfail":
            self._on_authfail(i18n.from_server(message))
        elif kind == "history":
            self._show_history(message)
        elif kind == "conversations":
            localcache.save_rooms(self.server, self.user,
                                  message.get("items") or [])
            self.conversations = message.get("items") or []
            self._refresh_side_list()
            self._update_header()
            известные = {one["id"] for one in self.conversations}

            if self.conversation is None and self.conversations:
                # После обрыва возвращаемся туда, где человек был, а не
                # в первую попавшуюся переписку
                куда = (self.was_open if self.was_open in известные
                        else self.conversations[0]["id"])
                self._open(куда, force=True)
            elif self.conversation in известные:
                # Связь могла пропасть в тот самый миг, когда мы просили
                # историю: просим заново, иначе лента так и будет пустой
                self._open(self.conversation, force=True)
            elif not self.conversations:
                self._nothing_open()
        elif kind == "conversation":
            self._on_conversation(message)
        elif kind == "ack":
            self._on_ack(message)
        elif kind == "receipts":
            self._on_receipts(message)
        elif kind == "people":
            self.people = message.get("items") or []
            self.online = set(message.get("online") or [])
            for person in self.people:
                if person.get("seen"):
                    self.seen[person["id"]] = person["seen"]
            self._refresh_side_list()
            self._refresh_subtitle()
        elif kind == "presence":
            self._on_presence(message)
        elif kind == "typing":
            self._on_typing(message)
        elif kind == "gallery":
            self._show_gallery(message)
        elif kind == "preview":
            self._on_preview(message)
        elif kind == "edited":
            self._on_edited(message)
        elif kind == "deleted":
            self._on_deleted(message)
        elif kind == "reactions":
            self._on_reactions(message)
        elif kind == "pinned":
            self._on_pinned(message)
        elif kind == "admin":
            self._on_admin(message)
        elif kind == "upload_ready":
            self._on_upload_ready(message)
        elif kind == "upload_progress":
            self._on_upload_progress(message)
        elif kind == "search":
            self._show_search(message)
        elif kind in ("text", "media"):
            self._on_incoming(message)
        elif kind == "blob":
            self._fill_media(message)
        elif kind == "update_blob":
            self._install_update(message)
        elif kind == "profile":
            self._on_profile(message)
        elif kind in ("system", "error"):
            self._service_label(i18n.from_server(message))

    def _on_profile(self, message):
        user = message.get("user") or {}
        self.user.update(user)
        store.update_name(self.config_data, self.user.get("login"), self.server,
                          self.user.get("name"))
        store.save(self.config_data)
        self._refresh_me()
        if self.profile_view.winfo_ismapped():
            self._paint_avatar(self.profile_avatar, self.user.get("name", "?"),
                               self.user.get("avatar"), AVATAR_LARGE)
            self.profile_hint.configure(text=t("Сохранено"), text_color=ONLINE)

    def _show_recovery(self, code):
        """Показывает код восстановления — единственный раз, когда он виден."""
        window = ctk.CTkToplevel(self)
        window.title(t("Сохраните код восстановления"))
        window.geometry("420x260")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)

        ctk.CTkLabel(window, text=t("Сохраните код восстановления"),
                     font=self.font_name, text_color=TEXT).pack(pady=(22, 8))

        ctk.CTkLabel(window, text=code, font=self.font_title,
                     text_color=ACCENT).pack(pady=(0, 10))

        ctk.CTkLabel(
            window,
            text=t("По нему меняют пароль, если его забыли. Другого способа нет: "
                   "почту мы не спрашиваем, а сервер стоит у вас дома."),
            font=self.font_small, text_color=MUTED, wraplength=340,
            justify="left").pack(padx=30)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(code)
            hint.configure(text=t("Код скопирован"))

        ctk.CTkButton(window, text=t("Копировать"), width=300, height=38,
                      corner_radius=R_ITEM, font=self.font_small, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=TEXT,
                      command=copy).pack(pady=(14, 4))

        hint = ctk.CTkLabel(window, text="", font=self.font_small,
                            text_color=ONLINE)
        hint.pack()

        ctk.CTkButton(window, text=t("Понятно"), width=300, height=32,
                      corner_radius=R_SMALL, font=self.font_small,
                      fg_color="transparent", hover_color=INPUT_BG,
                      text_color=MUTED, command=window.destroy).pack(pady=(2, 12))

    def _on_authfail(self, text):
        self.pending_login = None
        self.network.disconnect()
        self.primary_button.configure(text=t("ВОЙТИ"), state="normal")
        self.auth_view.pack(fill="both", expand=True)
        self.chat_view.pack_forget()
        self.auth_error.configure(text=text)
        if not self.form.winfo_ismapped():
            self._show_form(register=False)
            self.auth_error.configure(text=text)

    def _on_incoming(self, message):
        """Сообщение из сети: показываем, если оно в открытой переписке."""
        where = message.get("conversation")
        if message.get("user") != self.user.get("id") \
                and self.settings.get("sound", True):
            # Своё эхо не озвучиваем: человек и так знает, что написал
            chime.play()
        if where not in (None, self.conversation) \
                or self.state() != "normal":
            # Пришло не сюда или окно спрятано — считаем непрочитанным
            if message.get("user") != self.user.get("id"):
                self.unread[where] = self.unread.get(where, 0) + 1

        if message.get("conversation") not in (None, self.conversation):
            # Пришло в другую переписку: ленту не трогаем, только
            # обновляем строку в списке слева
            self._bump_preview(message)
            return
        self.loaded_items.append(message)
        self._show_item(message)
        self._keep_history_later()
        self._bump_preview(message, notify=False)
        if message.get("id"):
            self._mark_read([message["id"]])

    def _bump_preview(self, message, notify=True):
        """Обновляет строчку в списке слева, не открывая переписку.

        Вызывается только для живых сообщений: во время загрузки истории
        перерисовывать список полсотни раз незачем.
        """
        conversation = message.get("conversation") or self.conversation
        for item in self.conversations:
            if item["id"] == conversation:
                item["last"] = {"text": message.get("text", ""),
                                "kind": message.get("kind", "text"),
                                "at": message.get("at"), "nick": message.get("nick")}
        self._refresh_side_list()
        if notify:
            self._notify_if_hidden(message.get("nick", ""), message.get("text", ""))

    def _show_item(self, item):
        """Показывает одно сообщение — своё или чужое, текст или вложение."""
        moment = local_time(item.get("at"))
        self._ensure_date(moment.strftime("%d.%m"))
        nickname = item.get("nick", "?")
        time_text = moment.strftime("%H:%M")
        avatar = item.get("avatar")
        own = item.get("user") == self.user.get("id")

        if item.get("kind") == "deleted":
            row = ctk.CTkFrame(self.messages, fg_color="transparent")
            row.pack(fill="x", padx=22, pady=2)
            ctk.CTkLabel(row, text=t("сообщение удалено"), font=self.font_small,
                         text_color=MUTED).pack(anchor="e" if own else "w")
            if item.get("id"):
                self.rows[item["id"]] = row
            return

        if item.get("kind", "text") == "text":
            self._add_bubble(nickname, item.get("text", ""), own=own,
                             time_text=time_text, avatar=avatar, item=item)
            if not own:
                self._notify_if_hidden(nickname, item.get("text", ""))
        else:
            self._add_media_bubble(nickname, own=own, kind=item["kind"],
                                   media_id=item.get("media"),
                                   name=item.get("name", t("файл")),
                                   size=item.get("size", 0), time_text=time_text,
                                   avatar=avatar, item=item)

    def _on_presence(self, message):
        """Кто-то пришёл или ушёл."""
        user_id = message.get("user")
        if message.get("online"):
            self.online.add(user_id)
        else:
            self.online.discard(user_id)
            if message.get("seen"):
                self.seen[user_id] = message["seen"]
        self._refresh_side_list()
        self._refresh_subtitle()

    def _notify_if_hidden(self, nickname, text):
        """Пока окно в трее, о новых сообщениях сообщаем всплывашкой."""
        if self.state() == "withdrawn":
            self.tray.notify(nickname, text[:120])

    def _on_disconnected(self):
        self.status_dot.configure(text_color=OFFLINE)
        self.message_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.attach_button.configure(state="disabled")

        if self.token and self.server:
            self.header_subtitle.configure(text=t("нет связи · возвращаемся…"))
            self._retry_connect()
        else:
            self.header_subtitle.configure(text=t("нет связи с сервером"))
            if self.chat_view.winfo_ismapped():
                self._service_label(
                    t("Соединение потеряно. Нажмите «Сменить», "
                      "чтобы войти заново."))

    def _retry_connect(self):
        """Возвращается на связь сам, с растущей паузой.

        Раньше окно просто говорило «нажмите Сменить» и сидело так до
        последнего: интернет мигнул — и переписка стояла пустая, потому что
        запрос истории уходил в никуда.
        """
        if self.retry_job is not None:
            self.after_cancel(self.retry_job)

        пауза = min(1000 * 2 ** self.retry_at, 30000)
        self.retry_at = min(self.retry_at + 1, 6)

        def попробовать():
            self.retry_job = None
            if self.network.websocket is not None or not self.token:
                return
            self.pending_login = protocol.auth_message(self.token)
            self.network.connect(connection_uris(self.server))

        self.retry_job = self.after(пауза, попробовать)

    def _stop_retrying(self):
        """Больше не возвращаемся: человек ушёл сам."""
        if self.retry_job is not None:
            self.after_cancel(self.retry_job)
            self.retry_job = None
        self.retry_at = 0

    def _on_error(self, text):
        self.pending_login = None
        self.primary_button.configure(text=t("ВОЙТИ"), state="normal")
        self.auth_error.configure(text=text)
        if not self.auth_view.winfo_ismapped():
            self._show_auth()
        self.auth_error.configure(text=text)

    # ----------------------------------------------------------- аватарки

    def _paint_avatar(self, label, nickname, avatar_id, side):
        """Рисует кружок с буквой, а если есть фото — заменяет его фотографией."""
        label.configure(text=(nickname or "?")[0].upper(), image=None,
                        fg_color=avatar_color(nickname), corner_radius=side // 2,
                        width=side, height=side)
        # image=None в CustomTkinter картинку не снимает — она просто остаётся
        # на месте. Поэтому чистим внутреннюю метку сами, иначе прежнее фото
        # просвечивало бы из-под буквы у всех подряд.
        label._label.configure(image="")

        # Кружок в шапке один на все переписки: помечаем, чьё фото он ждёт,
        # иначе запоздавшая картинка легла бы на уже другого человека
        label.velix_avatar = avatar_id

        if not avatar_id:
            return

        cached = self.avatar_cache.get((avatar_id, side))
        if cached is not None:
            label.configure(text="", image=cached, fg_color="transparent")
            return

        self.avatar_waiters.setdefault(avatar_id, []).append((label, side))
        if len(self.avatar_waiters[avatar_id]) > 1:
            return

        сохранённое = mediacache.get(avatar_id)
        if сохранённое is not None:
            self._fill_avatar(avatar_id, сохранённое)
        else:
            self._ask_media(avatar_id)

    def _fill_avatar(self, avatar_id, data):
        waiters = self.avatar_waiters.pop(avatar_id, [])
        for label, side in waiters:
            try:
                picture = circular(data, side)
            except Exception:
                continue
            image = ctk.CTkImage(light_image=picture, dark_image=picture,
                                 size=(side, side))
            self.avatar_cache[(avatar_id, side)] = image
            self.images.append(image)
            if getattr(label, "velix_avatar", None) != avatar_id:
                continue          # кружок за это время достался другому
            if label.winfo_exists():
                label.configure(text="", image=image, fg_color="transparent")

    # ----------------------------------------------------------- сообщения

    # ------------------------------------------------------------ переписки

    def _tween(self, ms, шаг, конец=None):
        """Проигрывает короткое движение: шаг(доля) от нуля до единицы."""
        начало = time.monotonic()

        def тик():
            доля = min((time.monotonic() - начало) * 1000 / max(ms, 1), 1.0)
            try:
                шаг(ease(доля))
            except (tkinter.TclError, RuntimeError):
                return              # виджет исчез посреди движения
            if доля < 1.0:
                self.after(STEP_MS, тик)
            elif конец is not None:
                конец()

        тик()

    def _fade_widget(self, widget, откуда, куда, ms=FADE_MS, ключ="fg_color"):
        """Перекрашивает виджет из одного цвета в другой."""
        def шаг(доля):
            if widget.winfo_exists():
                widget.configure(**{ключ: mix(откуда, куда, доля)})

        self._tween(ms, шаг)

    def _glide(self, canvas, пикселей):
        """Доводит список до места плавно, а не рывком.

        Щелчки колеса складываются: три щелчка подряд — это один длинный
        проезд, а не три отдельных прыжка друг через друга.
        """
        всё = canvas.bbox("all")
        видно = canvas.winfo_height()
        if not всё or всё[3] <= видно:
            return
        высота = всё[3]
        предел = max(1 - видно / высота, 0.0)

        было = self.glides.get(canvas)
        откуда = canvas.yview()[0]
        цель = (было[1] if было else откуда) + пикселей / высота
        цель = min(max(цель, 0.0), предел)
        if было:
            self.glides[canvas] = (откуда, цель, было[2])
            return              # едущее движение само подхватит новую цель

        поездка = [True]        # пока правда, поездка ещё нужна
        self.glides[canvas] = (откуда, цель, поездка)

        def шаг(доля):
            запись = self.glides.get(canvas)
            if запись is None or not поездка[0]:
                return
            начало, конец, _ = запись
            canvas.yview_moveto(начало + (конец - начало) * доля)

        def всё_приехало():
            запись = self.glides.get(canvas)
            if запись is not None and запись[2] is поездка:
                self.glides.pop(canvas, None)

        self._tween(GLIDE_MS, шаг, всё_приехало)

    def _stop_glide(self, canvas):
        """Обрывает поездку: лента прыгает вниз сама, без спорщиков."""
        запись = self.glides.pop(canvas, None)
        if запись is not None:
            запись[2][0] = False

    def _on_wheel(self, event):
        """Колесо листает крупнее.

        CustomTkinter крутит по двадцать пикселей за щелчок — это ползком.
        Своя привязка заменяет общую: она находит список под указателем и
        двигает его на человеческий шаг.
        """
        frame = self._scrollable_under(event)
        if frame is None:
            return None

        canvas = frame._parent_canvas
        if canvas.yview() == (0.0, 1.0):
            return None      # всё и так помещается

        щелчков = int(event.delta / 120) or (1 if event.delta > 0 else -1)
        self._glide(canvas, -щелчков * WHEEL_STEP)
        return "break"

    def _scrollable_under(self, event):
        """Какой из списков сейчас под указателем."""
        известные = [one for one in (self.messages, self.side_list,
                                     getattr(self, "admin_list", None))
                     if one is not None]
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return None

        while widget is not None:
            for frame in известные:
                if widget in (frame, frame._parent_canvas,
                              getattr(frame, "_parent_frame", None)):
                    return frame
            widget = getattr(widget, "master", None)
        return None

    def _refresh_side_list(self):
        """Перерисовывает список: переписки, а по запросу — и люди."""
        for widget in self.side_list.winfo_children():
            widget.destroy()

        запрос = self.search_entry.get().strip().lstrip("@").lower()

        for item in self.conversations:
            if запрос and запрос not in self._title_of(item).lower():
                continue
            self._conversation_row(item)

        # Всех подряд не показываем: в списке переписки, люди — по поиску
        if not запрос:
            return

        нашлись = [person for person in self.people
                   if person["id"] != self.user.get("id")
                   and (запрос in str(person.get("login", "")).lower()
                        or запрос in str(person.get("name", "")).lower())]
        if not нашлись:
            return

        ctk.CTkLabel(self.side_list, text=t("ЛЮДИ"), font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x", padx=10,
                                                        pady=(14, 4))
        for person in нашлись:
            self._person_row(person)

    def _on_search_typing(self, event=None):
        """Набирают в поиске — сразу подсказываем, кто нашёлся."""
        self._refresh_side_list()

    def _title_of(self, item):
        """Название переписки.

        Общий чат заведён на сервере с русским названием, но человеку его
        нужно показать на своём языке.
        """
        item = item or {}
        if item.get("id") == protocol.GENERAL_ID:
            return t("Общий чат")
        return item.get("title") or t("Общий чат")

    def _conversation_row(self, item):
        active = item["id"] == self.conversation
        row = ctk.CTkFrame(self.side_list,
                           fg_color=SIDEBAR_ACTIVE if active else "transparent",
                           corner_radius=R_ITEM, height=64)
        row.pack(fill="x", pady=3)
        row.pack_propagate(False)

        title = self._title_of(item)
        colour = ON_ACCENT if active else TEXT
        quiet = ON_ACCENT if active else MUTED

        avatar = ctk.CTkLabel(row, text="", width=40, height=40, font=self.font_sender)
        avatar.pack(side="left", padx=(8, 8), pady=10)
        self._paint_avatar(avatar, title, item.get("avatar"), 40)

        lines = ctk.CTkFrame(row, fg_color="transparent")
        lines.pack(side="left", fill="both", expand=True, pady=10, padx=(0, 8))

        top = ctk.CTkFrame(lines, fg_color="transparent")
        top.pack(fill="x")
        if item.get("kind") == "group":
            # Значок вместо слова: место в строчке дорого
            ctk.CTkLabel(top, text="👥", font=self.font_small,
                         text_color=quiet).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(top, text=title, font=self.font_name, text_color=colour,
                     anchor="w").pack(side="left")

        last = item.get("last")
        if last:
            moment = local_time(last.get("at"))
            ctk.CTkLabel(top, text=moment.strftime("%H:%M"), font=self.font_small,
                         text_color=quiet).pack(side="right")
            preview = self._what_it_was(last)
            preview = f"{last.get('nick') or ''}: {preview}".strip(": ")
        else:
            preview = t("нет сообщений")

        ctk.CTkLabel(lines, text=short(preview, 30), font=self.font_small,
                     text_color=quiet, anchor="w").pack(fill="x")

        waiting = self.unread.get(item["id"], 0)
        if waiting:
            значок = ctk.CTkLabel(row, text=f" {waiting} ", font=self.font_small,
                                  text_color=ON_ACCENT, fg_color=OFFLINE,
                                  corner_radius=R_ITEM, width=24, height=20)
            значок.pack(side="right", padx=(0, 10))
            # Кружок с числом выскакивает, а не появляется из ниоткуда
            self._tween(160, lambda доля: значок.winfo_exists()
                        and значок.configure(width=int(8 + 16 * доля)))

        for widget in (row, lines, avatar):
            widget.bind("<Button-1>", lambda event, i=item["id"]: self._open(i))
            widget.bind("<Button-3>", lambda event, i=item: self._group_menu(event, i))
        for child in lines.winfo_children():
            child.bind("<Button-1>", lambda event, i=item["id"]: self._open(i))
            child.bind("<Button-3>", lambda event, i=item: self._group_menu(event, i))
        if not active:
            self._make_hoverable(row, [lines, avatar] + lines.winfo_children())

    def _make_hoverable(self, row, дети=()):
        """Строчка мягко светлеет, когда на неё наводят.

        Наведение считаем по всей строчке: указатель, переходя с ряда на
        подпись внутри него, не должен гасить подсветку.
        """
        внутри = [False]        # указатель сейчас над строчкой?

        def войти(event=None):
            if внутри[0] or not row.winfo_exists():
                return
            внутри[0] = True
            self._fade_widget(row, SIDEBAR, SIDEBAR_HOVER, HOVER_MS)

        def выйти(event=None):
            if not row.winfo_exists():
                return
            указатель = row.winfo_containing(row.winfo_pointerx(),
                                             row.winfo_pointery())
            место = указатель
            while место is not None:
                if место is row:
                    return          # ушли на свою же подпись — не гасим
                место = getattr(место, "master", None)
            внутри[0] = False
            self._fade_widget(row, SIDEBAR_HOVER, "transparent", HOVER_MS)

        for widget in [row, *дети]:
            widget.bind("<Enter>", войти, add="+")
            widget.bind("<Leave>", выйти, add="+")

    def _person_row(self, person):
        row = ctk.CTkFrame(self.side_list, fg_color="transparent", height=46)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        avatar = ctk.CTkLabel(row, text="", width=32, height=32, font=self.font_small)
        avatar.pack(side="left", padx=(8, 8), pady=7)
        self._paint_avatar(avatar, person["name"], person.get("avatar"), 32)

        подписи = ctk.CTkFrame(row, fg_color="transparent")
        подписи.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(подписи, text=person["name"], font=self.font_body,
                     text_color=TEXT, anchor="w").pack(fill="x")
        логин = person.get("login")
        if логин:
            ctk.CTkLabel(подписи, text=f"@{логин}", font=self.font_small,
                         text_color=MUTED, anchor="w").pack(fill="x")

        ctk.CTkLabel(row, text="●", font=self.font_small,
                     text_color=ONLINE if person["id"] in self.online else MUTED,
                     width=14).pack(side="right", padx=(0, 10))

        row.bind("<Button-1>", lambda event, i=person["id"]: self._start_direct(i))
        for child in row.winfo_children():
            child.bind("<Button-1>", lambda event, i=person["id"]: self._start_direct(i))

    def _keep_draft(self):
        """Запоминает недописанное перед уходом из переписки."""
        if self.conversation is None or not hasattr(self, "message_entry"):
            return
        if self.editing is not None:
            return          # правка — не черновик, она отменится сама
        текст = self.message_entry.get().strip()
        if текст:
            self.drafts[self.conversation] = текст
        else:
            self.drafts.pop(self.conversation, None)
        self._save_drafts()

    def _restore_draft(self):
        """Возвращает недописанное, когда человек вернулся в переписку."""
        if not hasattr(self, "message_entry"):
            return
        self.message_entry.delete(0, "end")
        текст = self.drafts.get(self.conversation)
        if текст:
            self.message_entry.insert(0, текст)

    def _save_drafts(self):
        """Черновики переживают и закрытие окна: их место — в настройках."""
        try:
            self.config_data.setdefault("drafts", {})[self.server] = {
                str(номер): текст for номер, текст in self.drafts.items()}
            store.save(self.config_data)
        except Exception:
            pass            # не сохранилось — черновик всё равно на экране

    def _load_drafts(self):
        сохранённые = (self.config_data.get("drafts") or {}).get(self.server) or {}
        self.drafts = {int(номер): текст for номер, текст in сохранённые.items()
                       if текст}

    def _open(self, conversation_id, force=False):
        """Открывает переписку и просит её историю."""
        if conversation_id == self.conversation and not force:
            return
        self._keep_draft()
        self.conversation = conversation_id
        self.was_open = conversation_id
        self.unread.pop(conversation_id, None)
        self._cancel_reply()
        self._restore_draft()
        self._clear_messages()
        self._refresh_side_list()
        self._update_header()
        self._refresh_pin_bar()

        self._remember_room()
        if not self.network.send(protocol.open_request(conversation_id)):
            # Связи нет — показываем сохранённое. Пусто оставлять нельзя:
            # запрос ушёл в никуда, и перерисовать переписку больше нечему
            self._show_cached_history(conversation_id)
            return

        # Сокет мог умереть молча — например, после сна ноутбука. Тогда
        # запрос уходит «в трубу», ответа нет, и лента остаётся пустой.
        # Через несколько секунд спрашиваем ещё раз, потом честно говорим.
        self.waiting_for = conversation_id
        self.open_token += 1
        self.after(4000, self._nudge_history, conversation_id, 1,
                   self.open_token)

    def _start_direct(self, user_id):
        # Номер переписки знает только сервер, поэтому просто помечаем, что
        # ждём её: пришедшую следом историю откроем, какой бы она ни была
        self.pending_direct = True
        self.network.send(protocol.direct_request(user_id))

    def _nothing_open(self):
        """Переписок нет — показываем, что с этим делать."""
        self.conversation = None
        self._clear_messages()
        self.empty_hint = self._service_label(
            t("Создайте группу или найдите человека по @username."))
        self.header_title.configure(text="Velix")
        self.header_subtitle.configure(text="")

    def _on_conversation(self, message):
        """Появилась новая переписка — например, группа, куда нас позвали."""
        item = message.get("item") or {}
        if not item.get("id"):
            return

        self.conversations = [known for known in self.conversations
                              if known["id"] != item["id"]] + [item]
        self._refresh_side_list()
        if item["id"] == self.conversation:
            # Переписка могла обновиться на ходу — скажем, ей сменили фото
            self._update_header()
        if self.pending_group or self.conversation is None:
            self.pending_group = False
            self._open(item["id"])

    def _new_group(self):
        """Окошко создания группы: название и кого позвать."""
        if self.network.websocket is None:
            return

        window = ctk.CTkToplevel(self)
        window.title(t("Новая группа"))
        window.geometry("340x470")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)
        # Окно захватывает ввод не сразу: Windows отдаёт его с задержкой
        window.after(200, window.grab_set)

        name = ctk.CTkEntry(window, placeholder_text=t("Название группы"),
                            height=40, corner_radius=R_ITEM, border_width=1,
                            border_color=SEPARATOR, fg_color=INPUT_BG,
                            text_color=TEXT, font=self.font_body)
        name.pack(fill="x", padx=18, pady=(18, 10))
        name.bind("<Control-KeyPress>", self._on_entry_shortcut)

        ctk.CTkLabel(window, text=t("Кого позвать"), font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x", padx=20)

        box = ctk.CTkScrollableFrame(window, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=12, pady=6)

        chosen = {}
        for person in self.people:
            if person["id"] == self.user.get("id"):
                continue
            variable = tkinter.IntVar()
            ctk.CTkCheckBox(box, text=person["name"], variable=variable,
                            font=self.font_body, text_color=TEXT, fg_color=ACCENT,
                            hover_color=ACCENT_HOVER).pack(anchor="w", pady=4)
            chosen[person["id"]] = variable

        row = ctk.CTkFrame(window, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(row, text=t("Отмена"), height=38, corner_radius=R_ITEM,
                      font=self.font_small, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=window.destroy).pack(side="left", expand=True,
                                                   fill="x", padx=(0, 6))
        ctk.CTkButton(row, text=t("Создать"), height=38, corner_radius=R_ITEM,
                      font=self.font_button, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                      command=lambda: self._create_group(window, name, chosen)).pack(
            side="left", expand=True, fill="x", padx=(6, 0))

    def _create_group(self, window, name, chosen):
        title = name.get().strip()
        members = [user_id for user_id, box in chosen.items() if box.get()]
        if not title or not members:
            return
        self.pending_group = True
        self.network.send(protocol.group_request(title, members))
        window.destroy()

    def _ask_gallery(self):
        """Просит у сервера все вложения переписки."""
        if self.conversation is None:
            return
        if not self.network.send(protocol.gallery_request(self.conversation)):
            self._service_label(t("Нет связи."))

    def _show_gallery(self, message):
        """Показывает вложения переписки сеткой поверх ленты."""
        if message.get("conversation") != self.conversation:
            return

        items = [one for one in (message.get("items") or [])
                 if one.get("media")]
        if self.viewer is not None:
            self._close_full(self.viewer)

        overlay = ctk.CTkFrame(self, fg_color=CHAT_BG)
        self.viewer = overlay
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._fade_widget(overlay, BUBBLE_IN, CHAT_BG, 160)

        шапка = ctk.CTkFrame(overlay, fg_color="transparent")
        шапка.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(шапка, text=t("Вложения переписки"), font=self.font_name,
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(шапка, text=t("всего: {count}", count=len(items)),
                     font=self.font_small, text_color=MUTED).pack(side="left",
                                                                  padx=10)
        ctk.CTkButton(шапка, text="✕", width=36, height=36, corner_radius=18,
                      font=self.font_button, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=TEXT,
                      command=lambda: self._close_full(overlay)).pack(side="right")

        сетка = ctk.CTkScrollableFrame(overlay, fg_color="transparent")
        сетка.pack(fill="both", expand=True, padx=10, pady=(0, 12))

        if not items:
            ctk.CTkLabel(сетка, text=t("Пока ничего не присылали"),
                         font=self.font_body, text_color=MUTED).pack(pady=30)
            self.bind("<Escape>", lambda event: self._close_full(overlay))
            return

        # Листаем потом ровно то, что показали здесь, и в том же порядке
        порядок = list(reversed(items))
        self.gallery = [{"media": one["media"], "kind": one.get("kind", "image"),
                         "name": one.get("name") or t("вложение")}
                        for one in порядок]

        В_РЯД = 4
        ряд = None
        for место, one in enumerate(порядок):
            if место % В_РЯД == 0:
                ряд = ctk.CTkFrame(сетка, fg_color="transparent")
                ряд.pack(fill="x", pady=3)
            self._gallery_cell(ряд, one)

        self.bind("<Escape>", lambda event: self._close_full(overlay))

    def _gallery_cell(self, ряд, item):
        """Одна клетка сетки: картинка или подпись про видео и файл."""
        клетка = ctk.CTkFrame(ряд, fg_color=INPUT_BG, corner_radius=R_ITEM,
                              width=132, height=132)
        клетка.pack(side="left", padx=3)
        клетка.pack_propagate(False)

        media_id = item.get("media")
        вид = item.get("kind", "image")
        подпись = ctk.CTkLabel(клетка, text="▶" if вид == "video" else "…",
                               font=self.font_body, text_color=MUTED)
        подпись.pack(expand=True)

        данные = self.kept_media.get(media_id) or mediacache.get(media_id)
        if данные is not None and вид in ("image", "gif"):
            self._paint_cell(подпись, данные, media_id)
        elif вид in ("image", "gif"):
            self.pending_media[media_id] = ("cell", подпись, вид)
            self._ask_media(media_id)

        for widget in (клетка, подпись):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda event, m=media_id, k=вид:
                        self._open_from_gallery(m, k))

    def _paint_cell(self, holder, данные, media_id):
        """Рисует уменьшенную картинку в клетке сетки."""
        try:
            picture = self._thumbnail(данные, media_id).copy()
        except Exception:
            return
        picture.thumbnail((128, 128), Image.LANCZOS)
        image = ctk.CTkImage(light_image=picture, dark_image=picture,
                             size=picture.size)
        self.images.append(image)
        if holder.winfo_exists():
            holder.configure(text="", image=image)

    def _open_from_gallery(self, media_id, kind):
        """Из сетки — в полный экран, с листанием по всей переписке."""
        данные = self.kept_media.get(media_id) or mediacache.get(media_id)
        self._close_full(self.viewer)
        self._show_full(данные, kind, media_id)

    def _update_header(self):
        if self.conversation is None:
            return
        item = next((c for c in self.conversations if c["id"] == self.conversation),
                    None)
        title = self._title_of(item)
        self.header_title.configure(text=title)
        self._paint_avatar(self.header_avatar, title, (item or {}).get("avatar"), 40)
        self._refresh_subtitle()

    def _refresh_subtitle(self):
        """Строчка под названием переписки.

        Человеку важнее всего, здесь ли собеседник: печатает ли он сейчас,
        в сети ли, а если нет — когда заходил. Всё остальное (кто мы и по
        какому адресу вошли) видно слева, в углу со своим именем.
        """
        if not hasattr(self, "header_subtitle"):
            return
        self.header_subtitle.configure(text=self._presence_line())

    def _presence_line(self):
        предупреждение = "" if self.secure else t("⚠ без шифрования · ")
        item = next((c for c in self.conversations
                     if c["id"] == self.conversation), None) or {}

        if self.typing_who and time.monotonic() < self.typing_until:
            строчка = t("{name} печатает…", name=self.typing_who)
            if строчка.endswith("…"):
                # Точки бегут: сразу видно, что человек печатает прямо сейчас
                сколько = int(time.monotonic() * 2.5) % 3 + 1
                строчка = строчка[:-1] + "." * сколько
            return предупреждение + строчка

        if item.get("kind") == "direct":
            собеседник = item.get("user")
            if собеседник in self.online:
                return предупреждение + t("в сети")
            return предупреждение + t("был(а) в сети {when}",
                                      when=self._seen_text(self.seen.get(собеседник)))

        if item.get("kind") == "group":
            сколько = len(item.get("members") or [])
            if сколько:
                return предупреждение + t("участников: {count}", count=сколько)

        return предупреждение + t("вы вошли как {name}", name=self.user.get("name"))

    def _seen_text(self, stamp):
        """«только что», «вчера в 21:15», «24 августа в 22:31»."""
        if not stamp:
            return t("давно")
        когда = local_time(stamp)
        сейчас = datetime.now().astimezone()
        if (сейчас - когда).total_seconds() < 90:
            return t("только что")
        разница = (сейчас.date() - когда.date()).days
        часы = когда.strftime("%H:%M")
        if разница <= 0:
            return t("сегодня в {time}", time=часы)
        if разница == 1:
            return t("вчера в {time}", time=часы)
        if когда.year == сейчас.year:
            return t("{date} в {time}",
                     date=i18n.month_day(когда.day, когда.month), time=часы)
        return когда.strftime("%d.%m.%Y")

    def _keep_subtitle_fresh(self):
        """«только что» со временем должно становиться «сегодня в 21:15»."""
        self._refresh_subtitle()
        self.after(60000, self._keep_subtitle_fresh)

    def _clear_messages(self):
        # Голос из прошлой переписки не должен доигрывать в новой
        self._stop_voices()
        for widget in self.messages.winfo_children():
            widget.destroy()
        self.rows.clear()
        self.reaction_rows.clear()
        self.gallery.clear()
        self.last_sender = None
        self.current_date = None
        self.oldest = None
        self.empty_hint = None
        # Прошлая переписка могла быть длинной. Не пересчитав область,
        # лента останется прокрученной туда, где уже ничего нет
        self._refit_feed()
        self.messages._parent_canvas.yview_moveto(0.0)

    def _show_history(self, message):
        """Показывает пришедший кусок истории."""
        if self.conversation is None:
            # Первая переписка после входа: какую сервер прислал, ту и открыли
            self.conversation = message.get("conversation")
            self._refresh_side_list()
            self._update_header()
        elif self.pending_direct and message.get("conversation") != self.conversation:
            # Это история личной переписки, которую мы только что попросили
            self.pending_direct = False
            self.conversation = message.get("conversation")
            self._cancel_reply()
            self._refresh_side_list()
            self._update_header()
        elif message.get("conversation") != self.conversation:
            return

        self.waiting_for = None
        self.quotes.update(message.get("quotes") or {})
        for key, value in (message.get("reactions") or {}).items():
            self.reactions[int(key)] = value
        self.has_older = bool(message.get("more"))
        items = message.get("items") or []

        if message.get("before"):
            # Подгрузка старого: перерисовываем всё, чтобы порядок не сбился
            already = self.loaded_items
            self.loaded_items = items + already
        else:
            self.loaded_items = items

        self._clear_messages()
        if self.has_older:
            self.older_button = ctk.CTkButton(
                self.messages, text=t("Показать более старые"), height=30,
                corner_radius=R_ITEM, font=self.font_small, fg_color=INPUT_BG,
                hover_color=SEPARATOR, text_color=MUTED, command=self._load_older)
            self.older_button.pack(pady=(8, 4))

        if not self.loaded_items:
            self.empty_hint = self._service_label(t("Пока тихо. Напишите первым."))

        # Историю рисуем разом: проявлять два десятка пузырей по очереди —
        # это не плавность, а мельтешение
        self.drawing_history = True
        try:
            for item in self.loaded_items:
                self._show_item(item)
        finally:
            self.drawing_history = False

        self._mark_read([item["id"] for item in self.loaded_items
                         if item.get("id") and item.get("user") != self.user.get("id")])
        if self.loaded_items:
            self.oldest = self.loaded_items[0].get("id")

        # То, что пришло, кладём на диск: в следующий раз без связи это и
        # покажется
        localcache.save_history(self.server, self.conversation,
                                self.loaded_items)

    def _nudge_history(self, conversation_id, попытка, пропуск):
        """История не пришла — просим ещё раз, а потом сознаёмся.

        Пропуск отличает нынешний заход от прежних: без него сторож от
        старого захода срабатывал уже после того, как история пришла, и
        стирал только что показанную ленту.
        """
        if пропуск != self.open_token:
            return          # это сторож от прежнего захода
        if self.conversation != conversation_id or self.waiting_for is None:
            return          # уже пришла или человек ушёл в другую переписку

        if попытка <= 2 and self.network.send(
                protocol.open_request(conversation_id)):
            self.after(4000, self._nudge_history, conversation_id,
                       попытка + 1, пропуск)
            return

        # Рвать связь самим не стоит: если сокет и правда умер, это заметит
        # сама библиотека — она шлёт пинги и закроет соединение, а окно
        # переподключится. Наше дело — не оставлять человека перед пустотой.
        self._service_label(t("Сервер не ответил. Ждём связи…"))

    def _load_older(self):
        if self.oldest:
            self.network.send(protocol.open_request(self.conversation, self.oldest))

    # --------------------------------------------------------- ответ и удаление

    def _react(self, message_id, emoji):
        self.network.send(protocol.react_request(message_id, emoji))

    def _draw_reactions(self, message_id):
        """Перерисовывает строку реакций под сообщением."""
        holder = self.reaction_rows.get(message_id)
        if holder is None or not holder.winfo_exists():
            return

        for widget in holder.winfo_children():
            widget.destroy()

        summary = self.reactions.get(message_id) or {}
        if not summary:
            holder.pack_forget()
            return

        holder.pack(fill="x", padx=13, pady=(0, 4))
        for emoji, people in sorted(summary.items()):
            mine = self.user.get("id") in people
            ctk.CTkButton(
                holder, text=f"{emoji} {len(people)}", width=44, height=24,
                corner_radius=R_CARD, font=self.font_small,
                fg_color=ACCENT if mine else SEPARATOR,
                hover_color=ACCENT_HOVER if mine else MUTED,
                text_color=ON_ACCENT if mine else TEXT,
                command=lambda e=emoji, m=message_id: self._react(m, e)).pack(
                side="left", padx=(0, 4))

    def _on_reactions(self, message):
        """Пришла обновлённая сводка реакций."""
        message_id = message.get("id")
        self.reactions[message_id] = message.get("reactions") or {}
        self._draw_reactions(message_id)

    def _emoji_menu(self, message_id):
        """Маленькое окно с набором смайликов."""
        picker = ctk.CTkToplevel(self)
        picker.title(t("Реакция"))
        picker.geometry("260x70")
        picker.transient(self)
        picker.configure(fg_color=SIDEBAR)

        row = ctk.CTkFrame(picker, fg_color="transparent")
        row.pack(expand=True)
        for emoji in EMOJI:
            ctk.CTkButton(row, text=emoji, width=36, height=36, corner_radius=18,
                          font=self.font_body, fg_color=INPUT_BG,
                          hover_color=SEPARATOR, text_color=TEXT,
                          command=lambda e=emoji: (self._react(message_id, e),
                                                   picker.destroy())).pack(
                side="left", padx=3)

    def _start_reply(self, item):
        self.reply_to = item.get("id")
        who = item.get("nick", "")
        what = item.get("text") or item.get("name") or t("вложение")
        self.reply_label.configure(
            text=t("Ответ {name}: {text}", name=who, text=what[:40]))
        self.reply_bar.grid(row=3, column=0, sticky="ew")
        self.message_entry.focus_set()

    def _cancel_reply(self):
        self.reply_to = None
        self.editing = None
        self.reply_bar.grid_forget()

    def _start_edit(self, item):
        """Кладём текст обратно в строку ввода — там его и правят.

        Отдельного окошка не нужно: человек и так пишет внизу, ему привычно
        там же и поправить. Полоска сверху напоминает, что идёт правка.
        """
        self.reply_to = None
        self.editing = item.get("id")
        self.reply_label.configure(
            text=t("Правим: {text}", text=(item.get("text") or "")[:40]))
        self.reply_bar.grid(row=3, column=0, sticky="ew")
        self.message_entry.delete(0, "end")
        self.message_entry.insert(0, item.get("text") or "")
        self.message_entry.focus_set()

    def _delete_message(self, message_id):
        self.network.send(protocol.delete_request(message_id))

    def _group_menu(self, event, item):
        """Правая кнопка на группе: сменить фото, удалить."""
        if item.get("kind") != "group" or item.get("id") == protocol.GENERAL_ID:
            return

        self._close_menu()
        holder = ctk.CTkFrame(self, fg_color="transparent")
        self.menu = holder
        self.bind("<Escape>", lambda _: self._close_menu())
        self.bind_all("<Button-1>", self._click_outside_menu, add="+")
        self.bind_all("<Button-3>", self._click_outside_menu, add="+")

        card = ctk.CTkFrame(holder, fg_color=MENU_BG, corner_radius=R_CARD)
        card.pack(anchor="w")

        self._menu_row(card, "copy", t("Фото группы"),
                       lambda: self._choose_group_photo(item))
        self._menu_row(card, "forward", t("Позвать людей"),
                       lambda: self._invite_to_group(item))
        # Удалить группу может тот, кто её завёл, и хозяин чата
        if item.get("owner") == self.user.get("id") or self.is_admin:
            self._menu_row(card, "trash", t("Удалить группу"),
                           lambda: self._delete_group(item))

        self.update_idletasks()
        width = max(holder.winfo_reqwidth(), 180)
        height = holder.winfo_reqheight()
        x = min(max(event.x_root - self.winfo_rootx(), 8),
                max(self.winfo_width() - width - 8, 8))
        y = min(max(event.y_root - self.winfo_rooty(), 8),
                max(self.winfo_height() - height - 8, 8))
        holder.place(x=x, y=y)

    def _choose_group_photo(self, item):
        path = filedialog.askopenfilename(
            title=t("Выберите фото"),
            filetypes=[(t("Картинки"), "*.png *.jpg *.jpeg *.webp *.bmp"),
                       (t("Все файлы"), "*.*")])
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as error:
            self._service_label(t("Не удалось прочитать файл: {error}",
                                  error=error))
            return
        self.network.send(protocol.group_avatar_header(
            item["id"], Path(path).name, len(data)), data)

    def _invite_to_group(self, item):
        """Окошко «кого позвать» для уже заведённой группы."""
        уже = set(item.get("members") or [])
        свободные = [person for person in self.people
                     if person["id"] != self.user.get("id")
                     and person["id"] not in уже]
        if not свободные:
            self._service_label(t("Все уже в группе."))
            return

        window = ctk.CTkToplevel(self)
        window.title(t("Позвать людей"))
        window.geometry("340x420")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)
        window.after(200, window.grab_set)

        ctk.CTkLabel(window, text=t("Кого позвать в «{title}»",
                                    title=self._title_of(item)),
                     font=self.font_name, text_color=TEXT,
                     wraplength=300).pack(padx=18, pady=(18, 8))

        box = ctk.CTkScrollableFrame(window, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=12, pady=6)

        отмеченные = {}
        for person in свободные:
            переменная = tkinter.IntVar()
            подпись = f"{person['name']} · @{person.get('login', '')}"
            ctk.CTkCheckBox(box, text=подпись, variable=переменная,
                            font=self.font_body, text_color=TEXT, fg_color=ACCENT,
                            hover_color=ACCENT_HOVER).pack(anchor="w", pady=4)
            отмеченные[person["id"]] = переменная

        строка = ctk.CTkFrame(window, fg_color="transparent")
        строка.pack(fill="x", padx=18, pady=(0, 16))

        def позвать():
            кого = [номер for номер, флаг in отмеченные.items() if flag_of(флаг)]
            if кого:
                self.network.send(protocol.members_request(item["id"], кого))
            window.destroy()

        def flag_of(переменная):
            return bool(переменная.get())

        ctk.CTkButton(строка, text=t("Отмена"), height=38, corner_radius=R_ITEM,
                      font=self.font_small, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=window.destroy).pack(side="left", expand=True,
                                                   fill="x", padx=(0, 6))
        ctk.CTkButton(строка, text=t("Позвать"), height=38, corner_radius=R_ITEM,
                      font=self.font_small, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                      command=позвать).pack(side="left", expand=True, fill="x",
                                            padx=(6, 0))

    def _delete_group(self, item):
        title = self._title_of(item)
        if self._confirm(t("Удалить «{title}»?", title=title),
                         t("Переписка и вложения пропадут у всех. "
                           "Отменить это нельзя.")):
            self.network.send(protocol.delete_group_request(item["id"]))

    def _message_menu(self, event, item, own):
        """Правая кнопка на сообщении: панель с действиями и реакциями.

        Своё окно вместо системного меню Tk: там нельзя ни скруглить углы,
        ни поставить рядом полоску смайликов.
        """
        self._close_menu()

        # Меню кладём прямо в окно: подложка на весь экран закрасила бы
        # переписку, ведь «прозрачный» в CustomTkinter — это цвет родителя.
        # Щелчок мимо ловим общей привязкой и сами смотрим, куда попали.
        holder = ctk.CTkFrame(self, fg_color="transparent")
        self.menu = holder
        self.bind("<Escape>", lambda _: self._close_menu())
        self.bind_all("<Button-1>", self._click_outside_menu, add="+")
        self.bind_all("<Button-3>", self._click_outside_menu, add="+")

        if item.get("id"):
            strip = ctk.CTkFrame(holder, fg_color=MENU_BG, corner_radius=20)
            strip.pack(anchor="w", pady=(0, 6))
            for emoji in EMOJI:
                ctk.CTkButton(strip, text=emoji, width=34, height=34,
                              corner_radius=17, font=self.font_body,
                              fg_color="transparent", hover_color=MENU_HOVER,
                              text_color=TEXT,
                              command=lambda e=emoji: self._pick_reaction(item, e)
                              ).pack(side="left", padx=2, pady=3)

        card = ctk.CTkFrame(holder, fg_color=MENU_BG, corner_radius=R_CARD)
        card.pack(anchor="w")

        actions = [("reply", t("Ответить"), lambda: self._start_reply(item))]
        if item.get("id"):
            pinned = (self.pinned.get(self.conversation) or {}).get("id")
            if pinned == item["id"]:
                actions.append(("pin", t("Открепить"), self._unpin))
            else:
                actions.append(("pin", t("Закрепить"),
                                lambda: self._pin_message(item["id"])))
            actions.append(("copy", self._copy_label(item),
                            lambda: self._copy_item(item)))
            actions.append(("forward", t("Переслать"),
                            lambda: self._forward_menu(item)))
        if own and item.get("id") and item.get("kind", "text") == "text":
            actions.append(("pencil", t("Изменить"),
                            lambda: self._start_edit(item)))
        if own and item.get("id"):
            actions.append(("trash", t("Удалить"),
                            lambda: self._delete_message(item["id"])))

        for icon, label, command in actions:
            self._menu_row(card, icon, label, command)

        # Ставим у курсора, но не даём вылезти за край окна
        self.update_idletasks()
        width = max(holder.winfo_reqwidth(), 180)
        height = holder.winfo_reqheight()
        x = min(max(event.x_root - self.winfo_rootx(), 8),
                max(self.winfo_width() - width - 8, 8))
        y = min(max(event.y_root - self.winfo_rooty(), 8),
                max(self.winfo_height() - height - 8, 8))
        holder.place(x=x, y=y)

    def _menu_row(self, card, icon, label, command):
        """Строка меню: значок слева, надпись рядом, подсветка под мышью."""
        row = ctk.CTkButton(card, text=f"   {label}", image=menu_icon(icon),
                            compound="left", anchor="w",
                            height=38, corner_radius=R_SMALL, font=self.font_body,
                            fg_color="transparent", hover_color=MENU_HOVER,
                            text_color=TEXT,
                            command=lambda: (self._close_menu(), command()))
        row.pack(fill="x", padx=6, pady=2)
        return row

    def _click_outside_menu(self, event):
        """Закрывает меню, если щёлкнули мимо него."""
        if self.menu is None:
            return
        widget = event.widget
        while widget is not None:
            if widget is self.menu:
                return
            widget = getattr(widget, "master", None)
        self._close_menu()

    def _copy_label(self, item):
        """Что именно скопируется — зависит от вида сообщения."""
        kind = item.get("kind", "text")
        if kind == "text":
            return t("Копировать текст")
        if kind in ("image", "gif"):
            return t("Копировать фото")
        return t("Копировать файл")

    def _pick_reaction(self, item, emoji):
        self._close_menu()
        self._react(item["id"], emoji)

    def _close_menu(self):
        """Убирает меню, если оно открыто."""
        if self.menu is None:
            return
        self.menu.destroy()
        self.menu = None
        self.unbind("<Escape>")
        self.unbind_all("<Button-1>")
        self.unbind_all("<Button-3>")

    # ---------------------------------------------- закрепить и переслать

    def _pin_message(self, message_id):
        self.network.send(protocol.pin_request(self.conversation, message_id))

    def _unpin(self):
        self.network.send(protocol.pin_request(self.conversation, None))

    def _on_pinned(self, message):
        """Сервер сказал, что закреплено в переписке."""
        conversation = message.get("conversation")
        item = message.get("item")
        if item:
            self.pinned[conversation] = item
        else:
            self.pinned.pop(conversation, None)
        self._refresh_pin_bar()

    def _refresh_pin_bar(self):
        item = self.pinned.get(self.conversation)
        if not item:
            self.pin_bar.grid_forget()
            return

        what = item.get("text") or item.get("name") or t("вложение")
        self.pin_label.configure(text=f"{item.get('nick', '')}: {short(what, 70)}")
        self.pin_bar.grid(row=1, column=0, sticky="ew")

    def _forward_menu(self, item):
        """Куда переслать: список переписок в отдельном окне."""
        window = ctk.CTkToplevel(self)
        window.title(t("Куда переслать"))
        window.geometry("300x380")
        window.transient(self)
        window.configure(fg_color=SIDEBAR)

        ctk.CTkLabel(window, text=t("Куда переслать"), font=self.font_name,
                     text_color=TEXT).pack(pady=(16, 8))

        listing = ctk.CTkScrollableFrame(window, fg_color="transparent")
        listing.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for conversation in self.conversations:
            if conversation["id"] == self.conversation:
                continue
            ctk.CTkButton(
                listing, text=self._title_of(conversation), anchor="w", height=38,
                corner_radius=R_SMALL, font=self.font_body, fg_color=INPUT_BG,
                hover_color=SEPARATOR, text_color=TEXT,
                command=lambda c=conversation["id"]: (
                    self.network.send(protocol.forward_request(item["id"], c)),
                    window.destroy())).pack(fill="x", pady=2)

    def _copy_item(self, item):
        """Кладёт сообщение в буфер: текст — текстом, картинку — картинкой."""
        if item.get("kind", "text") == "text":
            self.clipboard_clear()
            self.clipboard_append(item.get("text", ""))
            self._service_label(t("Скопировано"))
            return

        media_id = item.get("media")
        if not media_id:
            return

        data = self.kept_media.get(media_id) or mediacache.get(media_id)
        if data is None:
            # Содержимое ещё не забирали с сервера — попросим и вернёмся сюда
            self.pending_media[media_id] = ("copy", None, item)
            self._ask_media(media_id)
            return
        self._copy_bytes(item, data)

    def _copy_bytes(self, item, data):
        """Картинка ложится в буфер картинкой, остальное — файлом.

        Гифку и видео как картинку класть нельзя: буфер хранит один кадр, и
        движение пропадёт. Файл же вставится и в чат, и в проводник.
        """
        suffix = Path(item.get("name") or "").suffix[:10]
        path = Path(tempfile.gettempdir()) / f"velix-copy-{item.get('media')}{suffix}"
        quoted = str(path).replace("'", "''")

        if item.get("kind") == "image":
            script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                      f"$picture=[System.Drawing.Image]::FromFile('{quoted}');"
                      "[System.Windows.Forms.Clipboard]::SetImage($picture)")
        else:
            script = f"Set-Clipboard -LiteralPath '{quoted}'"

        try:
            path.write_bytes(data)
            subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                           check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as error:
            self._service_label(t("Не удалось скопировать: {error}", error=error))
            return
        self._service_label(t("Скопировано"))

    def _on_edited(self, message):
        """Сообщение поправили — показываем новое, не перерисовывая ленту."""
        if message.get("conversation") != self.conversation:
            return
        message_id = message.get("id")
        for item in self.loaded_items:
            if item.get("id") == message_id:
                item["text"] = message.get("text", "")
                item["edited"] = message.get("edited")
                break

        row = self.rows.get(message_id)
        if row is None or not row.winfo_exists():
            return
        for label in self._labels_of(row):
            if getattr(label, "velix_body", False):
                label.configure(text=message.get("text", ""))
        self._mark_edited(row)

    def _labels_of(self, widget):
        """Все подписи внутри ряда, на любой глубине."""
        найдено = []
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                найдено.append(child)
            найдено.extend(self._labels_of(child))
        return найдено

    def _mark_edited(self, row):
        """Ставит пометку «изменено» рядом со временем."""
        for label in self._labels_of(row):
            if getattr(label, "velix_time", False):
                if not label.cget("text").startswith(t("изменено")):
                    label.configure(text=t("изменено") + " · " + label.cget("text"))
                return

    def _on_deleted(self, message):
        """Сообщение убрали — гасим его на месте."""
        row = self.rows.get(message.get("id"))
        if row is not None and row.winfo_exists():
            for widget in row.winfo_children():
                widget.destroy()
            ctk.CTkLabel(row, text=t("сообщение удалено"), font=self.font_small,
                         text_color=MUTED).pack(padx=13, pady=4)

    # ------------------------------------------------------------------ поиск

    def _on_search(self):
        query = self.search_entry.get().strip()
        if len(query) < 2:
            return
        self.network.send(protocol.search_request(query))

    def _show_search(self, message):
        items = message.get("items") or []
        self._clear_messages()
        self._service_label(
            t("Найдено: {count}", count=len(items)) if items
            else t("По запросу «{query}» ничего нет", query=message.get("query")))
        for item in items:
            self._show_item(item)

    # -------------------------------------------------------------- печатает

    def _on_typing(self, message):
        if message.get("conversation") != self.conversation:
            return
        self.typing_until = time.monotonic() + 3
        self.typing_who = message.get("nick")
        self._refresh_subtitle()
        self.after(3200, self._clear_typing)
        if self.typing_dots is None:
            self._dance_dots()

    def _dance_dots(self):
        """Пока человек печатает, точки в подписи бегут."""
        if self.typing_who and time.monotonic() < self.typing_until:
            self._refresh_subtitle()
            self.typing_dots = self.after(400, self._dance_dots)
        else:
            self.typing_dots = None

    def _clear_typing(self):
        if time.monotonic() >= self.typing_until:
            self.typing_who = None
            self._refresh_subtitle()

    def _notify_typing(self, event=None):
        """Сообщаем собеседникам, что набираем текст, но не чаще раза в 2 секунды."""
        now = time.monotonic()
        if now - getattr(self, "typing_sent", 0) < 2:
            return
        self.typing_sent = now
        self.network.send(protocol.typing_message(self.conversation))

    def _clear_hint(self):
        if self.empty_hint is not None:
            self.empty_hint.destroy()
            self.empty_hint = None

    def _ensure_date(self, date_text):
        """Плашка с датой — как разделители дней в Telegram."""
        if date_text == self.current_date:
            return
        self.current_date = date_text

        day, month = date_text.split(".")
        if date_text == datetime.now().strftime("%d.%m"):
            caption = t("Сегодня")
        else:
            caption = i18n.month_day(int(day), int(month))

        self._service_label(caption)
        self.last_sender = None

    def _service_label(self, text):
        """Служебная строка: скруглённая плашка по центру."""
        self._clear_hint()
        row = ctk.CTkFrame(self.messages, fg_color="transparent")
        row.pack(fill="x", pady=8)
        ctk.CTkLabel(row, text=text, font=self.font_small, text_color=MUTED,
                     fg_color=SERVICE_BG, corner_radius=R_CARD, height=26,
                     wraplength=self.wrap_length).pack(padx=14, ipadx=10)
        self._scroll_to_bottom()
        return row

    def _new_bubble(self, nickname, own, avatar=None):
        """Общая обвязка пузыря: ряд, аватарка, подпись автора."""
        grouped = self.last_sender == (nickname, own)
        self.last_sender = (nickname, own)

        row = ctk.CTkFrame(self.messages, fg_color="transparent")
        row.pack(fill="x", padx=26, pady=(2 if grouped else 7, 0))

        if not own:
            # Аватарку показываем только у первого сообщения в серии, дальше
            # оставляем отступ той же ширины — так делает Telegram
            if grouped:
                ctk.CTkFrame(row, fg_color="transparent", width=44,
                             height=1).pack(side="left")
            else:
                label = ctk.CTkLabel(row, text="", width=AVATAR_SMALL,
                                     height=AVATAR_SMALL, font=self.font_sender)
                label.pack(side="left", padx=(0, 8), anchor="s")
                self._paint_avatar(label, nickname, avatar, AVATAR_SMALL)

        цвет = BUBBLE_OUT if own else BUBBLE_IN
        bubble = ctk.CTkFrame(row, corner_radius=R_BUBBLE, fg_color=цвет)
        bubble.pack(side="right" if own else "left")

        # Пришедшее сообщение проявляется из фона. Всю ленту разом так не
        # рисуем: два десятка таймеров одновременно — это не плавность
        if not self.drawing_history:
            self._fade_widget(bubble, CHAT_BG, цвет)

        if not own and not grouped:
            ctk.CTkLabel(bubble, text=nickname, font=self.font_sender,
                         text_color=avatar_color(nickname), anchor="w").pack(
                fill="x", padx=15, pady=(8, 0))

        return bubble, grouped

    def _add_time(self, bubble, own, time_text, item=None):
        item = item or {}
        if time_text:
            line = ctk.CTkFrame(bubble, fg_color="transparent")
            line.pack(fill="x", padx=15, pady=(0, 6))
            if own:
                # Галочки: одна — сервер принял, две — дошло до всех,
                # голубые — все прочитали
                tick = ctk.CTkLabel(line, text="", font=self.font_small,
                                    text_color=TICK_SENT, width=24, anchor="e")
                tick.pack(side="right")
                self._remember_tick(item, tick)
            подпись = t("изменено") + " · " + time_text \
                if item.get("edited") else time_text
            часы = ctk.CTkLabel(line, text=подпись, font=self.font_small,
                                text_color=TIME_OUT if own else TIME_IN,
                                anchor="e")
            часы.velix_time = True
            часы.pack(side="right")

        # Полоска реакций живёт под сообщением и появляется, когда есть что показать
        message_id = (item or {}).get("id")
        if message_id:
            holder = ctk.CTkFrame(bubble, fg_color="transparent")
            self.reaction_rows[message_id] = holder
            self._draw_reactions(message_id)

    def _remember_tick(self, item, label):
        """Запоминает, где рисовать галочки, и сразу их рисует."""
        key = item.get("id") or item.get("local")
        if key is None:
            return

        self.ticks[key] = label
        state = item.get("state") or self.states.get(key)
        if state is None:
            # У своего сообщения до ответа сервера номера ещё нет
            if item.get("waiting"):
                state = "waiting"       # связи не было, лежит в очереди
            else:
                state = "sending" if item.get("id") is None else "sent"
        self._paint_tick(key, state)

    def _paint_tick(self, key, state):
        """Рисует галочки одного сообщения."""
        self.states[key] = state
        label = self.ticks.get(key)
        if label is None or not label.winfo_exists():
            return
        marks = {"waiting": "🕓", "sending": "·", "sent": "✓",
                 "delivered": "✓✓", "read": "✓✓"}
        label.configure(text=marks.get(state, "✓"),
                        text_color=TICK_READ if state == "read" else TICK_SENT)

    def _finish_upload(self, local, message):
        """Отправка закончилась: убираем строчку о ходе, показываем вложение."""
        отправка = self.sending.pop(local, None)
        if отправка is None:
            return

        строка = отправка["label"]
        if строка is not None and строка.winfo_exists():
            строка.destroy()

        запись = next((one for one in self.loaded_items
                       if one.get("local") == local), None)
        if запись is None:
            return
        запись["media"] = message.get("media")

        self._add_media_bubble(self.user.get("name", t("Я")), own=True,
                               kind=запись.get("kind"),
                               media_id=message.get("media"),
                               name=запись.get("name"),
                               size=запись.get("size"),
                               time_text=local_time(message.get("at")).strftime("%H:%M"),
                               data=None, item=запись)

    def _on_ack(self, message):
        """Сервер принял сообщение и назвал его настоящий номер."""
        local = message.get("local")
        message_id = message.get("id")
        if not message_id:
            return

        if local in self.sending:
            # Большой файл доехал: показываем его вместо строчки о ходе
            self._finish_upload(local, message)

        for item in self.loaded_items:
            if local and item.get("local") == local:
                item["id"] = message_id

        label = self.ticks.pop(local, None)
        self.states.pop(local, None)
        if label is not None:
            self.ticks[message_id] = label
        row = self.rows.pop(local, None)
        if row is not None:
            self.rows[message_id] = row
        self._paint_tick(message_id, self.states.get(message_id, "sent"))

    def _on_receipts(self, message):
        """Сообщения дошли или их прочитали."""
        for key, state in (message.get("items") or {}).items():
            try:
                self._paint_tick(int(key), state)
            except (TypeError, ValueError):
                continue

    def _mark_read(self, ids):
        """Говорит серверу, что эти сообщения прочитаны.

        Спрятанное окно не считается: человек их не видел.
        """
        if not ids or self.conversation is None:
            return
        if self.state() != "normal":
            return
        self.network.send(protocol.read_request(self.conversation, ids))

    def _add_forward_mark(self, bubble, item):
        """Строка «переслано от кого-то» над самим сообщением."""
        if not item.get("forwarded"):
            return
        ctk.CTkLabel(bubble, text=t("Переслано от {name}",
                                    name=item["forwarded"]),
                     font=self.font_small, text_color=MUTED, anchor="w").pack(
            fill="x", padx=13, pady=(4, 0))

    def _add_quote(self, bubble, item):
        """Показывает, на что отвечает это сообщение."""
        quoted = self.quotes.get(str(item.get("reply_to")))
        if not quoted:
            return
        what = quoted.get("text") or quoted.get("name") or t("вложение")
        strip = ctk.CTkFrame(bubble, fg_color=SEPARATOR, corner_radius=R_SMALL)
        strip.pack(fill="x", padx=13, pady=(6, 2))
        ctk.CTkLabel(strip, text=f"{quoted.get('nick', '')}: {what[:60]}",
                     font=self.font_small, text_color=MUTED, anchor="w",
                     wraplength=self.wrap_length - 30).pack(fill="x", padx=8, pady=4)

    def _attach_menu(self, widgets, item, own):
        """Вешает меню правой кнопки на пузырь и его начинку."""
        for widget in widgets:
            widget.bind("<Button-3>",
                        lambda event, i=item, o=own: self._message_menu(event, i, o))

    def _add_bubble(self, nickname, text, own, time_text=None, avatar=None, item=None):
        self._clear_hint()
        bubble, grouped = self._new_bubble(nickname, own, avatar)
        item = item or {}

        self._add_forward_mark(bubble, item)
        if item.get("reply_to"):
            self._add_quote(bubble, item)

        label = ctk.CTkLabel(bubble, text=text, font=self.font_body,
                             text_color=TEXT_OUT if own else TEXT, justify="left",
                             anchor="w", wraplength=self.wrap_length)
        label.velix_body = True         # эту подпись меняет правка
        label.pack(fill="x", padx=15, pady=(4 if own or grouped else 2, 0))

        bubble.velix_bubble = True      # в него же приезжает карточка ссылки
        bubble.velix_own = own
        if item.get("preview"):
            self._add_link_card(bubble, item["preview"], own)

        if self.user.get("id") in (item.get("mentions") or []):
            # Окликнули именно нас: рамка заметна, но не кричит
            bubble.configure(border_width=2, border_color=ACCENT)

        self._add_time(bubble, own, time_text, item)
        self._attach_menu((bubble, label), item, own)
        if item.get("id") or item.get("local"):
            self.rows[item.get("id") or item["local"]] = bubble.master
        self._scroll_to_bottom()

    # -------------------------------------------------------- карточка ссылки

    def _bubble_of(self, row):
        """Находит пузырь внутри ряда: карточке ссылки ехать некуда больше."""
        для_обхода = list(row.winfo_children())
        while для_обхода:
            какой = для_обхода.pop(0)
            if getattr(какой, "velix_bubble", False):
                return какой
            для_обхода.extend(какой.winfo_children())
        return None

    def _add_link_card(self, bubble, card, own):
        """Показывает ссылку карточкой: сайт, заголовок, выжимка, картинка.

        Полоска слева — как у цитаты: глазу сразу понятно, что это не текст
        сообщения, а то, что нашлось по ссылке.
        """
        if getattr(bubble, "velix_card", None) is not None:
            return          # карточка уже нарисована — второй не нужно

        ширина = self.wrap_length - 30
        обёртка = ctk.CTkFrame(bubble, fg_color=SEPARATOR,
                               corner_radius=R_ITEM)
        обёртка.pack(fill="x", padx=15, pady=(6, 2))
        bubble.velix_card = обёртка

        полоска = ctk.CTkFrame(обёртка, fg_color=ACCENT, corner_radius=R_SMALL,
                               width=3)
        полоска.pack(side="left", fill="y", padx=(4, 0), pady=4)

        внутри = ctk.CTkFrame(обёртка, fg_color="transparent")
        внутри.pack(side="left", fill="both", expand=True, padx=(8, 8), pady=6)

        подписи = []
        if card.get("site"):
            сайт = ctk.CTkLabel(внутри, text=card["site"][:60],
                                font=self.font_small, text_color=ACCENT,
                                anchor="w")
            сайт.pack(fill="x")
            подписи.append(сайт)

        if card.get("title"):
            заголовок = ctk.CTkLabel(внутри, text=card["title"],
                                     font=self.font_body, justify="left",
                                     text_color=TEXT_OUT if own else TEXT,
                                     anchor="w", wraplength=ширина)
            заголовок.pack(fill="x", pady=(1, 0))
            подписи.append(заголовок)

        if card.get("text"):
            выжимка = ctk.CTkLabel(внутри, text=card["text"][:180],
                                   font=self.font_small, text_color=MUTED,
                                   justify="left", anchor="w",
                                   wraplength=ширина)
            выжимка.pack(fill="x", pady=(1, 0))
            подписи.append(выжимка)

        if card.get("image"):
            место = ctk.CTkLabel(внутри, text="", font=self.font_small,
                                 text_color=MUTED)
            место.pack(fill="x", pady=(5, 0))
            готовое = mediacache.get(card["image"])
            if готовое:
                self._paint_link_picture(место, готовое, card)
            else:
                self.pending_media[card["image"]] = ("card", место, card)
                self._ask_media(card["image"])

        куда = card.get("url")
        if куда:
            for какая in (обёртка, внутри, *подписи):
                какая.configure(cursor="hand2")
                какая.bind("<Button-1>", lambda event, где=куда: self._open_link(где))

    def _paint_link_picture(self, holder, data, card):
        """Кладёт в карточку картинку — широкую и невысокую, как в ленте."""
        if not holder.winfo_exists():
            return
        try:
            picture = Image.open(io.BytesIO(data))
            picture.load()
            ширина = self.wrap_length - 46
            высота = max(1, round(picture.height * ширина / max(picture.width, 1)))
            высота = min(высота, 170)
            picture = picture.resize(
                (ширина, высота), Image.LANCZOS) if picture.width != ширина                 else picture
            готовая = ctk.CTkImage(light_image=picture, dark_image=picture,
                                   size=(ширина, высота))
        except Exception:
            # Картинка не открылась — карточка и без неё хороша
            holder.pack_forget()
            return

        self.images.append(готовая)
        holder.configure(image=готовая, text="", cursor="hand2")
        if card.get("url"):
            holder.bind("<Button-1>",
                        lambda event, где=card["url"]: self._open_link(где))

    def _open_link(self, куда):
        """Открывает ссылку в браузере."""
        try:
            webbrowser.open(куда)
        except Exception:
            self._service_label(t("Не получилось открыть ссылку"))

    def _on_preview(self, message):
        """Сервер сходил по ссылке — показываем карточку под сообщением."""
        if message.get("conversation") != self.conversation:
            return

        карточка = {ключ: message[ключ] for ключ in
                    ("url", "title", "text", "site", "image") if ключ in message}
        message_id = message.get("id")
        for item in self.loaded_items:
            if item.get("id") == message_id:
                item["preview"] = карточка
                # Карточка приезжает после истории — сохранённое обновляем,
                # иначе без сети от ссылки остался бы голый адрес
                self._keep_history_later()
                break

        row = self.rows.get(message_id)
        if row is None or not row.winfo_exists():
            return
        bubble = self._bubble_of(row)
        if bubble is not None:
            self._add_link_card(bubble, карточка,
                                getattr(bubble, "velix_own", False))
            self._scroll_to_bottom()

    # ----------------------------------------------------------- вложения

    def _remember_media(self, kind, media_id, name):
        """Копит список того, что можно листать в полном экране.

        Порядок — как в ленте: открыв снимок посреди переписки, человек
        ждёт, что стрелка влево покажет предыдущий, а не что-нибудь ещё.
        """
        if not media_id or kind not in ("image", "gif", "video"):
            return
        if any(one["media"] == media_id for one in self.gallery):
            return
        self.gallery.append({"media": media_id, "kind": kind,
                             "name": name or t("вложение")})

    def _add_media_bubble(self, nickname, own, kind, media_id, name, size,
                          time_text=None, data=None, avatar=None, item=None):
        self._clear_hint()
        self._remember_media(kind, media_id, name)
        bubble, _ = self._new_bubble(nickname, own, avatar)
        item = item or {}
        self._add_forward_mark(bubble, item)
        if item.get("reply_to"):
            self._add_quote(bubble, item)

        if kind in ("voice", "circle"):
            if kind == "voice":
                self._voice_card(bubble, own, media_id, name, item, data)
            else:
                self._circle_card(bubble, own, media_id, name, item, data)
            self._add_time(bubble, own, time_text, item)
            self._attach_menu((bubble,), item, own)
            if item.get("id"):
                self.rows[item["id"]] = bubble.master
            self._scroll_to_bottom()
            return

        if kind in ("image", "gif"):
            holder = ctk.CTkLabel(bubble, text=t("загружаю картинку…"),
                                  font=self.font_small, text_color=MUTED)
            holder.pack(padx=6, pady=(6, 2))

            if data is None and media_id:
                # Однажды пришедшая картинка лежит на диске: сеть не трогаем
                data = mediacache.get(media_id)

            if data is not None:
                self._show_picture(holder, kind, data, media_id)
            elif media_id:
                self.pending_media[media_id] = ("picture", holder, kind)
                self._ask_media(media_id)
            else:
                # Своё вложение, номер которого мы ещё не знаем: сервер
                # отправителю его не подтверждает
                holder.configure(text=name)
        else:
            self._file_card(bubble, own, kind, media_id, name, size, data)

        self._add_time(bubble, own, time_text, item)
        self._attach_menu((bubble,), item, own)
        if item.get("id"):
            self.rows[item["id"]] = bubble.master
        self._scroll_to_bottom()

    def _voice_card(self, bubble, own, media_id, name, item, data=None):
        """Голосовое: кнопка, полоска и время.

        Полоску рисуем сразу, ещё до того как приедут байты: длительность
        приходит вместе с описанием, и пустое место вместо сообщения
        выглядело бы поломкой.
        """
        секунд = int(item.get("seconds") or 0)
        карточка = ctk.CTkFrame(bubble, fg_color="transparent")
        карточка.pack(fill="x", padx=13, pady=(5, 2))

        кнопка = ctk.CTkButton(карточка, text="▶", width=38, height=38,
                               corner_radius=19,
                               font=ctk.CTkFont(family="Segoe UI", size=15),
                               fg_color=ACCENT, hover_color=ACCENT_HOVER,
                               text_color=ON_ACCENT)
        кнопка.pack(side="left", padx=(0, 10))

        справа = ctk.CTkFrame(карточка, fg_color="transparent")
        справа.pack(side="left", fill="x", expand=True)

        полоска = ctk.CTkProgressBar(справа, width=150, height=5,
                                     corner_radius=3, progress_color=ACCENT,
                                     fg_color=SEPARATOR)
        полоска.set(0)
        полоска.pack(fill="x", pady=(6, 3))

        часы = ctk.CTkLabel(справа, text=self._duration_text(0, секунд),
                            font=self.font_small,
                            text_color=TIME_OUT if own else TIME_IN, anchor="w")
        часы.pack(fill="x")

        состояние = {"box": None, "data": data, "seconds": секунд}
        if data is None and media_id:
            состояние["data"] = mediacache.get(media_id)
        кнопка.configure(command=lambda: self._play_voice(
            media_id, name, состояние, кнопка, полоска, часы))

    def _what_it_was(self, last):
        """Чем было последнее сообщение — строчкой для списка переписок."""
        вид = last.get("kind")
        if вид == "text":
            return last.get("text") or ""
        if вид == "voice":
            return t("голосовое")
        if вид == "circle":
            return t("кружочек")
        return t("вложение")

    def _duration_text(self, сколько, всего):
        """0:07 / 0:31 — так же, как подписано в любом проигрывателе."""
        def часы(секунд):
            секунд = max(0, int(секунд))
            return f"{секунд // 60}:{секунд % 60:02d}"

        return часы(сколько) + (f" / {часы(всего)}" if всего else "")

    def _play_voice(self, media_id, name, состояние, кнопка, полоска, часы):
        """Первое нажатие — играем, следующие — пауза и продолжение."""
        коробка = состояние.get("box")
        if коробка is not None:
            коробка.toggle()
            кнопка.configure(text="▶" if коробка.paused else "❚❚")
            return

        данные = состояние.get("data") or (mediacache.get(media_id)
                                           if media_id else None)
        if данные is None:
            if not media_id:
                return
            кнопка.configure(text="…")
            self.pending_media[media_id] = ("voice", кнопка,
                                            (состояние, полоска, часы, name))
            self._ask_media(media_id)
            return

        состояние["data"] = данные
        путь = self._video_file(media_id or name, name, данные)
        if путь is None:
            self._service_label(t("Не удалось открыть голосовое"))
            return

        # Играем по одному: два голоса разом — это каша
        self._stop_voices()

        def шаг(сейчас, всего):
            if not кнопка.winfo_exists():
                return
            длительность = всего or состояние.get("seconds") or 0
            полоска.set(min(1.0, сейчас / длительность) if длительность else 0)
            часы.configure(text=self._duration_text(сейчас, длительность))

        def конец():
            if not кнопка.winfo_exists():
                return
            кнопка.configure(text="▶")
            полоска.set(0)
            часы.configure(text=self._duration_text(
                0, состояние.get("seconds") or 0))

        коробка = videoplayer.VoiceBox(self, путь, on_tick=шаг, on_end=конец)
        if коробка.error:
            self._service_label(t("Не удалось открыть голосовое"))
            return

        состояние["box"] = коробка
        self.voices[media_id or name] = коробка
        кнопка.configure(text="❚❚")

    def _stop_voices(self):
        """Закрывает голосовые проигрыватели: SDL держит звук открытым."""
        for коробка in list(self.voices.values()):
            коробка.close()
        self.voices.clear()
        for одна in self.circles.values():
            одна.close()
        self.circles.clear()

    def _circle_card(self, bubble, own, media_id, name, item, data=None):
        """Кружочек: круглое видео прямо в ленте."""
        сторона = 200
        карточка = ctk.CTkFrame(bubble, fg_color="transparent")
        карточка.pack(padx=10, pady=(6, 2))

        фон = self._hex(BUBBLE_OUT if own else BUBBLE_IN)
        холст = tkinter.Label(карточка, text=t("кружочек"), bd=0,
                              highlightthickness=0, bg=фон,
                              fg=self._hex(MUTED),
                              width=сторона // 8, height=сторона // 16)
        холст.pack()

        секунд = int(item.get("seconds") or 0)
        подпись = ctk.CTkLabel(карточка, text=self._duration_text(0, секунд),
                               font=self.font_small,
                               text_color=TIME_OUT if own else TIME_IN)
        подпись.pack(pady=(4, 0))

        состояние = {"box": None, "data": data}
        if data is None and media_id:
            состояние["data"] = mediacache.get(media_id)

        холст.configure(cursor="hand2")
        холст.bind("<Button-1>", lambda event: self._play_circle(
            media_id, name, состояние, холст, подпись, сторона, own))

    def _play_circle(self, media_id, name, состояние, холст, подпись, сторона,
                     own):
        коробка = состояние.get("box")
        if коробка is not None:
            коробка.toggle()
            return

        данные = состояние.get("data") or (mediacache.get(media_id)
                                           if media_id else None)
        if данные is None:
            if not media_id:
                return
            холст.configure(text=t("загружаю…"))
            self.pending_media[media_id] = (
                "circle", холст, (состояние, подпись, сторона, own, name))
            self._ask_media(media_id)
            return

        состояние["data"] = данные
        путь = self._video_file(media_id or name, name, данные)
        if путь is None:
            self._service_label(t("Не удалось открыть кружочек"))
            return

        self._stop_voices()

        def шаг(коробка):
            if холст.winfo_exists():
                подпись.configure(text=self._duration_text(коробка.position,
                                                           коробка.duration))

        def конец(коробка):
            if холст.winfo_exists():
                подпись.configure(text=self._duration_text(0, коробка.duration))

        холст.configure(text="")
        коробка = videoplayer.VideoBox(
            холст, путь, (сторона, сторона), on_tick=шаг, on_end=конец,
            round_on=self._hex(BUBBLE_OUT if own else BUBBLE_IN))
        if коробка.error:
            холст.configure(text=t("кружочек"))
            self._service_label(t("Не удалось открыть кружочек"))
            return

        состояние["box"] = коробка
        self.circles[media_id or name] = коробка

    def _hex(self, цвет):
        """Цвет под текущую тему: у обычных Tk-виджетов пары цветов нет."""
        if isinstance(цвет, (tuple, list)):
            return цвет[1] if ctk.get_appearance_mode() == "Dark" else цвет[0]
        return цвет

    def _file_card(self, bubble, own, kind, media_id, name, size, data):
        """Видео и прочие файлы показываем карточкой с кнопкой «Открыть»."""
        card = ctk.CTkFrame(bubble, fg_color="transparent")
        card.pack(padx=13, pady=(4, 2))

        caption = t("Видео") if kind == "video" else t("Файл")
        ctk.CTkLabel(card, text=f"{caption} · {name}", font=self.font_body,
                     text_color=TEXT_OUT if own else TEXT, anchor="w",
                     wraplength=self.wrap_length - 40).pack(fill="x")
        ctk.CTkLabel(card, text=protocol.human_size(size or 0), font=self.font_small,
                     text_color=TIME_OUT if own else TIME_IN, anchor="w").pack(fill="x")

        button = ctk.CTkButton(card, text=t("Открыть"), height=30, corner_radius=R_SMALL,
                               font=self.font_small, fg_color=ACCENT,
                               hover_color=ACCENT_HOVER, text_color=ON_ACCENT)
        button.pack(fill="x", pady=(6, 2))

        кино = kind == "video" and videoplayer.available()
        if кино:
            button.configure(text=t("▶ Смотреть"))

        if data is not None:
            button.configure(command=(
                lambda: self._show_full(data, kind, media_id)) if кино
                else (lambda: self._open_media(name, data)))
        elif media_id:
            def request():
                button.configure(text=t("Загружаю…"), state="disabled")
                self.pending_media[media_id] = (
                    "watch" if кино else "file", button, name)
                self._ask_media(media_id)
            button.configure(command=request)
        else:
            button.configure(state="disabled")

        return card

    def _ask_media(self, media_id):
        """Просит вложение, но не больше нескольких сразу.

        Ответы идут по одному соединению, и мегабайты фотографий
        задерживают всё остальное — историю переписки в том числе.
        """
        if not media_id or media_id in self.fetch_flight \
                or media_id in self.fetch_queue:
            return
        self.fetch_queue.append(media_id)
        self._pump_fetches()

    def _pump_fetches(self):
        while self.fetch_queue and len(self.fetch_flight) < FETCH_WINDOW:
            media_id = self.fetch_queue.pop(0)
            self.fetch_flight.add(media_id)
            if not self.network.send(protocol.fetch_request(media_id)):
                self.fetch_flight.discard(media_id)
                return
            # Ответа может и не быть — например, файл на сервере пропал.
            # Через полминуты освобождаем место в очереди
            self.after(30000, self._done_fetch, media_id)

    def _done_fetch(self, media_id):
        """Вложение получено (или уже неважно) — зовём следующее."""
        if media_id in self.fetch_flight:
            self.fetch_flight.discard(media_id)
            self._pump_fetches()

    def _fill_media(self, message):
        """Пришло содержимое вложения — показываем его и кладём на диск."""
        media_id = message.get("id")
        data = message.get("data") or b""

        self._done_fetch(media_id)
        if data:
            mediacache.put(media_id, data)

        if media_id in self.avatar_waiters:
            self._fill_avatar(media_id, data)
            return

        # Картинку держим при себе: её могут попросить скопировать
        if data and len(data) <= protocol.MAX_MEDIA_SIZE // 5:
            self.kept_media[media_id] = data

        waiting = self.pending_media.pop(media_id, None)
        if waiting is None:
            return

        mode, widget, extra = waiting
        if mode == "viewer":
            if self.viewer is None:
                return          # просмотр уже закрыли — рисовать некуда
        elif mode != "copy" and (widget is None or not widget.winfo_exists()):
            # Пузырь исчез, пока картинка ехала — человек ушёл в другую
            # переписку. Рисовать в него нельзя: Tk бросит ошибку, и на
            # этом разбор пришедшего когда-то умирал навсегда
            return

        if mode == "copy":
            self._copy_bytes(extra, data)
        elif mode == "picture":
            self._show_picture(widget, extra, data, media_id)
        elif mode == "voice":
            состояние, полоска, часы, имя = extra
            if widget is not None and widget.winfo_exists():
                состояние["data"] = data
                widget.configure(text="▶")
                self._play_voice(media_id, имя, состояние, widget, полоска, часы)
        elif mode == "circle":
            состояние, подпись, сторона, own, имя = extra
            if widget is not None and widget.winfo_exists():
                состояние["data"] = data
                self._play_circle(media_id, имя, состояние, widget, подпись,
                                  сторона, own)
        elif mode == "card":
            if widget is not None and widget.winfo_exists():
                self._paint_link_picture(widget, data, extra)
        elif mode == "cell":
            if widget is not None and widget.winfo_exists():
                self._paint_cell(widget, data, media_id)
        elif mode == "viewer":
            self._viewer_arrived(media_id, data)
        elif mode == "watch":
            widget.configure(text=t("▶ Смотреть"), state="normal",
                             command=lambda: self._show_full(data, "video", media_id))
            self._show_full(data, "video", media_id)
        else:
            widget.configure(text=t("Открыть"), state="normal",
                             command=lambda: self._open_media(extra, data))
            self._open_media(extra, data)

    def _show_picture(self, holder, kind, data, media_id=None):
        """Заменяет заглушку картинкой, гифку — запускает."""
        image = None
        try:
            if kind == "gif":
                image = Image.open(io.BytesIO(data))
                frames = self._prepare_frames(image, kind)
            else:
                picture = self._thumbnail(data, media_id)
                frames = [ctk.CTkImage(light_image=picture, dark_image=picture,
                                       size=picture.size)]
        except Exception as error:
            holder.configure(
                text=t("не удалось показать картинку: {error}", error=error))
            return

        if not holder.winfo_exists():
            return          # пузырь исчез, пока готовилась картинка

        self.images.extend(frames)
        holder.configure(text="", image=frames[0], cursor="hand2")
        holder.bind("<Button-1>",
                    lambda event, bytes_=data, k=kind, m=media_id:
                    self._show_full(bytes_, k, m))

        if len(frames) > 1 and image is not None:
            delay = max(30, int(image.info.get("duration", 80)))
            self.animations[holder] = frames
            self._animate(holder, frames, 0, delay)

    def _show_full(self, data, kind, media_id=None):
        """Полный экран: снимок с приближением или ролик прямо в окне.

        Листать можно всё, что есть в этой переписке: стрелками, колесом
        по краям и кнопками. Открытое не из ленты (аватарка, например)
        листать не с чем — тогда в списке одна запись.
        """
        # Второй просмотр поверх первого не нужен: закрываем прежний
        if self.viewer is not None:
            self._close_full(self.viewer)

        self.viewer_items = [dict(one) for one in self.gallery] if media_id else []
        место = next((n for n, one in enumerate(self.viewer_items)
                      if one.get("media") == media_id), None)
        if место is None:
            self.viewer_items = [{"media": media_id, "kind": kind,
                                  "name": t("вложение"), "data": data}]
            место = 0
        else:
            self.viewer_items[место]["data"] = data
        self.viewer_at = место

        overlay = ctk.CTkFrame(self, fg_color=("#101820", "#05080c"))
        self.viewer = overlay
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._fade_widget(overlay, CHAT_BG, ("#101820", "#05080c"), 160)

        # Сцена — под всей остальной обвязкой: её содержимое меняется при
        # каждом перелистывании, а кнопки остаются на месте
        self.viewer_stage = ctk.CTkFrame(overlay, fg_color="transparent")
        self.viewer_stage.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.viewer_counter = ctk.CTkLabel(overlay, text="", font=self.font_small,
                                           text_color=MUTED, fg_color=INPUT_BG,
                                           corner_radius=R_ITEM, height=26)
        self.viewer_counter.place(relx=0.0, rely=0.0, x=20, y=20, anchor="nw")

        ctk.CTkButton(overlay, text="✕", width=36, height=36, corner_radius=18,
                      font=self.font_button, fg_color=INPUT_BG,
                      hover_color=SEPARATOR, text_color=TEXT,
                      command=lambda: self._close_full(overlay)).place(
            relx=1.0, rely=0.0, x=-20, y=20, anchor="ne")

        if len(self.viewer_items) > 1:
            for знак, куда, сдвиг, край in (("‹", 0.0, 20, "w"), ("›", 1.0, -20, "e")):
                ctk.CTkButton(overlay, text=знак, width=44, height=64,
                              corner_radius=R_BUBBLE, font=self.font_button,
                              fg_color=INPUT_BG, hover_color=SEPARATOR,
                              text_color=TEXT,
                              command=lambda шаг=1 if край == "e" else -1:
                              self._viewer_step(шаг)).place(
                    relx=куда, rely=0.5, x=сдвиг, anchor=край)

        self.bind("<Escape>", lambda event: self._close_full(overlay))
        self.bind("<Left>", lambda event: self._viewer_step(-1))
        self.bind("<Right>", lambda event: self._viewer_step(1))
        self._viewer_paint()

    def _viewer_step(self, шаг):
        """Следующее или предыдущее вложение переписки."""
        if self.viewer is None or len(self.viewer_items) < 2:
            return "break"
        self.viewer_at = (self.viewer_at + шаг) % len(self.viewer_items)
        self._viewer_paint()
        return "break"

    def _viewer_arrived(self, media_id, data):
        """Вложение доехало, пока человек смотрел на него в полном экране."""
        if self.viewer is None or not self.viewer_items:
            return
        for one in self.viewer_items:
            if one.get("media") == media_id:
                one["data"] = data
        if self.viewer_items[self.viewer_at].get("media") == media_id:
            self._viewer_paint()

    def _viewer_paint(self):
        """Рисует то вложение, на котором стоим."""
        if self.viewer is None or not self.viewer.winfo_exists():
            return
        self._stop_video()
        for widget in self.viewer_stage.winfo_children():
            widget.destroy()

        item = self.viewer_items[self.viewer_at]
        self.viewer_counter.configure(
            text=f"{self.viewer_at + 1} / {len(self.viewer_items)}"
            if len(self.viewer_items) > 1 else "")

        данные = (item.get("data") or self.kept_media.get(item.get("media"))
                  or mediacache.get(item.get("media")))
        if not данные:
            ctk.CTkLabel(self.viewer_stage, text=t("Загружаю…"),
                         font=self.font_body, text_color=MUTED).place(
                relx=0.5, rely=0.5, anchor="center")
            if item.get("media"):
                self.pending_media[item["media"]] = ("viewer", None, item["kind"])
                self._ask_media(item["media"])
            return
        item["data"] = данные

        box = (max(self.winfo_width() - 140, 240),
               max(self.winfo_height() - 160, 200))
        if item.get("kind") == "video":
            self._viewer_video(данные, item, box)
        else:
            self._viewer_picture(данные, item, box)

    def _viewer_picture(self, данные, item, box):
        """Снимок или гифка во весь экран."""
        kind = item.get("kind")
        try:
            image = Image.open(io.BytesIO(данные))
            frames = self._prepare_frames(image, kind, box)
        except Exception as error:
            ctk.CTkLabel(self.viewer_stage, font=self.font_body, text_color=MUTED,
                         text=t("не удалось показать картинку: {error}",
                                error=error)).place(relx=0.5, rely=0.5,
                                                    anchor="center")
            return

        self.images.extend(frames)
        picture = ctk.CTkLabel(self.viewer_stage, text="", image=frames[0])
        picture.place(relx=0.5, rely=0.5, anchor="center")

        if len(frames) > 1:
            delay = max(30, int(image.info.get("duration", 80)))
            self.animations[picture] = frames
            self._animate(picture, frames, 0, delay)
            # Гифку не приближаем: она и так живёт своей жизнью
            picture.configure(cursor="hand2")
            picture.bind("<Button-1>", lambda event: self._close_full(self.viewer))
            return

        self._make_zoom(self.viewer_stage, picture, данные, box)

    def _viewer_video(self, данные, item, box):
        """Ролик прямо в окне: кадры, звук, пауза и перемотка."""
        путь = self._video_file(item.get("media"), item.get("name"), данные)
        if путь is None or not videoplayer.available():
            ctk.CTkButton(self.viewer_stage, text=t("Открыть"), height=36,
                          corner_radius=R_ITEM, font=self.font_button, fg_color=ACCENT,
                          hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                          command=lambda: self._open_media(
                              item.get("name") or "video.mp4", данные)).place(
                relx=0.5, rely=0.5, anchor="center")
            return

        экран = tkinter.Label(self.viewer_stage, background="#05080c", text="",
                              borderwidth=0, highlightthickness=0)
        экран.place(relx=0.5, rely=0.45, anchor="center")

        пульт = ctk.CTkFrame(self.viewer_stage, fg_color=INPUT_BG, corner_radius=R_BUBBLE)
        пульт.place(relx=0.5, rely=1.0, y=-20, anchor="s")

        играть = ctk.CTkButton(пульт, text="⏸", width=40, height=32,
                               corner_radius=R_ITEM, font=self.font_button,
                               fg_color=SEPARATOR, hover_color=ACCENT,
                               text_color=TEXT)
        играть.pack(side="left", padx=(10, 6), pady=8)

        полоса = ctk.CTkSlider(пульт, from_=0, to=1, width=320, height=16,
                               button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                               progress_color=ACCENT)
        полоса.set(0)
        полоса.pack(side="left", padx=6, pady=8)

        часы = ctk.CTkLabel(пульт, text="0:00 / 0:00", font=self.font_small,
                            text_color=MUTED, width=92)
        часы.pack(side="left", padx=6, pady=8)

        ctk.CTkLabel(пульт, text="🔊", font=self.font_small,
                     text_color=MUTED).pack(side="left", padx=(6, 0), pady=8)
        громкость = ctk.CTkSlider(пульт, from_=0, to=1, width=80, height=16,
                                  button_color=ACCENT,
                                  button_hover_color=ACCENT_HOVER,
                                  progress_color=ACCENT)
        громкость.set(1.0)
        громкость.pack(side="left", padx=(4, 12), pady=8)

        тянут = [False]           # держит ли человек полосу перемотки

        def часики(секунды):
            секунды = max(int(секунды or 0), 0)
            return f"{секунды // 60}:{секунды % 60:02d}"

        def на_кадре(игрок):
            if not часы.winfo_exists():
                return
            if игрок.duration > 0 and not тянут[0]:
                полоса.set(min(игрок.position / игрок.duration, 1.0))
            часы.configure(text=f"{часики(игрок.position)} / "
                                f"{часики(игрок.duration)}")

        def в_конце(игрок):
            if играть.winfo_exists():
                играть.configure(text="⟳")

        def переключить(event=None):
            if self.video is None:
                return
            self.video.toggle()
            играть.configure(text="⏵" if self.video.paused else "⏸")

        def взяли_полосу(event):
            тянут[0] = True

        def отпустили_полосу(event):
            тянут[0] = False
            if self.video is not None:
                self.video.seek_to(полоса.get())
                играть.configure(text="⏵" if self.video.paused else "⏸")

        играть.configure(command=переключить)
        полоса.bind("<Button-1>", взяли_полосу, add="+")
        полоса.bind("<ButtonRelease-1>", отпустили_полосу, add="+")
        громкость.configure(command=lambda значение: self.video
                            and self.video.set_volume(значение))
        экран.configure(cursor="hand2")
        экран.bind("<Button-1>", переключить)
        self.bind("<space>", переключить)

        self.video = videoplayer.VideoBox(экран, путь, box, on_tick=на_кадре,
                                          on_end=в_конце)
        if self.video.error:
            экран.destroy()
            пульт.destroy()
            ctk.CTkLabel(self.viewer_stage, font=self.font_body, text_color=MUTED,
                         text=t("не удалось показать видео: {error}",
                                error=self.video.error)).place(
                relx=0.5, rely=0.5, anchor="center")

    def _video_file(self, media_id, name, данные):
        """Кладёт ролик во временный файл: проигрывателю нужен путь."""
        folder = Path(tempfile.gettempdir()) / "velix"
        try:
            folder.mkdir(exist_ok=True)
            основа = "".join(буква for буква in str(media_id or "")
                             if буква.isalnum())[:40] or "video"
            путь = folder / f"{основа}{Path(name or '').suffix[:8] or '.mp4'}"
            if not путь.exists() or путь.stat().st_size != len(данные):
                путь.write_bytes(данные)
        except OSError:
            return None
        return путь

    def _stop_video(self):
        if self.video is not None:
            self.video.close()
            self.video = None

    def _make_zoom(self, overlay, picture, data, box):
        """Приближение картинки: колесо, перетаскивание, кнопки и клавиши.

        Рисуем только тот кусок снимка, который сейчас виден. Растянуть
        картинку целиком на восемь размеров — это сотни мегабайт в памяти
        ради того, что всё равно не поместится на экране.
        """
        try:
            whole = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            return

        ширина, высота = box
        # Во сколько раз снимок ужат, чтобы влезть в окно целиком
        поместилось = min(ширина / whole.width, высота / whole.height, 1.0)

        # cx и cy — куда смотрим, в долях снимка
        state = {"scale": 1.0, "cx": 0.5, "cy": 0.5, "grab": None}

        подпись = ctk.CTkLabel(overlay, text="100%", font=self.font_small,
                               text_color=MUTED, fg_color=INPUT_BG,
                               corner_radius=R_ITEM, width=64, height=26)
        подпись.place(relx=0.5, rely=1.0, y=-24, anchor="s")

        def нарисовать():
            масштаб = поместилось * state["scale"]

            # Какой кусок снимка помещается в окно при таком приближении
            видно_ш = min(whole.width, ширина / масштаб)
            видно_в = min(whole.height, высота / масштаб)

            левый = min(max(state["cx"] * whole.width - видно_ш / 2, 0),
                        whole.width - видно_ш)
            верхний = min(max(state["cy"] * whole.height - видно_в / 2, 0),
                          whole.height - видно_в)
            state["cx"] = (левый + видно_ш / 2) / whole.width
            state["cy"] = (верхний + видно_в / 2) / whole.height

            кусок = whole.crop((int(левый), int(верхний),
                                int(левый + видно_ш), int(верхний + видно_в)))
            размер = (max(int(видно_ш * масштаб), 16),
                      max(int(видно_в * масштаб), 16))
            готовое = кусок.resize(размер, Image.LANCZOS)

            картинка = ctk.CTkImage(light_image=готовое, dark_image=готовое,
                                    size=размер)
            self.images.append(картинка)
            picture.configure(image=картинка)
            подпись.configure(text=f"{int(масштаб * 100)}%")

        def приблизить(во_сколько):
            прежний = state["scale"]
            state["scale"] = min(max(прежний * во_сколько, 0.25), 8.0)
            if state["scale"] != прежний:
                нарисовать()

        def целиком():
            state.update(scale=1.0, cx=0.5, cy=0.5)
            нарисовать()

        def колесом(event):
            приблизить(1.25 if event.delta > 0 else 1 / 1.25)
            return "break"

        def взять(event):
            state["grab"] = (event.x_root, event.y_root, False)

        def тянуть(event):
            if state["grab"] is None:
                return
            начало_x, начало_y, _ = state["grab"]
            сдвиг_x, сдвиг_y = event.x_root - начало_x, event.y_root - начало_y
            if abs(сдвиг_x) < 3 and abs(сдвиг_y) < 3:
                return

            state["grab"] = (event.x_root, event.y_root, True)
            масштаб = поместилось * state["scale"]
            state["cx"] -= сдвиг_x / (whole.width * масштаб)
            state["cy"] -= сдвиг_y / (whole.height * масштаб)
            нарисовать()

        def отпустить(event):
            # Щелчок без перетаскивания на целой картинке — закрыть просмотр
            тащили = state["grab"] is not None and state["grab"][2]
            state["grab"] = None
            if not тащили and state["scale"] <= 1.0:
                self._close_full(self.viewer)

        кнопки = ctk.CTkFrame(overlay, fg_color="transparent")
        кнопки.place(relx=0.5, rely=1.0, y=-60, anchor="s")
        for надпись, дело in (("−", lambda: приблизить(1 / 1.4)),
                              ("1:1", целиком),
                              ("+", lambda: приблизить(1.4))):
            ctk.CTkButton(кнопки, text=надпись, width=44, height=32,
                          corner_radius=R_ITEM, font=self.font_button,
                          fg_color=INPUT_BG, hover_color=SEPARATOR,
                          text_color=TEXT, command=дело).pack(side="left", padx=4)

        picture.configure(cursor="fleur")
        picture.bind("<MouseWheel>", колесом)
        picture.bind("<Button-1>", взять)
        picture.bind("<B1-Motion>", тянуть)
        picture.bind("<ButtonRelease-1>", отпустить)
        picture.bind("<Double-Button-1>",
                     lambda event: приблизить(2.0) if state["scale"] <= 1.0
                     else целиком())
        overlay.bind("<MouseWheel>", колесом)
        overlay.bind("<Button-1>", lambda event: self._close_full(self.viewer))

        self.bind("<Escape>", lambda event: self._close_full(self.viewer))
        self.bind("<plus>", lambda event: приблизить(1.4))
        self.bind("<minus>", lambda event: приблизить(1 / 1.4))
        self.zoom = state          # проверкам нужно видеть, что происходит
        нарисовать()


    def _close_full(self, overlay):
        """Закрывает просмотр вложения."""
        self._stop_video()
        self.viewer = None
        self.viewer_items = []
        self.zoom = None
        for клавиша in ("<Escape>", "<plus>", "<minus>", "<Left>", "<Right>",
                        "<space>"):
            self.unbind(клавиша)
        for widget in list(self.animations):
            if not widget.winfo_exists():
                self.animations.pop(widget, None)
        overlay.destroy()

    def _thumbnail(self, data, media_id):
        """Уменьшенная картинка для пузыря — по возможности готовая.

        Разобрать снимок с телефона и ужать его стоит десятков миллисекунд;
        на два десятка фотографий это уже заметная пауза при каждом входе в
        переписку. Поэтому готовую копию держим на диске.
        """
        if media_id:
            готовое = mediacache.get_thumb(media_id)
            if готовое is not None:
                try:
                    return Image.open(io.BytesIO(готовое)).convert("RGBA")
                except Exception:
                    pass          # копия испортилась — сделаем заново

        picture = Image.open(io.BytesIO(data)).convert("RGBA")
        picture.thumbnail(MAX_PICTURE, Image.LANCZOS)

        if media_id:
            holder = io.BytesIO()
            picture.save(holder, "PNG")
            mediacache.put_thumb(media_id, holder.getvalue())
        return picture

    def _prepare_frames(self, image, kind, box=MAX_PICTURE):
        """Готовит кадры: обычной картинке — один, гифке — все по очереди."""
        if kind != "gif":
            picture = image.convert("RGBA")
            picture.thumbnail(box, Image.LANCZOS)
            return [ctk.CTkImage(light_image=picture, dark_image=picture,
                                 size=picture.size)]

        frames = []
        for frame in ImageSequence.Iterator(image):
            picture = frame.convert("RGBA")
            picture.thumbnail(box, Image.LANCZOS)
            frames.append(ctk.CTkImage(light_image=picture, dark_image=picture,
                                       size=picture.size))
            if len(frames) >= MAX_GIF_FRAMES:
                break
        return frames or [ctk.CTkImage(light_image=image.convert("RGBA"),
                                       dark_image=image.convert("RGBA"))]

    def _animate(self, holder, frames, index, delay):
        """Крутит кадры гифки, пока пузырь жив."""
        if not holder.winfo_exists() or self.animations.get(holder) is not frames:
            return
        holder.configure(image=frames[index])
        self.after(delay, self._animate, holder, frames, (index + 1) % len(frames), delay)

    def _open_media(self, name, data):
        """Сохраняет вложение во временный файл и открывает системным плеером."""
        folder = Path(tempfile.gettempdir()) / "velix"
        folder.mkdir(exist_ok=True)
        path = folder / name
        try:
            path.write_bytes(data)
            open_in_system(path)
        except OSError as error:
            self._service_label(t("Не удалось открыть файл: {error}", error=error))

    def _refit_feed(self):
        """Пересчитывает область прокрутки ленты по её нынешнему росту.

        CustomTkinter трогает область только тогда, когда сама лента
        меняет размер. Уйдя из переписки с высокими картинками в
        короткую личную, область оставалась прежней — в тысячи пикселей.
        Лента, прокрученная вниз, вставала далеко под последним
        сообщением, и человек видел пустоту, хотя всё уже пришло.
        """
        полотно = self.messages._parent_canvas
        область = полотно.bbox("all")
        if область:
            полотно.configure(scrollregion=область)
        return область

    def _scroll_to_bottom(self):
        self.messages.update_idletasks()
        self._refit_feed()
        self._stop_glide(self.messages._parent_canvas)
        self.messages._parent_canvas.yview_moveto(1.0)


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    VelixApp().mainloop()


if __name__ == "__main__":
    main()
