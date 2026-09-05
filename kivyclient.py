"""Второй клиент Velix — на Kivy, и весь целиком в одном цикле asyncio.

Зачем он, если окно на Tk уже есть и работает.

Tkinter живёт своим циклом и не умеет делить его с чужим. Поэтому в gui.py
сеть вынесена в отдельный поток со своим циклом asyncio, а разговаривают они
через очередь: сеть кладёт кадр, окно раз в тридцать миллисекунд заглядывает,
не появилось ли чего. Работает надёжно, но правило «Tkinter нельзя трогать из
чужого потока» приходится помнить всё время, и каждое новое место — это ещё
одна пара «положил в очередь / разобрал очередь».

Kivy умеет иначе: App.async_run(async_lib="asyncio") отдаёт свой цикл нам.
Значит и рисование, и сокет живут в одном цикле, в одном потоке, и обработчик
нажатия может просто написать await. Ни очереди, ни правил про потоки.

    python kivyclient.py

Клиент пока умеет главное: войти, показать переписки, прочитать историю,
получать сообщения живьём и писать текстом. Вложения, голосовые и кружочки
остались за окном на Tk — это проба подхода, а не замена.
"""

import asyncio
import os
import ssl
import sys

os.environ.setdefault("KIVY_NO_ARGS", "1")

import websockets
from kivy.app import App
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, RoundedRectangle

import i18n
import protocol
import store
from i18n import t

# Те же цвета, что и в окне на Tk, только числами от нуля до единицы
ФОН = (0.063, 0.102, 0.141, 1)
БОКОВАЯ = (0.09, 0.129, 0.169, 1)
ПУЗЫРЬ_СВОЙ = (0.169, 0.322, 0.471, 1)
ПУЗЫРЬ_ЧУЖОЙ = (0.106, 0.157, 0.212, 1)
ПОЛЕ = (0.149, 0.2, 0.247, 1)
ТЕКСТ = (1, 1, 1, 1)
ТИХИЙ = (0.439, 0.518, 0.6, 1)
ЯРКИЙ = (0.322, 0.533, 0.757, 1)


class Скруглённый(BoxLayout):
    """Коробка со скруглённым цветным фоном — из таких собрано всё окно."""

    def __init__(self, цвет=ПУЗЫРЬ_ЧУЖОЙ, радиус=14, **прочее):
        super().__init__(**прочее)
        self.цвет = цвет
        self.радиус = радиус
        with self.canvas.before:
            self._краска = Color(*цвет)
            self._фон = RoundedRectangle(radius=[радиус])
        self.bind(pos=self._переложить, size=self._переложить)

    def _переложить(self, *_):
        self._фон.pos = self.pos
        self._фон.size = self.size


class Пузырь(Скруглённый):
    """Одно сообщение в ленте."""

    def __init__(self, item, свой, **прочее):
        super().__init__(цвет=ПУЗЫРЬ_СВОЙ if свой else ПУЗЫРЬ_ЧУЖОЙ,
                         orientation="vertical", padding=dp(10),
                         spacing=dp(2), size_hint_y=None, **прочее)
        if not свой:
            имя = Label(text=item.get("nick", ""), color=ЯРКИЙ, bold=True,
                        font_size=dp(13), size_hint_y=None, height=dp(18),
                        halign="left", valign="middle")
            имя.bind(size=lambda с, _: setattr(с, "text_size", с.size))
            self.add_widget(имя)

        текст = Label(text=self._о_чём(item), color=ТЕКСТ, font_size=dp(15),
                      size_hint_y=None, halign="left", valign="top",
                      markup=False)
        # Высоту подписи Kivy сам не считает: говорим ей ширину и просим
        # пересчитать высоту под перенос строк
        текст.bind(width=lambda с, ширина: setattr(с, "text_size",
                                                   (ширина, None)),
                   texture_size=lambda с, размер: setattr(с, "height",
                                                          размер[1]))
        self.add_widget(текст)
        self.bind(minimum_height=lambda с, высота: setattr(с, "height", высота))

    @staticmethod
    def _о_чём(item):
        вид = item.get("kind", "text")
        if вид == "text":
            return item.get("text", "")
        if вид == "voice":
            return "🎤 " + t("голосовое")
        if вид == "circle":
            return "◉ " + t("кружочек")
        if вид == "deleted":
            return t("сообщение удалено")
        return "📎 " + (item.get("name") or t("вложение"))


class Velix(App):
    """Клиент целиком: и рисование, и сокет — в одном цикле."""

    title = "Velix"

    def __init__(self, **прочее):
        super().__init__(**прочее)
        self.связь = None
        self.я = {}
        self.переписки = []
        self.открыта = None
        self.лента = None
        self.список = None
        self.строка = None
        self.подпись = None
        self.настройки = store.load()

    # ------------------------------------------------------------- вид

    def build(self):
        if Window is not None:
            Window.clearcolor = ФОН
        self.корень = BoxLayout(orientation="vertical")
        self._показать_вход()
        return self.корень

    def _показать_вход(self):
        self.корень.clear_widgets()
        карточка = Скруглённый(цвет=БОКОВАЯ, радиус=20, orientation="vertical",
                               padding=dp(24), spacing=dp(12),
                               size_hint=(None, None), size=(dp(320), dp(300)),
                               pos_hint={"center_x": 0.5, "center_y": 0.5})

        карточка.add_widget(Label(text="Velix", font_size=dp(26), color=ТЕКСТ,
                                  size_hint_y=None, height=dp(40)))

        последний = self.настройки.get("last_server") or "localhost:8765"
        self.поле_сервера = self._поле(последний)
        self.поле_имени = self._поле(self.настройки.get("last_login") or "")
        self.поле_пароля = self._поле("", пароль=True)
        for поле, подсказка in ((self.поле_сервера, t("Адрес сервера")),
                                (self.поле_имени, t("Логин")),
                                (self.поле_пароля, t("Пароль"))):
            поле.hint_text = подсказка
            карточка.add_widget(поле)

        кнопка = Button(text=t("ВОЙТИ"), size_hint_y=None, height=dp(44),
                        background_normal="", background_color=ЯРКИЙ,
                        color=(1, 1, 1, 1))
        кнопка.bind(on_release=lambda _: self.позже(self.войти()))
        карточка.add_widget(кнопка)

        self.подпись = Label(text="", color=ТИХИЙ, font_size=dp(13),
                             size_hint_y=None, height=dp(22))
        карточка.add_widget(self.подпись)

        обёртка = BoxLayout()
        обёртка.add_widget(карточка)
        self.корень.add_widget(обёртка)

    @staticmethod
    def _поле(значение, пароль=False):
        поле = TextInput(text=значение, multiline=False, password=пароль,
                         size_hint_y=None, height=dp(40), padding=[dp(10)] * 4,
                         background_normal="", background_active="",
                         background_color=ПОЛЕ, foreground_color=ТЕКСТ,
                         cursor_color=ЯРКИЙ, hint_text_color=ТИХИЙ)
        return поле

    def _показать_чат(self):
        self.корень.clear_widgets()
        всё = BoxLayout(orientation="horizontal", spacing=dp(2))

        слева = Скруглённый(цвет=БОКОВАЯ, радиус=0, orientation="vertical",
                            size_hint_x=None, width=dp(220), padding=dp(6),
                            spacing=dp(4))
        полка = ScrollView()
        self.список = BoxLayout(orientation="vertical", size_hint_y=None,
                                spacing=dp(4))
        self.список.bind(minimum_height=lambda с, в: setattr(с, "height", в))
        полка.add_widget(self.список)
        слева.add_widget(полка)
        всё.add_widget(слева)

        справа = BoxLayout(orientation="vertical")
        поток = ScrollView()
        self.лента = BoxLayout(orientation="vertical", size_hint_y=None,
                               padding=dp(10), spacing=dp(6))
        self.лента.bind(minimum_height=lambda с, в: setattr(с, "height", в))
        поток.add_widget(self.лента)
        self.поток = поток
        справа.add_widget(поток)

        низ = BoxLayout(size_hint_y=None, height=dp(56), padding=dp(8),
                        spacing=dp(8))
        self.строка = self._поле("")
        self.строка.hint_text = t("Написать сообщение…")
        self.строка.bind(on_text_validate=lambda _: self.позже(self.отправить()))
        низ.add_widget(self.строка)
        послать = Button(text="➤", size_hint_x=None, width=dp(48),
                         background_normal="", background_color=ЯРКИЙ)
        послать.bind(on_release=lambda _: self.позже(self.отправить()))
        низ.add_widget(послать)
        справа.add_widget(низ)

        всё.add_widget(справа)
        self.корень.add_widget(всё)
        self._нарисовать_список()

    def _нарисовать_список(self):
        if self.список is None:
            return
        self.список.clear_widgets()
        for одна in self.переписки:
            кнопка = Button(text=одна.get("title") or "?", size_hint_y=None,
                            height=dp(44), background_normal="",
                            background_color=(ПУЗЫРЬ_СВОЙ if одна["id"] ==
                                              self.открыта else ПОЛЕ),
                            color=ТЕКСТ, halign="left")
            кнопка.bind(on_release=lambda _, номер=одна["id"]:
                        self.позже(self.открыть(номер)))
            self.список.add_widget(кнопка)

    def _добавить(self, item):
        свой = item.get("user") == self.я.get("id")
        пузырь = Пузырь(item, свой, size_hint_x=0.75,
                        pos_hint={"right": 1} if свой else {"x": 0})
        self.лента.add_widget(пузырь)
        # Прокручиваем вниз на следующем кадре: сейчас высота ещё не сосчитана
        self.позже(self._вниз())

    async def _вниз(self):
        await asyncio.sleep(0)
        self.поток.scroll_y = 0

    # ---------------------------------------------------------- сеть

    @staticmethod
    def позже(корутина):
        """Пускает корутину в тот же цикл, в котором рисуется окно."""
        return asyncio.ensure_future(корутина)

    async def войти(self):
        адрес = self.поле_сервера.text.strip() or "localhost:8765"
        self.подпись.text = t("Подключаемся…")
        try:
            await self._подключиться(адрес)
        except Exception as беда:
            self.подпись.text = t("нет связи с сервером") + f": {беда}"
            return

        await self.связь.send(protocol.login_message(
            self.поле_имени.text.strip(), self.поле_пароля.text))
        self.настройки["last_server"] = адрес
        self.настройки["last_login"] = self.поле_имени.text.strip()
        store.save(self.настройки)
        self.позже(self.слушать())

    async def _подключиться(self, адрес):
        доверие = None
        куда = f"ws://{адрес}"
        if not адрес.startswith("localhost") and not адрес.startswith("127."):
            доверие = ssl.create_default_context()
            куда = f"wss://{адрес}"
        self.связь = await websockets.connect(
            куда, ssl=доверие, max_size=protocol.MAX_FRAME_SIZE,
            open_timeout=15, ping_interval=30, ping_timeout=90)

    async def слушать(self):
        """Читает кадры и сразу рисует — всё в одном потоке, без очередей."""
        try:
            async for сырое in self.связь:
                кадр = protocol.decode(сырое) if isinstance(сырое, str) else None
                if кадр is not None:
                    self.разобрать(кадр)
        except Exception:
            if self.подпись is not None:
                self.подпись.text = t("нет связи с сервером")

    def разобрать(self, кадр):
        вид = кадр.get("type")
        if вид == "welcome":
            self.я = кадр.get("user") or {}
            self._показать_чат()
        elif вид == "authfail":
            self.подпись.text = кадр.get("text", "")
        elif вид == "conversations":
            self.переписки = кадр.get("items", [])
            self._нарисовать_список()
            if self.открыта is None and self.переписки:
                self.позже(self.открыть(self.переписки[0]["id"]))
        elif вид == "history":
            if кадр.get("conversation") == self.открыта:
                self.лента.clear_widgets()
                for item in кадр.get("items", []):
                    self._добавить(item)
        elif вид in ("text", "media"):
            if кадр.get("conversation") == self.открыта:
                self._добавить(кадр)

    async def открыть(self, номер):
        self.открыта = номер
        self._нарисовать_список()
        self.лента.clear_widgets()
        await self.связь.send(protocol.open_request(номер))

    async def отправить(self):
        слова = self.строка.text.strip()
        if not слова or self.открыта is None:
            return
        self.строка.text = ""
        await self.связь.send(protocol.text_message(
            self.я.get("name", ""), слова, self.открыта))
        self._добавить({"kind": "text", "text": слова,
                        "user": self.я.get("id"), "nick": self.я.get("name")})

    # ------------------------------------------------------------ выход

    def on_stop(self):
        if self.связь is not None:
            self.позже(self.связь.close())


async def главное():
    i18n.set_language((store.load().get("settings") or {}).get(
        "language", i18n.DEFAULT))
    приложение = Velix()
    # Вот ради чего всё: окно и сеть в одном цикле, без потоков и очередей
    await приложение.async_run(async_lib="asyncio")


if __name__ == "__main__":
    sys.exit(asyncio.run(главное()) or 0)
