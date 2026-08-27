"""Кнопка обновления в настройках: что она показывает и что делает."""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-upd-gui-")) / "velix.json"
# Набор писался под русский интерфейс, а по умолчанию теперь
# английский: язык задаём явно
store.save({"settings": {"language": "ru"}})

os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402
import updates  # noqa: E402
import version  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# Подменяем «себя»: настоящий python.exe трогать нельзя
folder = Path(tempfile.mkdtemp(prefix="velix-fake-exe-"))
fake_exe = folder / "Velix.exe"
fake_exe.write_bytes(b"MZ" + b"\x00" * 200)

updates.running_as_exe = lambda: True
updates.executable_path = lambda: fake_exe
restarted = []
updates.restart = lambda path=None: restarted.append(path or fake_exe)

app = gui.VelixApp()
app._on_message({"type": "welcome", "token": "t",
                 "user": {"id": 1, "login": "gosha", "name": "Гоша", "bio": "",
                          "avatar": None}})
quits = []
app._quit = lambda: quits.append(1)


def run():
    # --- сервер ничего не предлагает
    app.available_update = None
    app._show_settings()
    app.update()
    check("update-button-idle",
          "последняя версия" in app.update_button.cget("text")
          and app.update_button.cget("state") == "disabled",
          app.update_button.cget("text"))
    check("update-version-shown", version.VERSION in app.version_label.cget("text"),
          app.version_label.cget("text"))

    # --- сервер предлагает старую версию
    app.available_update = {"version": "0.1.0", "size": 100}
    app._refresh_update_button()
    check("update-ignores-older", app.update_button.cget("state") == "disabled",
          app.update_button.cget("text"))

    # --- сервер предлагает свежую
    app.available_update = {"version": "9.9.9", "size": 5 * 1024 * 1024}
    app._refresh_update_button()
    text = app.update_button.cget("text")
    check("update-offers-newer", app.update_button.cget("state") == "normal"
          and "9.9.9" in text and "МБ" in text, text)

    # --- нажатие без связи с сервером
    app._on_update()
    check("update-needs-connection", "Нет связи" in app.settings_hint.cget("text"),
          app.settings_hint.cget("text"))

    # --- пришёл пустой файл
    app._install_update({"type": "update_blob", "version": "9.9.9", "data": b""})
    check("update-rejects-empty", "пустой" in app.settings_hint.cget("text"),
          app.settings_hint.cget("text"))
    check("update-file-untouched", fake_exe.read_bytes().startswith(b"MZ" + b"\x00"),
          "файл подменили пустышкой")

    # --- пришла настоящая сборка
    fresh = b"MZ" + "свежая сборка".encode("utf-8") + b"\x00" * 50
    app._install_update({"type": "update_blob", "version": "9.9.9", "data": fresh})
    app.update()
    check("update-installs", fake_exe.read_bytes() == fresh, "файл не подменился")
    check("update-keeps-old-copy", (folder / "Velix.exe.old").exists(),
          "старая версия не отложена")
    check("update-restarts", len(restarted) == 1, restarted)
    check("update-quits-after", len(quits) == 1, quits)
    check("update-tells-user", "перезапуск" in app.settings_hint.cget("text").lower(),
          app.settings_hint.cget("text"))

    app.destroy()


app.after(600, run)
app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
