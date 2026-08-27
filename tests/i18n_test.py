"""Язык интерфейса: словарь, переключение и полнота перевода.

Главная проверка — последняя: любая русская строка, показанная человеку,
должна быть завёрнута в t() и лежать в английском словаре. Иначе английский
интерфейс окажется наполовину русским.
"""

import ast
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import i18n  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ------------------------------------------------------------- сам словарь

check("i18n-default-english", i18n.DEFAULT == "en" and i18n.language() == "en",
      i18n.language())
check("i18n-translates", i18n.t("Настройки") == "Settings", i18n.t("Настройки"))
check("i18n-substitutes",
      i18n.t("Версия {version}", version="1.8.0") == "Version 1.8.0",
      i18n.t("Версия {version}", version="1.8.0"))
check("i18n-unknown-stays", i18n.t("Такой строки нет") == "Такой строки нет")
# Ключ идёт только позиционно: среди подстановок есть и {text}
check("i18n-text-placeholder",
      i18n.t("Ответ {name}: {text}", name="Лена", text="привет")
      == "Reply to Лена: привет",
      i18n.t("Ответ {name}: {text}", name="Лена", text="привет"))
check("i18n-system-placeholder",
      i18n.t("[Система]: {text}", text="ага") == "[System]: ага",
      i18n.t("[Система]: {text}", text="ага"))

i18n.set_language("ru")
check("i18n-russian-identity", i18n.t("Настройки") == "Настройки")
check("i18n-russian-substitutes",
      i18n.t("Версия {version}", version="1.8.0") == "Версия 1.8.0")
check("i18n-month-ru", i18n.month_day(5, 3) == "5 марта", i18n.month_day(5, 3))

i18n.set_language("en")
check("i18n-month-en", i18n.month_day(5, 3) == "March 5", i18n.month_day(5, 3))
check("i18n-bad-code-falls-back", i18n.set_language("de") == "en")

# --- сообщения сервера приходят кодом
i18n.set_language("en")
check("i18n-server-code",
      i18n.from_server({"code": "bad_credentials", "text": "Неверный логин или пароль."})
      == "Wrong username or password.",
      i18n.from_server({"code": "bad_credentials"}))
check("i18n-server-args",
      "5 min" in i18n.from_server({"code": "locked_out", "args": {"minutes": 5}}),
      i18n.from_server({"code": "locked_out", "args": {"minutes": 5}}))
check("i18n-server-old-server",
      i18n.from_server({"text": "что-то своё"}) == "что-то своё")
i18n.set_language("ru")
check("i18n-server-russian",
      i18n.from_server({"code": "bad_credentials"}) == "Неверный логин или пароль.")
i18n.set_language("en")

check("i18n-server-codes-translated",
      all(template in i18n.ENGLISH for template in i18n.SERVER_MESSAGES.values()),
      [code for code, template in i18n.SERVER_MESSAGES.items()
       if template not in i18n.ENGLISH])


# ------------------------------------------------- полнота перевода в коде

# Согласие пользователь набирает сам: русское «да» принимается на любом
# языке интерфейса и на экран не выводится
ANSWERS = {"д", "да"}


def cyrillic(text):
    return any("А" <= letter <= "я" or letter in "Ёё" for letter in text)


def translated_calls(tree):
    """Строки, отданные в t(...) — вместе с ключами из словарей вроде KIND_LABEL."""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "t":
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    keys.add(argument.value)
    return keys


def missed(path):
    """Русские строки, которые показываются человеку мимо перевода."""
    source = io.open(path, encoding="utf-8").read()
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))

    wrapped = translated_calls(tree)
    escaped = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or not cyrillic(node.value):
            continue
        if node.value in wrapped or node.value in ANSWERS:
            continue
        escaped.append((node.lineno, node.value))
    return escaped, wrapped


for name in ("gui.py", "client.py", "tray.py", "updates.py", "autostart.py"):
    escaped, wrapped = missed(REPO / name)
    check(f"i18n-{name}-all-wrapped", not escaped,
          [f"строка {line}: {text[:40]}" for line, text in escaped[:6]])

    unknown = [key for key in wrapped if key not in i18n.ENGLISH]
    check(f"i18n-{name}-all-in-dictionary", not unknown, unknown[:6])

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
