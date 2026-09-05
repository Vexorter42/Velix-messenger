"""Лента рисуется хвостом, а не целиком.

Пузырь в CustomTkinter стоит около двадцати миллисекунд: почти тысяча
обращений к Tcl, и львиная доля их — на скруглённые углы, каждый из которых
рисуется отдельными фигурами на холсте. Полсотни сообщений разом — это
полторы секунды замершего окна при каждом входе в переписку, а видно всё
равно десяток.

Поэтому рисуется хвост, а остальное лежит при клиенте и ждёт кнопки — той
самой, что и раньше была для лежащего на сервере. Проверяем и это, и то,
что на сервер за уже принесённым второй раз не ходят.

Сервер тут не нужен: лента набивается вызовами напрямую, как её набивает
пришедший кадр.
"""

import os
import sys
import tempfile
from pathlib import Path

import harness

REPO = Path(os.environ.get("VELIX_SRC")
            or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO))
os.chdir(REPO)

уголок = Path(tempfile.mkdtemp(prefix="velix-feed-"))
import store  # noqa: E402
store.CONFIG_PATH = уголок / "velix.json"
store.save({"settings": {"language": "ru"}})

import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


def история(сколько, more=False, before=None):
    """Кадр истории, какой его присылает сервер."""
    return {
        "type": "history", "conversation": 7, "more": more, "before": before,
        "items": [{"id": н + 1, "kind": "text",
                   "user": 2 if н % 2 else 1,
                   "nick": "Лена" if н % 2 else "Гоша",
                   "text": f"сообщение номер {н + 1}",
                   "at": "2026-09-05T18:00:00"} for н in range(сколько)],
    }


def подписи(app):
    """Весь текст, который сейчас нарисован в ленте."""
    найдено = []
    к_обходу = list(app.messages.winfo_children())
    while к_обходу:
        какой = к_обходу.pop(0)
        текст = None
        try:
            текст = какой.cget("text")
        except Exception:
            pass
        if текст:
            найдено.append(текст)
        к_обходу.extend(какой.winfo_children())
    return найдено


app = gui.VelixApp()
harness.тихое_окно(app)
app.update()
app.user = {"id": 1, "name": "Гоша"}
app.conversation = 7

# ------------------------------------------------ длинная история: только хвост

app._show_history(история(50))
app.update()

нарисовано = len(app.loaded_items) - app.feed_from
check("feed-draws-only-the-tail", нарисовано == gui.ЛЕНТА_СРАЗУ, нарисовано)
check("feed-keeps-everything-it-got", len(app.loaded_items) == 50,
      len(app.loaded_items))

видно = подписи(app)
check("feed-shows-the-newest", "сообщение номер 50" in видно, видно[-3:])
check("feed-hides-the-oldest", "сообщение номер 1" not in видно)
check("feed-offers-older", app.older_button is not None
      and app.older_button.winfo_exists())

# Просить у сервера будем от самого старого, что при нас есть, а не от
# самого старого нарисованного: иначе середина истории потерялась бы
check("feed-asks-from-the-oldest-held", app.oldest == 1, app.oldest)

# ------------------------------------- за уже принесённым на сервер не ходим

ушло = []
app.network.send = lambda кадр: ушло.append(кадр) or True

app._load_older()
app.update()
check("older-button-does-not-ask-the-server", ушло == [], ушло)

стало = len(app.loaded_items) - app.feed_from
check("older-button-shows-more", стало == gui.ЛЕНТА_СРАЗУ + gui.ЛЕНТА_ЕЩЁ,
      стало)
check("older-button-keeps-the-newest",
      "сообщение номер 50" in подписи(app))

# Дощёлкиваем до начала — теперь своего не осталось, и вот теперь на сервер
app._load_older()
app.update()
check("feed-opens-to-the-very-start", app.feed_from == 0, app.feed_from)
check("feed-shows-the-oldest-in-the-end",
      "сообщение номер 1" in подписи(app))

# Сервер сказал, что больше у него нет — значит, и кнопки быть не должно
check("older-button-gone-when-nothing-left",
      app.older_button is None or not app.older_button.winfo_exists())

# ------------------------------------------- короткая история рисуется целиком

app._show_history(история(6))
app.update()
check("short-feed-drawn-whole", app.feed_from == 0, app.feed_from)
check("short-feed-has-no-button",
      app.older_button is None or not app.older_button.winfo_exists())

# ------------------------------- пришедшее следом видно, хоть хвост и обрезан

app._show_history(история(50))
app.update()
app.loaded_items.append({"id": 777, "kind": "text", "user": 2, "nick": "Лена",
                         "text": "свежее сообщение", "at": "2026-09-05T18:05:00"})
app._show_item(app.loaded_items[-1])
app.update()
check("fresh-message-is-drawn", "свежее сообщение" in подписи(app))

# Перерисовка не должна его проглотить: отсчёт идёт от начала списка, а
# приходящее падает в конец
app._redraw_feed()
app.update()
check("fresh-message-survives-a-redraw", "свежее сообщение" in подписи(app),
      подписи(app)[-3:])

app.destroy()
import shutil  # noqa: E402
shutil.rmtree(уголок, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
