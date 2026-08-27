"""Настройки, трей и автозапуск."""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store  # noqa: E402
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-set-")) / "velix.json"

import autostart  # noqa: E402
os.environ["VELIX_CACHE"] = tempfile.mkdtemp(prefix="velix-cache-")

import gui  # noqa: E402
import tray as tray_module  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# --- автозапуск. Пишем во временную ветку реестра, настоящую не трогаем
TEST_KEY = r"Software\Velix\ТестАвтозапуска"

check("autostart-supported", autostart.supported(), "не Windows?")
check("autostart-clean-start", not autostart.is_enabled(TEST_KEY), "ветка не пуста")

check("autostart-enable", autostart.enable(TEST_KEY) is None)
check("autostart-now-on", autostart.is_enabled(TEST_KEY))

command = autostart.command()
check("autostart-command-quoted", command.startswith('"') and command.endswith('"'),
      command)
check("autostart-command-points-at-velix",
      "Velix" in command or "gui.py" in command, command)

check("autostart-disable", autostart.disable(TEST_KEY) is None)
check("autostart-now-off", not autostart.is_enabled(TEST_KEY))
check("autostart-disable-twice", autostart.disable(TEST_KEY) is None,
      "повторное выключение вернуло ошибку")

# настоящую ветку автозапуска мы не тронули
import winreg  # noqa: E402
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Velix") as key:
        winreg.DeleteKey(key, "ТестАвтозапуска")
except OSError:
    pass
try:
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Velix")
except OSError:
    pass

# --- значок в трее
icon_image = tray_module.make_icon_image(str(REPO / "icon.ico"))
check("tray-icon-from-file", icon_image.size == (64, 64), icon_image.size)
fallback = tray_module.make_icon_image("нет-такого-файла.ico")
check("tray-icon-fallback", fallback.size == (64, 64), fallback.size)

opened, quitted = [], []
icon = tray_module.Tray(on_open=lambda: opened.append(1),
                        on_quit=lambda: quitted.append(1))
check("tray-available", icon.available, "pystray не установлен")

# --- окно и настройки
app = gui.VelixApp()
app._on_message({"type": "welcome", "token": "тест",
                 "user": {"id": 1, "login": "nina", "name": "Нина", "bio": "",
                          "avatar": None}})
app.update()


def run():
    check("settings-defaults", app.settings["theme"] in ("dark", "light")
          and app.settings["tray"] is True, app.settings)

    app._show_settings()
    app.update()
    check("settings-screen-shown", app.settings_view.winfo_ismapped())
    check("settings-theme-switch-matches",
          bool(app.theme_switch.get()) == (app.settings["theme"] == "dark"),
          (app.theme_switch.get(), app.settings["theme"]))

    # --- переключаем тему
    app.theme_switch.deselect()
    app._on_theme_switch()
    app.update()
    check("settings-theme-light", app.settings["theme"] == "light", app.settings)
    check("settings-theme-applied", gui.ctk.get_appearance_mode() == "Light",
          gui.ctk.get_appearance_mode())
    check("settings-theme-saved",
          store.load(store.CONFIG_PATH)["settings"]["theme"] == "light",
          store.load(store.CONFIG_PATH))

    app.theme_switch.select()
    app._on_theme_switch()
    check("settings-theme-back-to-dark", gui.ctk.get_appearance_mode() == "Dark")

    # --- трей
    app.tray_switch.deselect()
    app._on_tray_switch()
    check("settings-tray-off-saved",
          store.load(store.CONFIG_PATH)["settings"]["tray"] is False,
          store.load(store.CONFIG_PATH)["settings"])

    # с выключенным треем закрытие окна означает выход
    app.settings["tray"] = True
    app.tray_switch.select()
    app._on_tray_switch()
    check("settings-tray-on-saved",
          store.load(store.CONFIG_PATH)["settings"]["tray"] is True)

    # --- закрытие прячет окно, а не убивает его
    app._show_chat()
    app.update()
    app._on_close()
    app.update()
    check("close-hides-window", app.state() == "withdrawn", app.state())
    check("close-keeps-app-alive", app.winfo_exists() == 1)
    check("close-shows-tray-icon", app.tray.icon is not None, "значок не появился")

    # --- новое сообщение, пока окно спрятано
    app._notify_if_hidden("Лена", "как дела?")
    check("hidden-notify-safe", True)

    # --- возврат из трея
    app._restore_window()
    app.update()
    check("restore-shows-window", app.state() == "normal", app.state())
    check("restore-hides-icon", app.tray.icon is None, "значок остался")

    # --- настройки переживают перезапуск
    saved = store.load(store.CONFIG_PATH)["settings"]
    merged = dict(gui.DEFAULT_SETTINGS, **saved)
    check("settings-survive-restart", merged["tray"] is True
          and merged["theme"] == "dark", merged)

    app._quit()


app.after(600, run)
app.mainloop()

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
