"""Обновление Velix на месте, без переустановки.

Сервер держит рядом с собой свежий Velix.exe и говорит клиенту, какая версия
доступна. Клиент забирает файл по тому же соединению, что и вложения, и
подменяет себя.

Хитрость подмены в том, что запущенный exe нельзя перезаписать, зато можно
переименовать. Поэтому старый файл отъезжает в сторону под именем *.old,
новый встаёт на его место, программа перезапускается и при следующем старте
подчищает отложенный файл.
"""

import os
import subprocess
import sys
from pathlib import Path

from i18n import t

OLD_SUFFIX = ".old"


def running_as_exe():
    """Запущены ли мы из собранного PyInstaller'ом файла."""
    return getattr(sys, "frozen", False)


def executable_path():
    return Path(sys.executable)


def cleanup(folder=None):
    """Убирает файлы, оставшиеся от прошлого обновления."""
    folder = Path(folder or executable_path().parent)
    removed = 0
    for leftover in folder.glob(f"*{OLD_SUFFIX}"):
        try:
            leftover.unlink()
            removed += 1
        except OSError:
            pass  # файл ещё занят — попробуем в следующий раз
    return removed


def swap(current, data):
    """Ставит новые байты на место current. Возвращает ошибку или None.

    Порядок важен: сначала уводим работающий файл в сторону, и только потом
    занимаем его имя. Если что-то пошло не так на втором шаге, возвращаем
    старый файл на место — остаться совсем без программы нельзя.
    """
    current = Path(current)
    fresh = current.with_name(current.name + ".new")
    retired = current.with_name(current.name + OLD_SUFFIX)

    try:
        fresh.write_bytes(data)
    except OSError as error:
        return t("не удалось записать новый файл: {error}", error=error)

    try:
        if retired.exists():
            retired.unlink()
    except OSError:
        retired = current.with_name(f"{current.name}.{os.getpid()}{OLD_SUFFIX}")

    try:
        os.replace(current, retired)
    except OSError as error:
        fresh.unlink(missing_ok=True)
        return t("не удалось освободить место под новую версию: {error}",
                 error=error)

    try:
        os.replace(fresh, current)
    except OSError as error:
        os.replace(retired, current)  # возвращаем как было
        return t("не удалось поставить новую версию: {error}", error=error)

    return None


def restart(path=None):
    """Запускает обновлённую программу и просит текущую завершиться."""
    path = Path(path or executable_path())
    try:
        subprocess.Popen([str(path)], close_fds=True)
    except OSError as error:
        return t("новая версия установлена, но не запустилась: {error}",
                 error=error)
    return None
