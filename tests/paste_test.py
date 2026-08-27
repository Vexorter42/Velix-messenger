"""Ctrl+V, Ctrl+A, Ctrl+C, Ctrl+X при любой раскладке."""
import os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import gui

results = []
def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")

app = gui.VelixApp()
app.update()


class FakeEvent:
    """Ctrl+клавиша с кодом, как приходит от Windows независимо от раскладки."""
    def __init__(self, widget, keycode):
        self.widget = widget
        self.keycode = keycode


def put(text):
    app.clipboard_clear()
    app.clipboard_append(text)
    app.update()


def run():
    field = app.server_entry
    field.focus_set()          # как будто пользователь щёлкнул в поле
    app.update()
    inner = field._entry

    put("velix.vexorter.duckdns.org")
    handled = app._on_entry_shortcut(FakeEvent(inner, 86))
    check("paste-v-works", field.get() == "velix.vexorter.duckdns.org", repr(field.get()))
    check("paste-consumes-event", handled == "break", handled)

    # выделить всё и заменить вставкой
    app._on_entry_shortcut(FakeEvent(inner, 65))
    put("192.168.0.225")
    app._on_entry_shortcut(FakeEvent(inner, 86))
    check("paste-replaces-selection", field.get() == "192.168.0.225", repr(field.get()))

    # копирование
    app._on_entry_shortcut(FakeEvent(inner, 65))
    put("мусор")
    app._on_entry_shortcut(FakeEvent(inner, 67))
    app.update()
    check("copy-works", app.clipboard_get() == "192.168.0.225", repr(app.clipboard_get()))

    # вырезание
    app._on_entry_shortcut(FakeEvent(inner, 65))
    app._on_entry_shortcut(FakeEvent(inner, 88))
    check("cut-clears-field", field.get() == "", repr(field.get()))
    app.update()
    check("cut-copies-too", app.clipboard_get() == "192.168.0.225", repr(app.clipboard_get()))

    # прочие сочетания оставляем системе
    check("other-keys-untouched", app._on_entry_shortcut(FakeEvent(inner, 90)) is None)

    # перевод строки из буфера не ломает однострочное поле
    put("две\nстроки")
    app._on_entry_shortcut(FakeEvent(inner, 86))
    check("paste-flattens-newlines",
          field.get() == "две строки", repr(field.get()))

    # то же самое в поле сообщения: текстовый буфер вставляется как текст
    app._show_chat()          # поле сообщения живёт на экране чата
    app.update()
    message = app.message_entry
    message.focus_set()
    app.update()
    put("привет из буфера")
    app._on_ctrl_key(FakeEvent(message._entry, 86))
    check("paste-in-message-entry", message.get() == "привет из буфера", repr(message.get()))

    app.destroy()


app.after(500, run)
app.mainloop()
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
