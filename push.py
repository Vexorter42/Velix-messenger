"""Уведомления на телефон, когда приложение закрыто.

Работает через Web Push: браузер выдаёт подписку, сервер шлёт по ней
сообщение через службу самого браузера (Google, Mozilla, Apple), и телефон
показывает уведомление, даже если вкладка закрыта.

Для этого нужна пара ключей VAPID: приватный остаётся на сервере, публичный
уходит в браузер при подписке. Ключи создаются один раз и лежат рядом с базой.
"""

import base64
import json
import os
from pathlib import Path

KEY_PATH = Path(os.environ.get("VELIX_PUSH_KEYS")
                or Path(__file__).with_name("push-keys.json"))

# Служба доставки требует контакт отправителя. Настоящая почта тут не нужна.
CONTACT = os.environ.get("VELIX_PUSH_CONTACT") or "mailto:velix@localhost"

try:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
except ImportError:  # без библиотеки просто не будет уведомлений
    Vapid01 = None
    webpush = None
    WebPushException = Exception


def available():
    return webpush is not None


def _encode(number, length):
    return number.to_bytes(length, "big")


def load_keys():
    """Возвращает (приватный ключ в PEM, публичный в base64) или None."""
    if Vapid01 is None:
        return None

    if KEY_PATH.exists():
        try:
            stored = json.loads(KEY_PATH.read_text(encoding="utf-8"))
            return stored["private"], stored["public"]
        except (OSError, ValueError, KeyError):
            pass

    vapid = Vapid01()
    vapid.generate_keys()

    private = vapid.private_pem().decode("utf-8")
    numbers = vapid.public_key.public_numbers()
    raw = b"\x04" + _encode(numbers.x, 32) + _encode(numbers.y, 32)
    public = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    try:
        KEY_PATH.write_text(json.dumps({"private": private, "public": public},
                                       ensure_ascii=False, indent=2),
                            encoding="utf-8")
        KEY_PATH.chmod(0o600)
    except OSError:
        pass  # не сохранили — при следующем запуске ключи будут другими

    return private, public


def public_key():
    keys = load_keys()
    return keys[1] if keys else None


def send(subscription, title, body, tag=""):
    """Отправляет одно уведомление. Возвращает текст ошибки или None.

    Отдельный случай — 404 и 410: подписка протухла, её надо забыть.
    """
    keys = load_keys()
    if keys is None or webpush is None:
        return "уведомления недоступны"

    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body, "tag": tag},
                            ensure_ascii=False),
            vapid_private_key=keys[0],
            vapid_claims={"sub": CONTACT},
            timeout=10,
        )
    except WebPushException as error:
        response = getattr(error, "response", None)
        if response is not None and response.status_code in (404, 410):
            return "gone"
        return str(error)
    except Exception as error:  # сеть отвалилась, служба недоступна
        return str(error)
    return None
