"""Запуск Velix вместе с Windows.

Автозапуск в Windows — это строка в реестре пользователя, в ветке
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run. Права администратора
для неё не нужны, чужие программы она не трогает, а убрать её можно тем же
переключателем в настройках.

На других системах функции ничего не делают и честно сообщают об этом.
"""

import sys
from pathlib import Path

from i18n import t

VALUE_NAME = "Velix"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

WINDOWS = sys.platform == "win32"

if WINDOWS:
    import winreg


def supported():
    """Умеем ли мы вообще прописывать автозапуск на этой системе."""
    return WINDOWS


def command():
    """Что именно должно запускаться при входе в систему."""
    if getattr(sys, "frozen", False):
        # Собранный exe — запускаем его самого
        return f'"{Path(sys.executable)}"'

    # Запуск из исходников: pythonw не открывает лишнее окно консоли
    launcher = Path(sys.executable)
    quiet = launcher.with_name("pythonw.exe")
    if quiet.exists():
        launcher = quiet
    return f'"{launcher}" "{Path(__file__).with_name("gui.py")}"'


def is_enabled(key_path=RUN_KEY):
    """Прописан ли автозапуск сейчас."""
    if not WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def enable(key_path=RUN_KEY):
    """Добавляет Velix в автозапуск. Возвращает текст ошибки или None."""
    if not WINDOWS:
        return t("Автозапуск умеет настраиваться только в Windows.")
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
    except OSError as error:
        return t("Не удалось прописать автозапуск: {error}", error=error)
    return None


def disable(key_path=RUN_KEY):
    """Убирает Velix из автозапуска. Возвращает текст ошибки или None."""
    if not WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return None  # уже не прописан, всё в порядке
    except OSError as error:
        return t("Не удалось убрать автозапуск: {error}", error=error)
    return None


def apply(enabled, key_path=RUN_KEY):
    """Приводит реестр к желаемому состоянию."""
    return enable(key_path) if enabled else disable(key_path)
