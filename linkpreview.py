"""Карточка ссылки: заголовок, описание и картинка вместо голого адреса.

Страницу тянет сервер, а не клиенты. Иначе каждый, кто просто открыл переписку,
сходил бы на чужой сайт и засветил там свой адрес — а заодно и то, что он эту
ссылку видел. Сервер сходит один раз на всех, запомнит, что нашёл, и раздаст.

Ходить куда попало он при этом не должен. Ссылка приходит от человека, а
сервер стоит внутри домашней сети: попроси его открыть http://192.168.0.1 —
и он послушно принесёт то, до чего снаружи не дотянуться. Поэтому имя
разрешается заранее, и на домашние, служебные и петлевые адреса мы не ходим —
ни сразу, ни после переадресации.
"""

import html
import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# Дольше ждать нечего: карточка — украшение, а не сообщение
ЖДЁМ = 8

# Читаем только начало страницы: всё нужное лежит в <head>
СТРАНИЦА = 512 * 1024

# Картинка карточки: больше мегабайта нам не нужно
КАРТИНКА = 4 * 1024 * 1024

ПЕРЕАДРЕСАЦИЙ = 3

# Представляемся честно: часть сайтов иначе отдаёт заглушку
ПРЕДСТАВЛЯЕМСЯ = ("Mozilla/5.0 (compatible; VelixLinkPreview/1.0; "
                  "+https://github.com/Vexorter42/Velix-messenger)")

# Домашние адреса запрещены нарочно — см. описание модуля. Открывается это
# только в проверках: им нужен сайт, поднятый тут же на localhost
ДОМА_МОЖНО = os.environ.get("VELIX_PREVIEW_ALLOW_LOCAL") == "1"

АДРЕС = re.compile(r"https?://[^\s<>\"'）)\]]+", re.IGNORECASE)

# Хвосты вроде точки в конце предложения в ссылку не входят
ХВОСТЫ = ".,;:!?»\"'"


def find_link(text):
    """Первая http(s)-ссылка в тексте или None."""
    найдено = АДРЕС.search(text or "")
    if not найдено:
        return None
    ссылка = найдено.group(0).rstrip(ХВОСТЫ)
    # Незакрытая скобка в конце — обычно тоже не часть адреса
    while ссылка.endswith(")") and ссылка.count("(") < ссылка.count(")"):
        ссылка = ссылка[:-1]
    return ссылка if len(ссылка) > 10 else None


def _домашний(адрес):
    """True, если адрес ведёт внутрь сети или в саму машину."""
    if ДОМА_МОЖНО:
        return False
    try:
        где = ipaddress.ip_address(адрес)
    except ValueError:
        return True
    return (где.is_private or где.is_loopback or где.is_link_local
            or где.is_multicast or где.is_reserved or где.is_unspecified)


def наружу(ссылка):
    """Проверяет, что ссылка ведёт в интернет, а не внутрь дома.

    Возвращает адрес, годный для запроса, или None. Между этой проверкой и
    самим запросом имя теоретически может смениться на домашнее — защищаться
    от такого пришлось бы своим соединением на голом сокете. Для переписки
    вчетвером это перебор, а вот прямую ссылку на роутер отсекает.

    Заодно адрес приводится к тому виду, который понимает http: люди
    присылают ссылки с кириллицей и в имени сайта, и в дорожке, а urllib
    такое отправить не может — русская Википедия без этого не показывалась бы
    вовсе.
    """
    try:
        разобрано = urllib.parse.urlsplit(ссылка)
    except ValueError:
        return None

    if разобрано.scheme not in ("http", "https") or not разобрано.hostname:
        return None

    try:
        имя = разобрано.hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None

    порт = разобрано.port or (443 if разобрано.scheme == "https" else 80)
    try:
        куда = socket.getaddrinfo(имя, порт, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError, OSError):
        return None

    if not куда or any(_домашний(один[4][0]) for один in куда):
        return None

    место = имя if разобрано.port is None else f"{имя}:{разобрано.port}"
    # Уже закодированное оставляем как есть — потому и % в безопасных
    дорожка = urllib.parse.quote(разобрано.path, safe="/%:@!$&'()*+,;=~")
    запрос = urllib.parse.quote(разобрано.query, safe="/%:@!$&'()*+,;=?~")
    # Якорь до сервера всё равно не доезжает
    return urllib.parse.urlunsplit((разобрано.scheme, место, дорожка, запрос, ""))


class _Осторожно(urllib.request.HTTPRedirectHandler):
    """Переадресация тоже может увести внутрь сети — проверяем каждую."""

    max_redirections = ПЕРЕАДРЕСАЦИЙ

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if наружу(newurl) is None:
            return None
        return super().redirect_request(request, fp, code, message, headers,
                                        newurl)


def _открыть(ссылка, сколько):
    """Скачивает начало страницы. Возвращает (байты, тип, конечный адрес)."""
    адрес = наружу(ссылка)
    if адрес is None:
        return None

    открывалка = urllib.request.build_opener(_Осторожно())
    открывалка.addheaders = []
    запрос = urllib.request.Request(адрес, headers={
        "User-Agent": ПРЕДСТАВЛЯЕМСЯ,
        "Accept-Language": "ru,en;q=0.8",
    })
    try:
        with открывалка.open(запрос, timeout=ЖДЁМ) as ответ:
            вид = (ответ.headers.get("Content-Type") or "").lower()
            обещано = ответ.headers.get("Content-Length")
            if обещано and обещано.isdigit() and int(обещано) > сколько * 4:
                return None
            return ответ.read(сколько), вид, ответ.geturl()
    except (urllib.error.URLError, OSError, ValueError,
            urllib.error.HTTPError):
        return None


class _Разбор(HTMLParser):
    """Вытаскивает из <head> то, что сайт сам о себе рассказывает."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.мета = {}
        self.заголовок = ""
        self._в_заголовке = False
        self.дочитали = False

    def handle_starttag(self, tag, attrs):
        if self.дочитали:
            return
        if tag == "title" and not self.заголовок:
            self._в_заголовке = True
            return
        if tag != "meta":
            return

        свойства = {имя.lower(): (значение or "") for имя, значение in attrs}
        имя = (свойства.get("property") or свойства.get("name") or "").lower()
        значение = свойства.get("content", "").strip()
        if имя and значение and имя not in self.мета:
            self.мета[имя] = значение

    def handle_endtag(self, tag):
        if tag == "title":
            self._в_заголовке = False
        elif tag == "head":
            # Ниже <head> смотреть незачем, а страницы бывают огромные
            self.дочитали = True

    def handle_data(self, data):
        if self._в_заголовке:
            self.заголовок += data


def _текстом(байты, вид):
    """Разбирает страницу в текст, доверяя кодировке из заголовков."""
    кодировка = "utf-8"
    if "charset=" in вид:
        кодировка = вид.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    try:
        return байты.decode(кодировка, "replace")
    except LookupError:
        return байты.decode("utf-8", "replace")


def read_meta(страница, откуда):
    """Заголовок, описание и адрес картинки — из разметки страницы."""
    разбор = _Разбор()
    try:
        разбор.feed(страница)
    except Exception:
        # Кривая разметка встречается чаще, чем хотелось бы
        pass

    мета = разбор.мета

    def первое(*имена):
        for имя in имена:
            если = мета.get(имя)
            if если:
                return html.unescape(если).strip()
        return ""

    заголовок = первое("og:title", "twitter:title") or разбор.заголовок.strip()
    описание = первое("og:description", "twitter:description", "description")
    картинка = первое("og:image", "og:image:url", "twitter:image",
                      "twitter:image:src")
    сайт = первое("og:site_name") or urllib.parse.urlsplit(откуда).hostname or ""

    if картинка:
        # Здесь только склеиваем: годность адреса проверит тот, кто пойдёт
        # за картинкой, — разбор разметки в сеть не ходит
        картинка = urllib.parse.urljoin(откуда, картинка)

    return {"title": заголовок[:200], "text": описание[:400],
            "image": картинка, "site": сайт[:80]}


def look(ссылка):
    """Ходит по ссылке и возвращает карточку или None.

    Работает блокирующе — на сервере зовётся из отдельного потока.
    """
    добыто = _открыть(ссылка, СТРАНИЦА)
    if добыто is None:
        return None

    байты, вид, конечный = добыто
    if "html" not in вид:
        return None

    карточка = read_meta(_текстом(байты, вид), конечный)
    if not карточка["title"] and not карточка["text"]:
        return None

    карточка["url"] = конечный
    return карточка


def picture(ссылка):
    """Скачивает картинку карточки. Возвращает байты или None."""
    добыто = _открыть(ссылка, КАРТИНКА)
    if добыто is None:
        return None

    байты, вид, _ = добыто
    if not байты or "image" not in вид:
        return None
    return байты
