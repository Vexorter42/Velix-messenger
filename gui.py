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
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import websockets
from PIL import Image, ImageSequence

import protocol

PORT = 8765

# Картинку в чате шире этого не показываем — иначе пузырь распирает окно
MAX_PICTURE = (360, 360)

# Кадров в гифке берём не больше: длинные ролики иначе съедают память
MAX_GIF_FRAMES = 120

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


def avatar_color(nickname):
    """Цвет аватарки закреплён за никнеймом, чтобы не прыгал между запусками."""
    return AVATAR_COLORS[sum(map(ord, nickname)) % len(AVATAR_COLORS)]


def build_uri(address):
    """Собирает адрес подключения из того, что ввёл пользователь.

    Порт можно дописать через двоеточие — "vexorter.duckdns.org:9000".
    Без него подставляется стандартный 8765.
    """
    address = address.strip() or "localhost"

    if address.startswith("["):  # IPv6 в скобках: [::1] или [::1]:8765
        host, _, rest = address.partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return f"ws://{address}"
        return f"ws://{host}]:{PORT}"

    if address.count(":") == 1:
        host, _, port = address.partition(":")
        if port.isdigit() and host:
            return f"ws://{host}:{port}"

    if address.count(":") > 1:  # голый IPv6 без порта
        return f"ws://[{address}]:{PORT}"

    return f"ws://{address}:{PORT}"


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


class Network:
    """Подключение к серверу в фоновом потоке."""

    def __init__(self, events):
        self.events = events
        self.loop = None
        self.websocket = None

    def connect(self, uri):
        threading.Thread(target=self._run, args=(uri,), daemon=True).start()

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

    def _run(self, uri):
        # Цикл держим в локальной переменной и только потом публикуем в self:
        # если пользователь успел переподключиться, старый поток не должен
        # закрыть цикл нового — тот ещё работает.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            loop.run_until_complete(self._session(uri))
        finally:
            loop.close()
            if self.loop is loop:
                self.loop = None

    async def _session(self, uri):
        connection = None
        try:
            async with websockets.connect(uri, max_size=protocol.MAX_FRAME_SIZE) as websocket:
                connection = websocket
                self.websocket = websocket
                self.events.put(("connected", None))

                while True:
                    message = protocol.decode(await websocket.recv())
                    if message is None:
                        continue
                    if message.get("type") == "blob":
                        # За описанием вложения сразу идёт кадр с содержимым
                        message["data"] = await websocket.recv()
                    self.events.put(("message", message))

        except ConnectionRefusedError:
            self.events.put(("error", "Сервер недоступен. Проверьте, запущен ли он."))
            return
        except OSError as error:
            self.events.put(("error", f"Не удалось подключиться: {error}"))
            return
        except websockets.exceptions.ConnectionClosed:
            pass
        except websockets.exceptions.InvalidStatus as error:
            # Сервер может пускать только по определённому имени и отвечать 403
            if error.response.status_code == 403:
                self.events.put(("error", "Сервер не принимает подключение по этому "
                                          "адресу. Проверьте, что он введён точно."))
            else:
                self.events.put(("error", f"Сервер ответил кодом {error.response.status_code}."))
            return
        except websockets.exceptions.WebSocketException as error:
            self.events.put(("error", f"Ошибка соединения: {error}"))
            return
        finally:
            # Сбрасываем только своё подключение, чужое не трогаем
            if self.websocket is connection:
                self.websocket = None

        self.events.put(("disconnected", None))


class VelixApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Velix")
        self.geometry("1040x680")
        self.minsize(820, 520)
        self.configure(fg_color=CHAT_BG)

        self.events = queue.Queue()
        self.network = Network(self.events)
        self.nickname = ""
        self.server = ""
        self.wrap_length = 420
        self.last_sender = None
        self.current_date = None
        self.empty_hint = None

        # Вложения, содержимое которых мы ждём от сервера
        self.pending_media = {}
        # Ссылки на картинки: без них Tkinter выбрасывает их сборщиком мусора
        self.images = []
        self.animations = {}

        self.font_title = ctk.CTkFont(family="Segoe UI Semibold", size=26)
        self.font_name = ctk.CTkFont(family="Segoe UI Semibold", size=14)
        self.font_sender = ctk.CTkFont(family="Segoe UI Semibold", size=13)
        self.font_body = ctk.CTkFont(family="Segoe UI", size=14)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=11)
        self.font_avatar = ctk.CTkFont(family="Segoe UI Semibold", size=16)
        self.font_button = ctk.CTkFont(family="Segoe UI Semibold", size=14)

        self._apply_icon()
        self._build_connect_view()
        self._build_chat_view()
        self._show_connect()

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

    # -------------------------------------------------------------- экраны

    def _build_connect_view(self):
        self.connect_view = ctk.CTkFrame(self, fg_color="transparent")

        card = ctk.CTkFrame(self.connect_view, fg_color=SIDEBAR, corner_radius=16)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="V", font=ctk.CTkFont(family="Segoe UI Semibold", size=34),
                     text_color=ON_ACCENT, fg_color=ACCENT, corner_radius=40,
                     width=80, height=80).pack(padx=48, pady=(40, 18))

        ctk.CTkLabel(card, text="Velix", font=self.font_title,
                     text_color=TEXT).pack(padx=48, pady=(0, 4))
        ctk.CTkLabel(card, text="Введите имя и адрес сервера", font=self.font_small,
                     text_color=MUTED).pack(padx=48, pady=(0, 24))

        self.nickname_entry = self._entry(card, "Ваше имя")
        self.nickname_entry.pack(padx=48, pady=(0, 10))

        self.server_entry = self._entry(card, "Адрес сервера — пусто значит localhost")
        self.server_entry.pack(padx=48, pady=(0, 18))

        self.connect_button = ctk.CTkButton(
            card, text="ПОДКЛЮЧИТЬСЯ", width=300, height=46, corner_radius=10,
            font=self.font_button, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ON_ACCENT, command=self._on_connect)
        self.connect_button.pack(padx=48, pady=(0, 12))

        self.connect_error = ctk.CTkLabel(card, text="", font=self.font_small,
                                          text_color=OFFLINE, wraplength=300)
        self.connect_error.pack(padx=48, pady=(0, 22))

        self.nickname_entry.bind("<Return>", lambda event: self._on_connect())
        self.server_entry.bind("<Return>", lambda event: self._on_connect())
        for entry in (self.nickname_entry, self.server_entry):
            entry.bind("<Control-KeyPress>", self._on_entry_shortcut)

    def _entry(self, master, placeholder):
        return ctk.CTkEntry(
            master, placeholder_text=placeholder, width=300, height=46,
            corner_radius=10, border_width=1, border_color=SEPARATOR,
            fg_color=INPUT_BG, text_color=TEXT, placeholder_text_color=MUTED,
            font=self.font_body)

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

        top = ctk.CTkFrame(sidebar, fg_color="transparent", height=58)
        top.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(top, text="Velix", font=self.font_name,
                     text_color=TEXT).pack(side="left", padx=(6, 0))

        self.leave_button = ctk.CTkButton(
            top, text="Выйти", width=62, height=30, corner_radius=8,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._on_leave)
        self.leave_button.pack(side="right")

        self.theme_button = ctk.CTkButton(
            top, text="Тема", width=58, height=30, corner_radius=8,
            font=self.font_small, fg_color=INPUT_BG, hover_color=SEPARATOR,
            text_color=MUTED, command=self._toggle_theme)
        self.theme_button.pack(side="right", padx=(0, 6))

        # Единственный чат в списке — он же всегда открытый
        item = ctk.CTkFrame(sidebar, fg_color=SIDEBAR_ACTIVE, corner_radius=10,
                            height=68)
        item.pack(fill="x", padx=8, pady=(2, 0))
        item.pack_propagate(False)

        ctk.CTkLabel(item, text="V", font=self.font_avatar, text_color=ON_ACCENT,
                     fg_color=avatar_color("Velix"), corner_radius=25,
                     width=50, height=50).pack(side="left", padx=(9, 10), pady=9)

        lines = ctk.CTkFrame(item, fg_color="transparent")
        lines.pack(side="left", fill="both", expand=True, pady=12, padx=(0, 10))

        title_row = ctk.CTkFrame(lines, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text="Общий чат", font=self.font_name,
                     text_color=ON_ACCENT).pack(side="left")
        self.chat_time = ctk.CTkLabel(title_row, text="", font=self.font_small,
                                      text_color=ON_ACCENT)
        self.chat_time.pack(side="right")

        self.chat_preview = ctk.CTkLabel(lines, text="нет сообщений",
                                         font=self.font_small, text_color=ON_ACCENT,
                                         anchor="w")
        self.chat_preview.pack(fill="x")

    def _build_conversation(self):
        main = ctk.CTkFrame(self.chat_view, fg_color=CHAT_BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0, height=62)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text="V", font=self.font_sender, text_color=ON_ACCENT,
                     fg_color=avatar_color("Velix"), corner_radius=20,
                     width=40, height=40).grid(row=0, column=0, padx=(18, 12), pady=11)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(titles, text="Общий чат", font=self.font_name,
                     text_color=TEXT).pack(anchor="w")
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

        composer = ctk.CTkFrame(main, fg_color=COMPOSER, corner_radius=0)
        composer.grid(row=2, column=0, sticky="ew")
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

        self.send_button = ctk.CTkButton(
            composer, text="➤", width=44, height=44, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=16), fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ON_ACCENT, command=self._on_send)
        self.send_button.grid(row=0, column=2, padx=(0, 18), pady=13)

    def _show_connect(self):
        self.chat_view.pack_forget()
        self.connect_view.pack(fill="both", expand=True)
        self.nickname_entry.focus_set()

    def _show_chat(self):
        self.connect_view.pack_forget()
        self.chat_view.pack(fill="both", expand=True)
        self.message_entry.focus_set()

    # ------------------------------------------------------------ действия

    def _on_connect(self):
        # Квадратные скобки вырезаем: имя показывается в подписи к сообщению
        nickname = self.nickname_entry.get().strip().replace("[", "").replace("]", "")
        if not nickname:
            nickname = "Аноним"
        server = self.server_entry.get().strip() or "localhost"

        self.nickname = nickname
        self.server = server
        self.connect_error.configure(text="")
        self.connect_button.configure(text="ПОДКЛЮЧЕНИЕ…", state="disabled")
        self.network.connect(build_uri(server))

    def _on_send(self):
        text = self.message_entry.get().strip()
        if not text or self.network.websocket is None:
            return

        self.message_entry.delete(0, "end")
        self.network.send(protocol.text_message(self.nickname, text))
        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        self._add_bubble(self.nickname, text, own=True, time_text=now.strftime("%H:%M"))

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

        try:
            entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        entry.insert("insert", text.strip().replace("\n", " "))
        return "break"

    def _on_ctrl_key(self, event):
        """Ctrl+V в поле сообщения: сначала пробуем картинку из буфера."""
        if event.keycode != 86:  # клавиша V, независимо от раскладки
            return self._on_entry_shortcut(event)
        if self._paste_from_clipboard() == "break":
            return "break"
        return self._paste_text(event.widget)

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
        self.network.send(protocol.media_header(self.nickname, kind, name, len(data)), data)

        now = datetime.now()
        self._ensure_date(now.strftime("%d.%m"))
        self._add_media_bubble(self.nickname, own=True, kind=kind, media_id=None,
                               name=name, size=len(data),
                               time_text=now.strftime("%H:%M"), data=data)

    def _on_leave(self):
        self.network.disconnect()
        self.connect_button.configure(text="ПОДКЛЮЧИТЬСЯ", state="normal")
        self._show_connect()

    def _toggle_theme(self):
        light = ctk.get_appearance_mode() == "Light"
        ctk.set_appearance_mode("dark" if light else "light")

    def _on_close(self):
        self.network.disconnect()
        self.destroy()

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
                if kind == "connected":
                    self._on_connected()
                elif kind == "message":
                    self._on_message(payload)
                elif kind == "disconnected":
                    self._on_disconnected()
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.after(60, self._pump_events)

    def _on_connected(self):
        for widget in self.messages.winfo_children():
            widget.destroy()
        self.last_sender = None
        self.current_date = None
        self.pending_media.clear()
        self.images.clear()
        self.animations.clear()
        self.empty_hint = self._service_label("Пока тихо. Напишите первым.")
        self.header_subtitle.configure(text=f"вы вошли как {self.nickname}")
        self.status_dot.configure(text_color=ONLINE)
        self.message_entry.configure(state="normal")
        self.send_button.configure(state="normal")
        self.attach_button.configure(state="normal")
        self.connect_button.configure(text="ПОДКЛЮЧИТЬСЯ", state="normal")
        self._show_chat()

    def _on_message(self, message):
        kind = message.get("type")

        if kind == "history":
            for item in message.get("items", []):
                self._show_item(item)
        elif kind in ("text", "media"):
            self._show_item(message)
        elif kind == "blob":
            self._fill_media(message)
        elif kind == "system":
            self._service_label(message.get("text", ""))
        elif kind == "error":
            self._service_label(message.get("text", ""))

    def _show_item(self, item):
        """Показывает одно сообщение — своё или чужое, текст или вложение."""
        moment = local_time(item.get("at"))
        self._ensure_date(moment.strftime("%d.%m"))
        nickname = item.get("nick", "?")
        time_text = moment.strftime("%H:%M")

        if item.get("kind", "text") == "text":
            self._add_bubble(nickname, item.get("text", ""), own=False,
                             time_text=time_text)
        else:
            self._add_media_bubble(nickname, own=False, kind=item["kind"],
                                   media_id=item.get("id"), name=item.get("name", "файл"),
                                   size=item.get("size", 0), time_text=time_text)

    def _on_disconnected(self):
        self.status_dot.configure(text_color=OFFLINE)
        self.header_subtitle.configure(text="нет связи с сервером")
        self.message_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.attach_button.configure(state="disabled")
        self._service_label("Соединение потеряно. Нажмите «Выйти», чтобы подключиться заново.")

    def _on_error(self, text):
        self.connect_button.configure(text="ПОДКЛЮЧИТЬСЯ", state="normal")
        self.connect_error.configure(text=text)
        self._show_connect()

    # ----------------------------------------------------------- сообщения

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

    def _new_bubble(self, nickname, own):
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
                ctk.CTkLabel(row, text=nickname[0].upper(), font=self.font_sender,
                             text_color=ON_ACCENT, fg_color=avatar_color(nickname),
                             corner_radius=18, width=36, height=36).pack(
                    side="left", padx=(0, 8), anchor="s")

        bubble = ctk.CTkFrame(row, corner_radius=14,
                              fg_color=BUBBLE_OUT if own else BUBBLE_IN)
        bubble.pack(side="right" if own else "left")

        if not own and not grouped:
            ctk.CTkLabel(bubble, text=nickname, font=self.font_sender,
                         text_color=avatar_color(nickname), anchor="w").pack(
                fill="x", padx=13, pady=(7, 0))

        return bubble, grouped

    def _add_time(self, bubble, own, time_text):
        if not time_text:
            return
        ctk.CTkLabel(bubble, text=time_text, font=self.font_small,
                     text_color=TIME_OUT if own else TIME_IN, anchor="e").pack(
            fill="x", padx=13, pady=(0, 5))

    def _add_bubble(self, nickname, text, own, time_text=None):
        self._clear_hint()
        bubble, grouped = self._new_bubble(nickname, own)

        ctk.CTkLabel(bubble, text=text, font=self.font_body,
                     text_color=TEXT_OUT if own else TEXT, justify="left",
                     anchor="w", wraplength=self.wrap_length).pack(
            fill="x", padx=13, pady=(3 if own or grouped else 1, 0))

        self._add_time(bubble, own, time_text)
        self._update_preview(nickname, text, time_text, own)
        self._scroll_to_bottom()

    # ----------------------------------------------------------- вложения

    def _add_media_bubble(self, nickname, own, kind, media_id, name, size,
                          time_text=None, data=None):
        self._clear_hint()
        bubble, _ = self._new_bubble(nickname, own)

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
            holder = self._file_card(bubble, own, kind, media_id, name, size, data)

        self._add_time(bubble, own, time_text)
        label = f"{KIND_LABEL.get(kind, 'Фото')}: {name}" if kind not in ("image", "gif") \
            else ("GIF" if kind == "gif" else "Фото")
        self._update_preview(nickname, label, time_text, own)
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
        waiting = self.pending_media.pop(message.get("id"), None)
        if waiting is None:
            return

        mode, widget, extra = waiting
        data = message.get("data") or b""

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

    def _update_preview(self, nickname, text, time_text, own):
        """Последнее сообщение видно в списке чатов слева."""
        author = "Вы" if own else nickname
        preview = f"{author}: {text}".replace("\n", " ")
        # Строка в списке чатов узкая — длинное сообщение обрезаем сами,
        # иначе край текста просто уедет под границу панели
        if len(preview) > 27:
            preview = preview[:26] + "…"
        self.chat_preview.configure(text=preview)
        if time_text:
            self.chat_time.configure(text=time_text)

    def _scroll_to_bottom(self):
        self.messages.update_idletasks()
        self.messages._parent_canvas.yview_moveto(1.0)


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    VelixApp().mainloop()


if __name__ == "__main__":
    main()
