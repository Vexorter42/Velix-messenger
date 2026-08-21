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
import time
import tkinter
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import websockets
from PIL import Image, ImageDraw, ImageSequence

import autostart
import protocol
import store
import tray as tray_module
import updates
import version

PORT = 8765

# Картинку в чате шире этого не показываем — иначе пузырь распирает окно
MAX_PICTURE = (360, 360)

# Кадров в гифке берём не больше: длинные ролики иначе съедают память
MAX_GIF_FRAMES = 120

AVATAR_SMALL = 36
AVATAR_LARGE = 96

# Палитра снята с Telegram Desktop. Пары — (светлая тема, тёмная тема),
# CustomTkinter сам подставит нужную половину.
SIDEBAR = ("#ffffff", "#17212b")
SIDEBAR_ACTIVE = ("#419fd9", "#2b5278")
CHAT_BG = ("#e6ebf0", "#0e1621")
COMPOSER = ("#ffffff", "#17212b")
INPUT_BG = ("#f1f3f5", "#242f3d")
BUBBLE_IN = ("#ffffff", "#182533")
BUBBLE_OUT = ("#effdde", "#2b5278")
TEXT = ("#000000", "#ffffff")
TEXT_OUT = ("#000000", "#ffffff")
MUTED = ("#707579", "#708499")
TIME_IN = ("#a1aab3", "#6d7f8f")
TIME_OUT = ("#62ad5a", "#7da8d3")
ACCENT = ("#3390ec", "#5288c1")
ACCENT_HOVER = ("#2b7fd4", "#3f6d9e")
SEPARATOR = ("#dfe4e9", "#1b2836")
SERVICE_BG = ("#ffffff", "#1b2836")
ON_ACCENT = "#ffffff"
ONLINE = ("#31a24c", "#4dc866")
OFFLINE = ("#d1435b", "#ec5f75")

AVATAR_COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862",
                 "#6ec9cb", "#65aadd", "#ee7aae"]

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]

KIND_LABEL = {"video": "Видео", "file": "Файл"}

# Что можно поставить на сообщение — короткий набор, как в Telegram
EMOJI = ["👍", "❤", "😂", "🔥", "😢", "👎"]

DEFAULT_SETTINGS = {
    "theme": "dark",
    "tray": True,        # закрытие окна прячет его в трей, а не выходит
    "autostart": False,  # запуск вместе с Windows
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
        self.websocket = None

    def connect(self, uris):
        if isinstance(uris, str):
            uris = [uris]
        threading.Thread(target=self._run, args=(list(uris),), daemon=True).start()

    def send(self, frame, payload=None):
        """Отправляет кадр, при необходимости следом двоичный."""
        if self.websocket is None or self.loop is None:
            return

        async def deliver(websocket):
            await websocket.send(frame)
            if payload is not None:
                await websocket.send(payload)

        asyncio.run_coroutine_threadsafe(deliver(self.websocket), self.loop)

    def disconnect(self):
        if self.websocket is not None and self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.websocket.close(), self.loop)

    def _run(self, uris):
        # Цикл держим в локальной переменной и только потом публикуем в self:
        # если пользователь успел переподключиться, старый поток не должен
        # закрыть цикл нового — тот ещё работает.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
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
                        # За описанием вложения сразу идёт кадр с содержимым
                        message["data"] = await websocket.recv()
                    self.events.put(("message", message))

        except ConnectionRefusedError:
            return False, (0, "Сервер недоступен. Проверьте, запущен ли он.")
        except ssl.SSLCertVerificationError:
            return False, (0, "Сертификат сервера выписан на другое имя. "
                              "Проверьте, правильно ли введён адрес.")
        except ssl.SSLError:
            return False, (1, "Сервер не принял защищённое соединение.")
        except socket.gaierror:
            return False, (0, "Не удалось найти сервер по этому адресу.")
        except OSError as error:
            return False, (2, f"Не удалось подключиться: {error}")
        except websockets.exceptions.ConnectionClosed:
            pass
        except websockets.exceptions.InvalidStatus as error:
            # Сервер может пускать только по определённому имени и отвечать 403
            if error.response.status_code == 403:
                self.events.put(("error", "Сервер не принимает подключение по этому "
                                          "адресу. Проверьте, что он введён точно."))
            else:
                self.events.put(("error", f"Сервер ответил кодом {error.response.status_code}."))
            return True, None
        except websockets.exceptions.InvalidMessage:
            return False, (1, "По этому адресу отвечает не Velix. "
                              "Проверьте адрес и порт.")
        except websockets.exceptions.WebSocketException as error:
            return False, (2, f"Ошибка соединения: {error}")
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
        # Тему поднимаем из настроек до того, как построим экраны
        ctk.set_appearance_mode(self.settings.get("theme", "dark"))

        self.tray = tray_module.Tray(on_open=lambda: self.events.put(("tray_open", None)),
                                     on_quit=lambda: self.events.put(("tray_quit", None)),
                                     icon_path=str(resource_path("icon.ico")))
        self.hidden_notice_shown = False
        self.available_update = None   # что сервер предлагает поставить
        self.secure = False            # идёт ли соединение по TLS

        self.conversation = 1          # какая переписка открыта
        self.conversations = []        # что показывать в списке слева
        self.people = []               # все участники
        self.online = set()            # кто сейчас в сети
        self.reply_to = None           # на какое сообщение отвечаем
        self.quotes = {}               # выжимки цитируемых сообщений
        self.rows = {}                 # номер сообщения -> его ряд в ленте
        self.oldest = None             # самое старое загруженное сообщение
        self.has_older = False         # есть ли что подгружать выше
        self.typing_until = 0          # до какого времени показывать «печатает»
        self.pending_direct = False    # ждём номер только что созданной личной
        self.reactions = {}            # номер сообщения -> {смайлик: [кто]}
        self.reaction_rows = {}        # где рисовать реакции у сообщения
        self.loaded_items = []         # что сейчас показано в ленте

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

        card = ctk.CTkFrame(self.auth_view, fg_color=SIDEBAR, corner_radius=16)
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

        self.server_entry = self._entry(self.form, "Адрес сервера")
        self.login_entry = self._entry(self.form, "Логин")
        self.password_entry = self._entry(self.form, "Пароль", show="•")
        self.name_entry = self._entry(self.form, "Как вас зовут")
        self.invite_entry = self._entry(self.form, "Код приглашения")

        self.primary_button = ctk.CTkButton(
            self.form, text="ВОЙТИ", width=300, height=46, corner_radius=10,
            font=self.font_button, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ON_ACCENT, command=self._on_primary)

        self.switch_button = ctk.CTkButton(
            self.form, text="Создать аккаунт", width=300, height=32,
            corner_radius=8, font=self.font_small, fg_color="transparent",
            hover_color=INPUT_BG, text_color=ACCENT, command=self._toggle_mode)

        self.back_button = ctk.CTkButton(
            card, text="К списку аккаунтов", width=300, height=32,
            corner_radius=8, font=self.font_small, fg_color="transparent",
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

    def _entry(self, master, placeholder, show=None):
        entry = ctk.CTkEntry(
            master, placeholder_text=placeholder, width=300, height=46,
            corner_radius=10, border_width=1, border_color=SEPARATOR,
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
        self.auth_subtitle.configure(text="Выберите аккаунт")

        for account in accounts[:6]:
            self._account_row(account)

        ctk.CTkButton(self.saved_box, text="Войти в другой аккаунт", width=300,
                      height=38, corner_radius=10, font=self.font_small,
                      fg_color="transparent", hover_color=INPUT_BG,
                      text_color=ACCENT,
                      command=lambda: self._show_form(register=False)).pack(pady=(6, 0))

    def _account_row(self, account):
        row = ctk.CTkFrame(self.saved_box, fg_color=INPUT_BG, corner_radius=10,
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

        ctk.CTkButton(row, text="✕", width=28, height=28, corner_radius=8,
                      font=self.font_small, fg_color="transparent",
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=lambda: self._forget(account)).pack(side="right", padx=(0, 8))

        for widget in (row, lines):
            widget.bind("<Button-1>", lambda event, item=account: self._enter_saved(item))
        for child in lines.winfo_children():
            child.bind("<Button-1>", lambda event, item=account: self._enter_saved(item))

    def _show_form(self, register):
        """Показывает форму входа или регистрации."""
        self.register_mode = register
        self.saved_box.pack_forget()
        self.form.pack(padx=48, fill="x", before=self.auth_error)

        for entry in (self.server_entry, self.login_entry, self.password_entry,
                      self.name_entry, self.invite_entry):
            entry.pack_forget()
        self.primary_button.pack_forget()
        self.switch_button.pack_forget()

        self.server_entry.pack(pady=(0, 10))
        self.login_entry.pack(pady=(0, 10))
        self.password_entry.pack(pady=(0, 10))
        if register:
            self.name_entry.pack(pady=(0, 10))
            self.invite_entry.pack(pady=(0, 10))

        self.primary_button.configure(text="СОЗДАТЬ АККАУНТ" if register else "ВОЙТИ")
        self.primary_button.pack(pady=(6, 6))
        self.switch_button.configure(text="У меня уже есть аккаунт" if register
                                     else "Создать аккаунт")
        self.switch_button.pack()

        self.auth_subtitle.configure(
            text="Нужен код приглашения" if register else "Вход в аккаунт")
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
            buttons, text="Профиль", width=70, height=30, corner_radius=8,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._show_profile)
        self.profile_button.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.settings_button = ctk.CTkButton(
            buttons, text="Настройки", width=70, height=30, corner_radius=8,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._show_settings)
        self.settings_button.pack(side="left", expand=True, fill="x", padx=4)

        self.leave_button = ctk.CTkButton(
            buttons, text="Сменить", width=70, height=30, corner_radius=8,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._on_leave)
        self.leave_button.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.search_entry = ctk.CTkEntry(
            sidebar, placeholder_text="Поиск по переписке", height=34,
            corner_radius=10, border_width=0, fg_color=INPUT_BG, text_color=TEXT,
            placeholder_text_color=MUTED, font=self.font_small)
        self.search_entry.pack(fill="x", padx=14, pady=(0, 8))
        self.search_entry.bind("<Return>", lambda event: self._on_search())
        self.search_entry.bind("<Control-KeyPress>", self._on_entry_shortcut)

        # Список переписок и участников: и то и другое живёт в одной колонке
        self.side_list = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=SEPARATOR, scrollbar_button_hover_color=MUTED)
        self.side_list.pack(fill="both", expand=True, padx=4)

    def _build_conversation(self):
        main = ctk.CTkFrame(self.chat_view, fg_color=CHAT_BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
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
        self.header_title = ctk.CTkLabel(titles, text="Общий чат", font=self.font_name,
                                         text_color=TEXT)
        self.header_title.pack(anchor="w")
        self.header_subtitle = ctk.CTkLabel(titles, text="", font=self.font_small,
                                            text_color=MUTED)
        self.header_subtitle.pack(anchor="w")

        self.status_dot = ctk.CTkLabel(header, text="●", font=self.font_small,
                                       text_color=ONLINE, width=14)
        self.status_dot.grid(row=0, column=2, padx=(0, 20))

        self.messages = ctk.CTkScrollableFrame(
            main, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=SEPARATOR, scrollbar_button_hover_color=MUTED)
        self.messages.grid(row=1, column=0, sticky="nsew")

        # Полоска «отвечаем на …» появляется над строкой ввода
        self.reply_bar = ctk.CTkFrame(main, fg_color=INPUT_BG, corner_radius=0)
        self.reply_label = ctk.CTkLabel(self.reply_bar, text="", font=self.font_small,
                                        text_color=MUTED, anchor="w")
        self.reply_label.pack(side="left", padx=(18, 8), pady=6)
        ctk.CTkButton(self.reply_bar, text="✕", width=28, height=24, corner_radius=8,
                      font=self.font_small, fg_color="transparent",
                      hover_color=SEPARATOR, text_color=MUTED,
                      command=self._cancel_reply).pack(side="right", padx=(0, 18))

        composer = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0)
        composer.grid(row=3, column=0, sticky="ew")
        composer.grid_columnconfigure(1, weight=1)

        self.attach_button = ctk.CTkButton(
            composer, text="+", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=22), fg_color=INPUT_BG,
            hover_color=SEPARATOR, text_color=MUTED, command=self._on_attach)
        self.attach_button.grid(row=0, column=0, padx=(18, 8), pady=13)

        self.message_entry = ctk.CTkEntry(
            composer, placeholder_text="Написать сообщение…", height=44,
            corner_radius=22, border_width=0, fg_color=INPUT_BG, text_color=TEXT,
            placeholder_text_color=MUTED, font=self.font_body)
        self.message_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=13)
        self.message_entry.bind("<Return>", lambda event: self._on_send())
        # Ctrl+V ловим по коду клавиши, а не по букве: при русской раскладке
        # событие <Control-v> просто не приходит
        self.message_entry.bind("<Control-KeyPress>", self._on_ctrl_key)
        self.message_entry.bind("<KeyRelease>", self._notify_typing)

        self.send_button = ctk.CTkButton(
            composer, text="➤", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=16), fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self._on_send)
        self.send_button.grid(row=0, column=2, padx=(0, 18), pady=13)

    def _build_profile_view(self):
        self.profile_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.profile_view, fg_color=SIDEBAR, corner_radius=16)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="Профиль", font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(32, 18))

        self.profile_avatar = ctk.CTkLabel(
            card, text="?", font=self.font_big_avatar, text_color=ON_ACCENT,
            fg_color=ACCENT, corner_radius=AVATAR_LARGE // 2,
            width=AVATAR_LARGE, height=AVATAR_LARGE)
        self.profile_avatar.pack(padx=48)
        self.profile_avatar.bind("<Button-1>", lambda event: self._choose_avatar())

        ctk.CTkButton(card, text="Сменить фото", width=300, height=32,
                      corner_radius=8, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=ACCENT,
                      command=self._choose_avatar).pack(padx=48, pady=(8, 16))

        self.profile_name = self._entry(card, "Как вас зовут")
        self.profile_name.pack(padx=48, pady=(0, 10))
        self.profile_name.bind("<Control-KeyPress>", self._on_entry_shortcut)

        self.profile_bio = ctk.CTkTextbox(
            card, width=300, height=90, corner_radius=10, border_width=1,
            border_color=SEPARATOR, fg_color=INPUT_BG, text_color=TEXT,
            font=self.font_body, wrap="word")
        self.profile_bio.pack(padx=48, pady=(0, 4))

        self.profile_hint = ctk.CTkLabel(card, text="Пара слов о себе",
                                         font=self.font_small, text_color=MUTED)
        self.profile_hint.pack(padx=48, pady=(0, 14))

        ctk.CTkButton(card, text="СОХРАНИТЬ", width=300, height=46,
                      corner_radius=10, font=self.font_button, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                      command=self._save_profile).pack(padx=48)

        ctk.CTkButton(card, text="Назад в чат", width=300, height=32,
                      corner_radius=8, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=MUTED,
                      command=self._show_chat).pack(padx=48, pady=(8, 30))

    def _build_settings_view(self):
        self.settings_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.settings_view, fg_color=SIDEBAR, corner_radius=16)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="Настройки", font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(32, 22))

        self.theme_switch = self._switch(card, "Тёмное оформление", self._on_theme_switch)
        self.tray_switch = self._switch(card, "Прятать в трей при закрытии",
                                        self._on_tray_switch)
        self.autostart_switch = self._switch(card, "Запускать вместе с Windows",
                                             self._on_autostart_switch)

        self.settings_hint = ctk.CTkLabel(card, text="", font=self.font_small,
                                          text_color=MUTED, wraplength=300,
                                          justify="left")
        self.settings_hint.pack(padx=48, pady=(6, 12))

        ctk.CTkFrame(card, height=2, corner_radius=0, fg_color=SEPARATOR).pack(
            fill="x", padx=48, pady=(0, 12))

        self.version_label = ctk.CTkLabel(card, text=f"Версия {version.VERSION}",
                                          font=self.font_small, text_color=MUTED)
        self.version_label.pack(padx=48)

        self.update_button = ctk.CTkButton(
            card, text="Обновлений нет", width=300, height=38, corner_radius=10,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, state="disabled", command=self._on_update)
        self.update_button.pack(padx=48, pady=(8, 4))

        ctk.CTkButton(card, text="Назад в чат", width=300, height=32,
                      corner_radius=8, font=self.font_small, fg_color="transparent",
                      hover_color=INPUT_BG, text_color=MUTED,
                      command=self._show_chat).pack(padx=48, pady=(0, 30))

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
        # Спрашиваем реестр, а не свою память: пользователь мог убрать
        # автозапуск и мимо нас
        self._set_switch(self.autostart_switch, autostart.is_enabled())

        if not autostart.supported():
            self.autostart_switch.configure(state="disabled")
        if not self.tray.available:
            self.tray_switch.configure(state="disabled")
        self.settings_hint.configure(text=self._settings_hint(), text_color=MUTED)
        self._refresh_update_button()

    def _settings_hint(self):
        if not self.tray.available:
            return "Значок в трее недоступен: не установлен pystray."
        if not autostart.supported():
            return "Автозапуск настраивается только в Windows."
        return "Настройки сохраняются сразу."

    def _set_switch(self, switch, on):
        switch.select() if on else switch.deselect()

    # -------------------------------------------------------- обновление

    def _refresh_update_button(self):
        """Приводит кнопку к тому, что сейчас предлагает сервер."""
        offer = self.available_update
        if not offer or not version.is_newer(offer.get("version")):
            self.update_button.configure(text="У вас последняя версия",
                                         state="disabled", fg_color=INPUT_BG,
                                         text_color=MUTED)
            return

        size = protocol.human_size(offer.get("size") or 0)
        if not updates.running_as_exe():
            self.update_button.configure(
                text=f"Есть версия {offer['version']} — обновитесь через git",
                state="disabled", fg_color=INPUT_BG, text_color=MUTED)
            return

        self.update_button.configure(text=f"Обновить до {offer['version']} · {size}",
                                     state="normal", fg_color=ACCENT,
                                     text_color=ON_ACCENT)

    def _on_update(self):
        """Просит у сервера свежую сборку."""
        if self.network.websocket is None:
            self.settings_hint.configure(text="Нет связи с сервером.",
                                         text_color=OFFLINE)
            return
        self.update_button.configure(text="Загружаю…", state="disabled",
                                     fg_color=INPUT_BG, text_color=MUTED)
        self.network.send(protocol.update_request())

    def _install_update(self, message):
        """Пришла новая сборка — подменяем себя и перезапускаемся."""
        data = message.get("data") or b""
        if not data:
            self.settings_hint.configure(text="Сервер прислал пустой файл.",
                                         text_color=OFFLINE)
            self._refresh_update_button()
            return

        problem = updates.swap(updates.executable_path(), data)
        if problem:
            self.settings_hint.configure(text=problem, text_color=OFFLINE)
            self._refresh_update_button()
            return

        self.settings_hint.configure(text="Обновление установлено, перезапускаюсь…",
                                     text_color=ONLINE)
        self.update_button.configure(text="Перезапуск…", state="disabled")
        self.update()

        problem = updates.restart()
        if problem:
            self.settings_hint.configure(text=problem, text_color=OFFLINE)
            return
        self._quit()

    def _save_settings(self):
        self.config_data["settings"] = self.settings
        store.save(self.config_data)

    def _on_theme_switch(self):
        theme = "dark" if self.theme_switch.get() else "light"
        self.settings["theme"] = theme
        ctk.set_appearance_mode(theme)
        self._save_settings()

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
            text="Velix будет запускаться при входе в Windows." if wanted
            else "Автозапуск выключен.", text_color=MUTED)

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
        self.profile_hint.configure(text="Пара слов о себе", text_color=MUTED)

    # ------------------------------------------------------------ действия

    def _on_primary(self):
        """Кнопка «Войти» или «Создать аккаунт»."""
        server = self.server_entry.get().strip() or "localhost"
        login = self.login_entry.get().strip()
        password = self.password_entry.get()

        if not login or not password:
            self.auth_error.configure(text="Заполните логин и пароль.")
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
        self.primary_button.configure(text="ПОДКЛЮЧЕНИЕ…", state="disabled")
        self.network.connect(connection_uris(server))

    def _enter_saved(self, account):
        """Вход по сохранённому токену, без пароля."""
        self.server = account.get("server", "localhost")
        self.login = account.get("login", "")
        self.pending_login = protocol.auth_message(account.get("token", ""))
        self.auth_error.configure(text="")
        self.auth_subtitle.configure(text=f"Входим как {account.get('name')}…")
        self.network.connect(connection_uris(self.server))

    def _on_send(self):
        text = self.message_entry.get().strip()
        if not text or self.network.websocket is None:
            return

        self.message_entry.delete(0, "end")
        self.network.send(protocol.text_message(self.user.get("name", ""), text,
                                                self.conversation, self.reply_to))
        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        self._add_bubble(self.user.get("name", "Я"), text, own=True,
                         time_text=now.strftime("%H:%M"),
                         item={"reply_to": self.reply_to})
        self._bump_preview({"text": text, "kind": "text", "nick": "Вы",
                            "at": now.astimezone().isoformat(),
                            "conversation": self.conversation}, notify=False)
        self._cancel_reply()

    def _on_attach(self):
        """Выбор файла для отправки."""
        if self.network.websocket is None:
            return

        path = filedialog.askopenfilename(
            title="Что отправляем?",
            filetypes=[
                ("Картинки и видео", "*.png *.jpg *.jpeg *.webp *.bmp *.gif "
                                     "*.mp4 *.mov *.webm *.mkv *.avi *.m4v"),
                ("Картинки", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                ("Видео", "*.mp4 *.mov *.webm *.mkv *.avi *.m4v"),
                ("Все файлы", "*.*"),
            ])
        if path:
            self._send_file(Path(path))

    def _choose_avatar(self):
        """Выбор картинки для профиля."""
        path = filedialog.askopenfilename(
            title="Выберите фото",
            filetypes=[("Картинки", "*.png *.jpg *.jpeg *.webp *.bmp"),
                       ("Все файлы", "*.*")])
        if not path:
            return

        try:
            data = Path(path).read_bytes()
        except OSError as error:
            self.profile_hint.configure(text=f"Не прочитать файл: {error}",
                                        text_color=OFFLINE)
            return

        self.profile_hint.configure(text="Отправляем фото…", text_color=MUTED)
        self.network.send(protocol.avatar_header(Path(path).name, len(data)), data)

    def _save_profile(self):
        name = self.profile_name.get().strip()
        bio = self.profile_bio.get("1.0", "end").strip()
        if not name:
            self.profile_hint.configure(text="Имя не может быть пустым.",
                                        text_color=OFFLINE)
            return
        self.network.send(protocol.profile_message(name, bio))
        self.profile_hint.configure(text="Сохраняем…", text_color=MUTED)

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
            self._send_bytes("вставка.png", buffer.getvalue())
            return "break"

        if isinstance(content, list) and content:
            for item in content[:5]:
                path = Path(item)
                if path.is_file():
                    self._send_file(path)
            return "break"

        return None  # в буфере текст — пусть вставится как обычно

    def _send_file(self, path):
        try:
            data = path.read_bytes()
        except OSError as error:
            self._service_label(f"Не удалось прочитать файл: {error}")
            return
        self._send_bytes(path.name, data)

    def _send_bytes(self, name, data):
        if len(data) > protocol.MAX_MEDIA_SIZE:
            self._service_label(
                f"«{name}» весит {protocol.human_size(len(data))}, а больше "
                f"{protocol.human_size(protocol.MAX_MEDIA_SIZE)} сервер не принимает.")
            return

        kind = protocol.kind_of(name)
        self.network.send(protocol.media_header(self.user.get("name", ""), kind,
                                                name, len(data), self.conversation,
                                                self.reply_to), data)

        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        self._add_media_bubble(self.user.get("name", "Я"), own=True, kind=kind,
                               media_id=None, name=name, size=len(data),
                               time_text=now.strftime("%H:%M"), data=data,
                               item={"reply_to": self.reply_to})
        self._cancel_reply()

    def _on_leave(self):
        """Возврат к выбору аккаунта — сессия сохраняется."""
        self.network.disconnect()
        self.primary_button.configure(text="ВОЙТИ", state="normal")
        self.password_entry.delete(0, "end")
        self._show_auth()

    def _toggle_theme(self):
        light = ctk.get_appearance_mode() == "Light"
        ctk.set_appearance_mode("dark" if light else "light")

    def _on_close(self):
        """Крестик окна: прячем в трей либо выходим совсем."""
        if self.settings.get("tray", True) and self.tray.available:
            self.withdraw()
            self.tray.show()
            if not self.hidden_notice_shown:
                self.tray.notify("Velix свернулся в трей",
                                 "Программа продолжает работать. "
                                 "Значок рядом с часами открывает окно обратно.")
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
        try:
            while True:
                kind, payload = self.events.get_nowait()
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
        self.after(60, self._pump_events)

    def _on_opened(self, secure):
        """Соединение открылось — отправляем то, чем собирались входить."""
        self.secure = bool(secure)
        if self.pending_login:
            self.network.send(self.pending_login)

    def _on_welcome(self, message):
        self.user = dict(message.get("user") or {})
        self.token = message.get("token")
        self.available_update = message.get("update")
        self.pending_login = None

        store.remember_account(self.config_data, self.user.get("login", ""),
                               self.user.get("name", ""), self.server, self.token)
        store.save(self.config_data)

        self.conversation = 1
        self.conversations = []
        self.people = []
        self.online = set()
        self.quotes = {}
        self.loaded_items = []
        self._clear_messages()
        self.pending_media.clear()
        self.avatar_waiters.clear()
        self.images.clear()
        self.animations.clear()
        self.empty_hint = self._service_label("Пока тихо. Напишите первым.")

        self._refresh_me()
        self.status_dot.configure(text_color=ONLINE)
        lock = "🔒 " if self.secure else "⚠ без шифрования · "
        self.header_subtitle.configure(
            text=f"{lock}вы вошли как {self.user.get('name')}")
        if not self.secure:
            self._service_label("Соединение без шифрования: сервер не умеет wss://. "
                                "Переписку в такой сети можно перехватить.")
        self.message_entry.configure(state="normal")
        self.send_button.configure(state="normal")
        self.attach_button.configure(state="normal")
        self.primary_button.configure(text="ВОЙТИ", state="normal")
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
            self._on_authfail(message.get("text", ""))
        elif kind == "history":
            self._show_history(message)
        elif kind == "conversations":
            self.conversations = message.get("items") or []
            self._refresh_side_list()
            self._update_header()
        elif kind == "people":
            self.people = message.get("items") or []
            self.online = set(message.get("online") or [])
            self._refresh_side_list()
        elif kind == "presence":
            self._on_presence(message)
        elif kind == "typing":
            self._on_typing(message)
        elif kind == "deleted":
            self._on_deleted(message)
        elif kind == "reactions":
            self._on_reactions(message)
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
            self._service_label(message.get("text", ""))

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
            self.profile_hint.configure(text="Сохранено", text_color=ONLINE)

    def _on_authfail(self, text):
        self.pending_login = None
        self.network.disconnect()
        self.primary_button.configure(text="ВОЙТИ", state="normal")
        self.auth_view.pack(fill="both", expand=True)
        self.chat_view.pack_forget()
        self.auth_error.configure(text=text)
        if not self.form.winfo_ismapped():
            self._show_form(register=False)
            self.auth_error.configure(text=text)

    def _on_incoming(self, message):
        """Сообщение из сети: показываем, если оно в открытой переписке."""
        if message.get("conversation") not in (None, self.conversation):
            # Пришло в другую переписку: ленту не трогаем, только
            # обновляем строку в списке слева
            self._bump_preview(message)
            return
        self._show_item(message)
        self._bump_preview(message, notify=False)

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
            ctk.CTkLabel(row, text="сообщение удалено", font=self.font_small,
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
                                   name=item.get("name", "файл"),
                                   size=item.get("size", 0), time_text=time_text,
                                   avatar=avatar, item=item)

    def _on_presence(self, message):
        """Кто-то пришёл или ушёл."""
        user_id = message.get("user")
        if message.get("online"):
            self.online.add(user_id)
        else:
            self.online.discard(user_id)
        self._refresh_side_list()

    def _notify_if_hidden(self, nickname, text):
        """Пока окно в трее, о новых сообщениях сообщаем всплывашкой."""
        if self.state() == "withdrawn":
            self.tray.notify(nickname, text[:120])

    def _on_disconnected(self):
        self.status_dot.configure(text_color=OFFLINE)
        self.header_subtitle.configure(text="нет связи с сервером")
        self.message_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.attach_button.configure(state="disabled")
        if self.chat_view.winfo_ismapped():
            self._service_label("Соединение потеряно. Нажмите «Сменить», чтобы войти заново.")

    def _on_error(self, text):
        self.pending_login = None
        self.primary_button.configure(text="ВОЙТИ", state="normal")
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

        if not avatar_id:
            return

        cached = self.avatar_cache.get((avatar_id, side))
        if cached is not None:
            label.configure(text="", image=cached, fg_color="transparent")
            return

        self.avatar_waiters.setdefault(avatar_id, []).append((label, side))
        if len(self.avatar_waiters[avatar_id]) == 1:
            self.network.send(protocol.fetch_request(avatar_id))

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
            if label.winfo_exists():
                label.configure(text="", image=image, fg_color="transparent")

    # ----------------------------------------------------------- сообщения

    # ------------------------------------------------------------ переписки

    def _refresh_side_list(self):
        """Перерисовывает список переписок и участников."""
        for widget in self.side_list.winfo_children():
            widget.destroy()

        for item in self.conversations:
            self._conversation_row(item)

        others = [person for person in self.people
                  if person["id"] != self.user.get("id")]
        if not others:
            return

        ctk.CTkLabel(self.side_list, text="УЧАСТНИКИ", font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x", padx=10,
                                                        pady=(14, 4))
        for person in others:
            self._person_row(person)

    def _conversation_row(self, item):
        active = item["id"] == self.conversation
        row = ctk.CTkFrame(self.side_list,
                           fg_color=SIDEBAR_ACTIVE if active else "transparent",
                           corner_radius=10, height=60)
        row.pack(fill="x", pady=2)
        row.pack_propagate(False)

        title = item.get("title") or "Общий чат"
        colour = ON_ACCENT if active else TEXT
        quiet = ON_ACCENT if active else MUTED

        avatar = ctk.CTkLabel(row, text="", width=40, height=40, font=self.font_sender)
        avatar.pack(side="left", padx=(8, 8), pady=10)
        self._paint_avatar(avatar, title, item.get("avatar"), 40)

        lines = ctk.CTkFrame(row, fg_color="transparent")
        lines.pack(side="left", fill="both", expand=True, pady=10, padx=(0, 8))

        top = ctk.CTkFrame(lines, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=title, font=self.font_name, text_color=colour,
                     anchor="w").pack(side="left")

        last = item.get("last")
        if last:
            moment = local_time(last.get("at"))
            ctk.CTkLabel(top, text=moment.strftime("%H:%M"), font=self.font_small,
                         text_color=quiet).pack(side="right")
            preview = last.get("text") if last.get("kind") == "text" else "вложение"
            preview = f"{last.get('nick') or ''}: {preview}".strip(": ")
        else:
            preview = "нет сообщений"

        ctk.CTkLabel(lines, text=short(preview, 30), font=self.font_small,
                     text_color=quiet, anchor="w").pack(fill="x")

        for widget in (row, lines, avatar):
            widget.bind("<Button-1>", lambda event, i=item["id"]: self._open(i))
        for child in lines.winfo_children():
            child.bind("<Button-1>", lambda event, i=item["id"]: self._open(i))

    def _person_row(self, person):
        row = ctk.CTkFrame(self.side_list, fg_color="transparent", height=46)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)

        avatar = ctk.CTkLabel(row, text="", width=32, height=32, font=self.font_small)
        avatar.pack(side="left", padx=(8, 8), pady=7)
        self._paint_avatar(avatar, person["name"], person.get("avatar"), 32)

        ctk.CTkLabel(row, text=person["name"], font=self.font_body, text_color=TEXT,
                     anchor="w").pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(row, text="●", font=self.font_small,
                     text_color=ONLINE if person["id"] in self.online else MUTED,
                     width=14).pack(side="right", padx=(0, 10))

        row.bind("<Button-1>", lambda event, i=person["id"]: self._start_direct(i))
        for child in row.winfo_children():
            child.bind("<Button-1>", lambda event, i=person["id"]: self._start_direct(i))

    def _open(self, conversation_id):
        """Открывает переписку и просит её историю."""
        if conversation_id == self.conversation:
            return
        self.conversation = conversation_id
        self._cancel_reply()
        self._clear_messages()
        self._refresh_side_list()
        self._update_header()
        self.network.send(protocol.open_request(conversation_id))

    def _start_direct(self, user_id):
        # Номер переписки знает только сервер, поэтому просто помечаем, что
        # ждём её: пришедшую следом историю откроем, какой бы она ни была
        self.pending_direct = True
        self.network.send(protocol.direct_request(user_id))

    def _update_header(self):
        item = next((c for c in self.conversations if c["id"] == self.conversation),
                    None)
        title = (item or {}).get("title") or "Общий чат"
        self.header_title.configure(text=title)
        self._paint_avatar(self.header_avatar, title, (item or {}).get("avatar"), 40)

    def _clear_messages(self):
        for widget in self.messages.winfo_children():
            widget.destroy()
        self.rows.clear()
        self.reaction_rows.clear()
        self.last_sender = None
        self.current_date = None
        self.oldest = None
        self.empty_hint = None

    def _show_history(self, message):
        """Показывает пришедший кусок истории."""
        if self.pending_direct and message.get("conversation") != self.conversation:
            # Это история личной переписки, которую мы только что попросили
            self.pending_direct = False
            self.conversation = message.get("conversation")
            self._cancel_reply()
            self._refresh_side_list()
            self._update_header()
        elif message.get("conversation") != self.conversation:
            return

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
                self.messages, text="Показать более старые", height=30,
                corner_radius=10, font=self.font_small, fg_color=INPUT_BG,
                hover_color=SEPARATOR, text_color=MUTED, command=self._load_older)
            self.older_button.pack(pady=(8, 4))

        if not self.loaded_items:
            self.empty_hint = self._service_label("Пока тихо. Напишите первым.")
        for item in self.loaded_items:
            self._show_item(item)
        if self.loaded_items:
            self.oldest = self.loaded_items[0].get("id")

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
                corner_radius=12, font=self.font_small,
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
        picker.title("Реакция")
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
        what = item.get("text") or item.get("name") or "вложение"
        self.reply_label.configure(text=f"Ответ {who}: {what[:40]}")
        self.reply_bar.grid(row=2, column=0, sticky="ew")
        self.message_entry.focus_set()

    def _cancel_reply(self):
        self.reply_to = None
        self.reply_bar.grid_forget()

    def _delete_message(self, message_id):
        self.network.send(protocol.delete_request(message_id))

    def _message_menu(self, event, item, own):
        """Правая кнопка на сообщении: ответить или удалить своё."""
        menu = tkinter.Menu(self, tearoff=0)
        menu.add_command(label="Ответить", command=lambda: self._start_reply(item))
        if item.get("id"):
            menu.add_command(label="Реакция",
                             command=lambda: self._emoji_menu(item["id"]))
        if own and item.get("id"):
            menu.add_command(label="Удалить",
                             command=lambda: self._delete_message(item["id"]))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_deleted(self, message):
        """Сообщение убрали — гасим его на месте."""
        row = self.rows.get(message.get("id"))
        if row is not None and row.winfo_exists():
            for widget in row.winfo_children():
                widget.destroy()
            ctk.CTkLabel(row, text="сообщение удалено", font=self.font_small,
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
        self._service_label(f"Найдено: {len(items)}" if items
                            else f"По запросу «{message.get('query')}» ничего нет")
        for item in items:
            self._show_item(item)

    # -------------------------------------------------------------- печатает

    def _on_typing(self, message):
        if message.get("conversation") != self.conversation:
            return
        self.typing_until = time.monotonic() + 3
        self.header_subtitle.configure(text=f"{message.get('nick')} печатает…")
        self.after(3200, self._clear_typing)

    def _clear_typing(self):
        if time.monotonic() >= self.typing_until:
            lock = "🔒 " if self.secure else "⚠ без шифрования · "
            self.header_subtitle.configure(
                text=f"{lock}вы вошли как {self.user.get('name')}")

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
            caption = "Сегодня"
        else:
            caption = f"{int(day)} {MONTHS[int(month) - 1]}"

        self._service_label(caption)
        self.last_sender = None

    def _service_label(self, text):
        """Служебная строка: скруглённая плашка по центру."""
        self._clear_hint()
        row = ctk.CTkFrame(self.messages, fg_color="transparent")
        row.pack(fill="x", pady=8)
        ctk.CTkLabel(row, text=text, font=self.font_small, text_color=MUTED,
                     fg_color=SERVICE_BG, corner_radius=12, height=26,
                     wraplength=self.wrap_length).pack(padx=14, ipadx=10)
        self._scroll_to_bottom()
        return row

    def _new_bubble(self, nickname, own, avatar=None):
        """Общая обвязка пузыря: ряд, аватарка, подпись автора."""
        grouped = self.last_sender == (nickname, own)
        self.last_sender = (nickname, own)

        row = ctk.CTkFrame(self.messages, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(1 if grouped else 5, 0))

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

        bubble = ctk.CTkFrame(row, corner_radius=14,
                              fg_color=BUBBLE_OUT if own else BUBBLE_IN)
        bubble.pack(side="right" if own else "left")

        if not own and not grouped:
            ctk.CTkLabel(bubble, text=nickname, font=self.font_sender,
                         text_color=avatar_color(nickname), anchor="w").pack(
                fill="x", padx=13, pady=(7, 0))

        return bubble, grouped

    def _add_time(self, bubble, own, time_text, item=None):
        if time_text:
            ctk.CTkLabel(bubble, text=time_text, font=self.font_small,
                         text_color=TIME_OUT if own else TIME_IN, anchor="e").pack(
                fill="x", padx=13, pady=(0, 5))

        # Полоска реакций живёт под сообщением и появляется, когда есть что показать
        message_id = (item or {}).get("id")
        if message_id:
            holder = ctk.CTkFrame(bubble, fg_color="transparent")
            self.reaction_rows[message_id] = holder
            self._draw_reactions(message_id)

    def _add_quote(self, bubble, item):
        """Показывает, на что отвечает это сообщение."""
        quoted = self.quotes.get(str(item.get("reply_to")))
        if not quoted:
            return
        what = quoted.get("text") or quoted.get("name") or "вложение"
        strip = ctk.CTkFrame(bubble, fg_color=SEPARATOR, corner_radius=6)
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

        if item.get("reply_to"):
            self._add_quote(bubble, item)

        label = ctk.CTkLabel(bubble, text=text, font=self.font_body,
                             text_color=TEXT_OUT if own else TEXT, justify="left",
                             anchor="w", wraplength=self.wrap_length)
        label.pack(fill="x", padx=13, pady=(3 if own or grouped else 1, 0))

        self._add_time(bubble, own, time_text, item)
        self._attach_menu((bubble, label), item, own)
        if item.get("id"):
            self.rows[item["id"]] = bubble.master
        self._scroll_to_bottom()

    # ----------------------------------------------------------- вложения

    def _add_media_bubble(self, nickname, own, kind, media_id, name, size,
                          time_text=None, data=None, avatar=None, item=None):
        self._clear_hint()
        bubble, _ = self._new_bubble(nickname, own, avatar)
        item = item or {}
        if item.get("reply_to"):
            self._add_quote(bubble, item)

        if kind in ("image", "gif"):
            holder = ctk.CTkLabel(bubble, text="загружаю картинку…",
                                  font=self.font_small, text_color=MUTED)
            holder.pack(padx=6, pady=(6, 2))

            if data is not None:
                self._show_picture(holder, kind, data)
            elif media_id:
                self.pending_media[media_id] = ("picture", holder, kind)
                self.network.send(protocol.fetch_request(media_id))
        else:
            self._file_card(bubble, own, kind, media_id, name, size, data)

        self._add_time(bubble, own, time_text, item)
        self._attach_menu((bubble,), item, own)
        if item.get("id"):
            self.rows[item["id"]] = bubble.master
        self._scroll_to_bottom()

    def _file_card(self, bubble, own, kind, media_id, name, size, data):
        """Видео и прочие файлы показываем карточкой с кнопкой «Открыть»."""
        card = ctk.CTkFrame(bubble, fg_color="transparent")
        card.pack(padx=13, pady=(4, 2))

        caption = KIND_LABEL.get(kind, "Файл")
        ctk.CTkLabel(card, text=f"{caption} · {name}", font=self.font_body,
                     text_color=TEXT_OUT if own else TEXT, anchor="w",
                     wraplength=self.wrap_length - 40).pack(fill="x")
        ctk.CTkLabel(card, text=protocol.human_size(size or 0), font=self.font_small,
                     text_color=TIME_OUT if own else TIME_IN, anchor="w").pack(fill="x")

        button = ctk.CTkButton(card, text="Открыть", height=30, corner_radius=8,
                               font=self.font_small, fg_color=ACCENT,
                               hover_color=ACCENT_HOVER, text_color=ON_ACCENT)
        button.pack(fill="x", pady=(6, 2))

        if data is not None:
            button.configure(command=lambda: self._open_media(name, data))
        elif media_id:
            def request():
                button.configure(text="Загружаю…", state="disabled")
                self.pending_media[media_id] = ("file", button, name)
                self.network.send(protocol.fetch_request(media_id))
            button.configure(command=request)
        else:
            button.configure(state="disabled")

        return card

    def _fill_media(self, message):
        """Пришло содержимое вложения — показываем его."""
        media_id = message.get("id")
        data = message.get("data") or b""

        if media_id in self.avatar_waiters:
            self._fill_avatar(media_id, data)
            return

        waiting = self.pending_media.pop(media_id, None)
        if waiting is None:
            return

        mode, widget, extra = waiting
        if mode == "picture":
            self._show_picture(widget, extra, data)
        else:
            widget.configure(text="Открыть", state="normal",
                             command=lambda: self._open_media(extra, data))
            self._open_media(extra, data)

    def _show_picture(self, holder, kind, data):
        """Заменяет заглушку картинкой, гифку — запускает."""
        try:
            image = Image.open(io.BytesIO(data))
            frames = self._prepare_frames(image, kind)
        except Exception as error:
            holder.configure(text=f"не удалось показать картинку: {error}")
            return

        self.images.extend(frames)
        holder.configure(text="", image=frames[0])

        if len(frames) > 1:
            delay = max(30, int(image.info.get("duration", 80)))
            self.animations[holder] = frames
            self._animate(holder, frames, 0, delay)

    def _prepare_frames(self, image, kind):
        """Готовит кадры: обычной картинке — один, гифке — все по очереди."""
        if kind != "gif":
            picture = image.convert("RGBA")
            picture.thumbnail(MAX_PICTURE, Image.LANCZOS)
            return [ctk.CTkImage(light_image=picture, dark_image=picture,
                                 size=picture.size)]

        frames = []
        for frame in ImageSequence.Iterator(image):
            picture = frame.convert("RGBA")
            picture.thumbnail(MAX_PICTURE, Image.LANCZOS)
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
            self._service_label(f"Не удалось открыть файл: {error}")

    def _scroll_to_bottom(self):
        self.messages.update_idletasks()
        self.messages._parent_canvas.yview_moveto(1.0)


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    VelixApp().mainloop()


if __name__ == "__main__":
    main()
