"""Версия Velix — одна на клиент, сервер и обновления.

При выпуске новой версии число меняется здесь, в installer.iss и в
android/AndroidManifest.xml.

Номер выглядит как 0.2.2.0: ведущий ноль — знак, что до первой круглой
версии дело ещё не дошло. Считать его не нужно, поэтому при сравнении
он отбрасывается: 0.2.2.0 и 2.2.0 — одно и то же.

Крупные новшества меняют вторую цифру (0.2.1.0 → 0.2.2.0), мелкие и
средние — последнюю (0.2.2.0 → 0.2.2.1).
"""

VERSION = "0.2.9.0"


def as_tuple(text):
    """Превращает "0.2.2.1" в (2, 2, 1). Мусор становится нулями."""
    parts = []
    for piece in str(text or "").split("."):
        digits = "".join(character for character in piece if character.isdigit())
        parts.append(int(digits) if digits else 0)

    # Ведущий ноль — украшение, а не число: без этого 0.2.2.0 оказалось бы
    # старше 2.1.0, и старые клиенты не увидели бы обновления
    while len(parts) > 1 and parts[0] == 0:
        parts.pop(0)

    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(candidate, current=VERSION):
    """Правда ли, что candidate новее current."""
    return as_tuple(candidate) > as_tuple(current)
