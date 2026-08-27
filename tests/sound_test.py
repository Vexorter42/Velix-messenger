"""Звук о новом сообщении: волна считается и играет, но не на чужое эхо."""

import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import chime  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


# ---------------------------------------------------------- сама волна

данные = chime._собрать()
check("chime-is-wav", данные[:4] == b"RIFF", данные[:8])

холст = Path(__file__).with_name("chime-proba.wav")
холст.write_bytes(данные)
with wave.open(str(холст), "rb") as файл:
    check("chime-mono", файл.getnchannels() == 1, файл.getnchannels())
    check("chime-16-bit", файл.getsampwidth() == 2, файл.getsampwidth())
    длительность = файл.getnframes() / файл.getframerate()
    check("chime-short", 0.1 < длительность < 0.35, длительность)
холст.unlink()

# Тишины быть не должно: волна с ненулевым размахом
куски = [данные[место:место + 2] for место in range(44, len(данные), 2)]
самое = max(int.from_bytes(one, "little", signed=True) for one in куски)
check("chime-not-silent", самое > 3000, самое)

# ------------------------------------------------- играется без ошибок

if chime.available():
    check("chime-plays", chime.play() is True)
    check("chime-file-kept", Path(chime._где_лежит()).exists(),
          chime._где_лежит())
else:
    check("chime-silent-elsewhere", chime.play() is False,
          "на этой системе звука нет — и это не ошибка")

# ------------------------------------- окно не звучит на собственное эхо

источник = (REPO / "gui.py").read_text(encoding="utf-8")
место = источник.index("def _on_incoming")
кусок = источник[место:место + 700]
check("chime-not-on-own",
      'message.get("user") != self.user.get("id")' in кусок
      and "chime.play()" in кусок, кусок[:200])
check("chime-switch-exists", '"sound": True' in источник
      and "sound_switch" in источник)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
