"""Проверки телеграмного оформления: без сервера, только разбор и отрисовка."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402

# Настройки уводим в сторону, чтобы не трогать настоящие
import tempfile
store.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="velix-tg-"), "velix.json")
# Набор писался под русский интерфейс, а по умолчанию теперь
# английский: язык задаём явно
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


now = datetime.now(timezone.utc)
yesterday = now - timedelta(days=1)


def moment(base, hour, minute):
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


app = gui.VelixApp()
app._on_message({"type": "welcome", "token": "тест",
                 "user": {"id": 1, "login": "nina", "name": "Нина", "bio": "",
                          "avatar": None}})

app._on_message({"type": "history", "conversation": 1, "quotes": {},
                 "reactions": {}, "more": False, "before": None, "items": [
    {"nick": "Гоша", "at": moment(yesterday, 10, 0), "kind": "text", "text": "вчерашнее"},
    {"nick": "Гоша", "at": moment(now, 11, 0), "kind": "text", "text": "первое подряд"},
    {"nick": "Гоша", "at": moment(now, 11, 1), "kind": "text", "text": "второе подряд"},
    {"nick": "Лена", "at": moment(now, 11, 2), "kind": "text", "text": "а это другой автор"},
]})
app.update_idletasks()


def rows():
    return app.messages.winfo_children()


def labels_of(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, gui.ctk.CTkLabel):
            found.append(child.cget("text"))
        found.extend(labels_of(child))
    return found


all_labels = [text for row in rows() for text in labels_of(row)]

# 1. Плашки с датами
local_yesterday = datetime.fromisoformat(moment(yesterday, 10, 0)).astimezone()
expected = gui.i18n.month_day(local_yesterday.day, local_yesterday.month)
check("tg-date-pill-yesterday", expected in all_labels,
      [t for t in all_labels if len(t) < 20])
check("tg-date-pill-today", "Сегодня" in all_labels)
check("tg-date-pill-once", all_labels.count("Сегодня") == 1, all_labels.count("Сегодня"))

# 2. Группировка: плашка с датой серию разрывает, поэтому «Гоша» подписан дважды
check("tg-sender-name-twice", all_labels.count("Гоша") == 2,
      f"имя встречается {all_labels.count('Гоша')} раз")

grouped_row = next(row for row in rows() if "второе подряд" in labels_of(row))
check("tg-grouped-no-name", "Гоша" not in labels_of(grouped_row), labels_of(grouped_row))
check("tg-grouped-no-avatar", "Г" not in labels_of(grouped_row), labels_of(grouped_row))

first_row = next(row for row in rows() if "первое подряд" in labels_of(row))
check("tg-first-has-name", "Гоша" in labels_of(first_row), labels_of(first_row))
check("tg-first-has-avatar", "Г" in labels_of(first_row), labels_of(first_row))

check("tg-new-sender-shown", "Лена" in all_labels)

# 3. Время берётся из отметки сообщения, а не из «сейчас»
check("tg-time-from-message",
      datetime.fromisoformat(moment(now, 11, 2)).astimezone().strftime("%H:%M") in all_labels,
      [t for t in all_labels if ":" in t])

# 4. Цвет аватарки
check("tg-avatar-color-stable",
      gui.avatar_color("Гоша") == gui.avatar_color("Гоша")
      and gui.avatar_color("Гоша") in gui.AVATAR_COLORS)
check("tg-avatar-color-differs", gui.avatar_color("Гоша") != gui.avatar_color("Лена"))

# 5. Превью в строке чата слева
app.conversations = [{"id": 1, "title": "Общий чат", "last": None}]
app._bump_preview({"conversation": 1, "nick": "Гоша", "kind": "text",
                   "text": "очень длинное сообщение, которое точно не влезет",
                   "at": moment(now, 12, 0)}, notify=False)
app.update_idletasks()

side = [text for row in app.side_list.winfo_children() for text in labels_of(row)]
trimmed = [text for text in side if text.endswith("…")]
check("tg-preview-trimmed", trimmed and len(trimmed[0]) <= 30, side)

app._bump_preview({"conversation": 1, "nick": "Вы", "kind": "text",
                   "text": "моё сообщение", "at": moment(now, 12, 1)},
                  notify=False)
app.update_idletasks()
side = [text for row in app.side_list.winfo_children() for text in labels_of(row)]
check("tg-preview-own", any(text.startswith("Вы:") for text in side), side)

# 6. Своё сообщение — пузырь без аватарки
before = len(rows())
app._add_bubble("Нина", "проверка", own=True, time_text="12:02")
app.update_idletasks()
own_row = rows()[-1]
check("tg-own-no-avatar",
      not any(isinstance(c, gui.ctk.CTkLabel) and c.cget("text") == "Н"
              for c in own_row.winfo_children()),
      "у своего сообщения появилась аватарка")
check("tg-own-row-added", len(rows()) == before + 1)

# 7. Вложение в истории рисуется карточкой и просит содержимое у сервера
app._on_message({"type": "media", "id": 42, "media": "abc123", "conversation": 1,
                 "nick": "Лена", "kind": "video", "name": "ролик.mp4",
                 "size": 5 * 1024 * 1024, "at": moment(now, 11, 30)})
app.update_idletasks()
video_labels = labels_of(rows()[-1])
check("tg-video-card", any("ролик.mp4" in t for t in video_labels), video_labels)
check("tg-video-size-shown", any("МБ" in t for t in video_labels), video_labels)
check("tg-video-not-fetched", "abc123" not in app.pending_media,
      "видео потянулось само, хотя должно ждать нажатия")

app.destroy()
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
