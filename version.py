"""Версия Velix — одна на клиент, сервер и обновления.

При выпуске новой версии число меняется здесь и в installer.iss.
"""

VERSION = "1.9.0"


def as_tuple(text):
    """Превращает "1.4.0" в (1, 4, 0). Мусор становится нулями."""
    parts = []
    for piece in str(text or "").split("."):
        digits = "".join(character for character in piece if character.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(candidate, current=VERSION):
    """Правда ли, что candidate новее current."""
    return as_tuple(candidate) > as_tuple(current)
