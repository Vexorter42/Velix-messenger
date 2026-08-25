"""Учётные записи: пароли, проверки, токены.

Пароль на сервере не хранится — только результат scrypt с солью. Даже если
файл базы утечёт, восстановить из него пароли перебором дорого.

Работа с базой живёт в storage.py, здесь только чистые функции: посчитать
хеш, сверить пароль, проверить логин на пригодность.
"""

import base64
import hashlib
import hmac
import re
import secrets

# Логин — то, чем человек входит: латиница, цифры, точка, дефис, подчёркивание
LOGIN_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,24}$")

MIN_PASSWORD = 6
MAX_PASSWORD = 128
MAX_NAME = 32
MAX_BIO = 280

# Параметры scrypt. N=2^14 — примерно десятые доли секунды на малине:
# для входа незаметно, для перебора дорого.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32


def hash_password(password):
    """Считает хеш пароля вместе с солью и параметрами."""
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                         r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES)
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode(), base64.b64encode(key).decode(),
    ])


def verify_password(password, stored):
    """Сверяет пароль с сохранённым хешем."""
    try:
        algorithm, n, r, p, salt, key = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(password.encode("utf-8"),
                                salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError, AttributeError):
        return False

    # Сравнение постоянного времени: обычное == подсказывало бы подбирающему,
    # сколько первых байтов он угадал
    return hmac.compare_digest(actual, expected)


def new_token():
    """Токен сессии: клиент хранит его вместо пароля."""
    return secrets.token_urlsafe(32)


def new_invite():
    """Код приглашения: четыре группы по четыре знака, без похожих букв."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)


def new_recovery():
    """Код восстановления: тот же вид, что у приглашения.

    Почты мы не спрашиваем, а сервер стоит дома — восстанавливать пароль
    больше нечем. Код выдаётся один раз при регистрации; его хранят там же,
    где хранят ключи от квартиры.
    """
    return new_invite()


def clean_invite(code):
    """Приводит введённый код к единому виду: заглавные, дефисы на местах."""
    letters = "".join(character for character in str(code or "").upper()
                      if character.isalnum())
    return "-".join(letters[index:index + 4] for index in range(0, len(letters), 4))


# Коды ошибок для клиента: тот же текст он покажет на своём языке
def code_for(problem):
    """Код и подстановки для текста проверки логина или пароля."""
    if problem is None:
        return None, {}
    if problem.startswith("Логин"):
        return "bad_login", {}
    if "не короче" in problem:
        return "short_password", {"least": MIN_PASSWORD}
    if "длиннее" in problem:
        return "long_password", {"most": MAX_PASSWORD}
    return None, {}


def check_login(login):
    """Возвращает текст ошибки или None, если логин годится."""
    if not LOGIN_PATTERN.match(login or ""):
        return ("Логин: от 3 до 24 символов, латиница, цифры, точка, дефис"
                " или подчёркивание.")
    return None


def check_password(password):
    """Возвращает текст ошибки или None, если пароль годится."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"Пароль должен быть не короче {MIN_PASSWORD} символов."
    if len(password) > MAX_PASSWORD:
        return f"Пароль длиннее {MAX_PASSWORD} символов не принимаем."
    return None


def clean_name(name, fallback):
    """Имя, которое видят собеседники."""
    cleaned = str(name or "").strip().replace("\n", " ")[:MAX_NAME]
    # Квадратные скобки убираем: с ними имя не отличить от служебной пометки
    cleaned = cleaned.replace("[", "").replace("]", "").strip()
    return cleaned or fallback


def clean_bio(bio):
    """Рассказ о себе: несколько строк, без простыней."""
    lines = [line.strip() for line in str(bio or "").splitlines()]
    return "\n".join(lines).strip()[:MAX_BIO]
