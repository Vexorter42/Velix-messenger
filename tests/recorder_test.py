"""Запись голоса и кружочка: чем пишем и что именно просим у ffmpeg.

Микрофон здесь не включаем: настоящая запись — дело живой машины, а тут
проверяется то, что от неё не зависит. Что ffmpeg нашёлся там, где лежит.
Что голос просят в opus, а кружочек — квадратом со звуком. И что запись без
микрофона честно отвечает отказом, а не молча пишет тишину.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import recorder  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


if os.name != "nt":
    print("TEST recorder-needs-windows: SKIP — запись пока только в Windows")
    print("\nИТОГО: 0/0 проверок прошли")
    sys.exit(0)

if not recorder.available():
    print("TEST recorder-needs-ffmpeg: SKIP — ffmpeg рядом не нашёлся")
    print("\nИТОГО: 0/0 проверок прошли")
    sys.exit(0)

check("recorder-found-ffmpeg", recorder.FFMPEG and recorder.FFMPEG.exists(),
      recorder.FFMPEG)

# --------------------------------------------------- перечень устройств

микрофоны = recorder.microphones()
камеры = recorder.cameras()
check("recorder-lists-devices", isinstance(микрофоны, list)
      and isinstance(камеры, list))
check("recorder-devices-have-names",
      all(одно.get("id") and одно.get("name") for одно in микрофоны + камеры),
      микрофоны + камеры)

# Запомненное устройство выбирается, даже если оно не первое в списке
if len(микрофоны) > 1:
    check("recorder-remembers-the-choice",
          recorder.pick_microphone(микрофоны[-1]["id"]) == микрофоны[-1]["id"])
    check("recorder-falls-back-to-the-first",
          recorder.pick_microphone("такого-нет") == микрофоны[0]["id"])
elif микрофоны:
    check("recorder-picks-the-only-one",
          recorder.pick_microphone(None) == микрофоны[0]["id"])

# --------------------------------------------- о чём просим ffmpeg
#
# Саму запись не запускаем: собираем ту же строку запуска и смотрим на неё.

песочница = Path(tempfile.mkdtemp(prefix="velix-rec-"))


class Немая(recorder.Recording):
    """Всё как в настоящей записи, только ffmpeg не запускается."""

    def __init__(self, kind, microphone, camera=None, folder=None):
        self.kind = kind
        self.path = Path(folder or песочница) / ("проба.ogg" if kind == "voice"
                                                 else "проба.mp4")
        self.error = None
        self.process = None
        self.команда = self._команда(microphone, camera)


голос = Немая("voice", "audio-device")
check("voice-asks-for-opus", "libopus" in голос.команда, голос.команда)
check("voice-is-mono", "-ac" in голос.команда
      and голос.команда[голос.команда.index("-ac") + 1] == "1", голос.команда)
check("voice-has-a-limit", str(recorder.MAX_VOICE) in голос.команда, голос.команда)
check("voice-takes-no-camera",
      not any("video=" in one for one in голос.команда), голос.команда)

кружок = Немая("circle", "audio-device", "video-device")
check("circle-takes-the-camera",
      any(one == "video=video-device" for one in кружок.команда), кружок.команда)
check("circle-takes-the-microphone",
      any(one == "audio=audio-device" for one in кружок.команда), кружок.команда)
check("circle-is-square",
      any(f"scale={recorder.CIRCLE_SIDE}:{recorder.CIRCLE_SIDE}" in one
          for one in кружок.команда), кружок.команда)
check("circle-crops-the-middle",
      any("crop=" in one for one in кружок.команда), кружок.команда)
check("circle-has-sound", "aac" in кружок.команда, кружок.команда)
check("circle-has-a-shorter-limit", str(recorder.MAX_CIRCLE) in кружок.команда,
      кружок.команда)

# ------------------------------------------ без устройства не пишем

пустая = recorder.Recording("voice", None, folder=песочница)
check("recorder-refuses-without-a-microphone", пустая.error is not None,
      пустая.error)
check("recorder-writes-nothing-then", not пустая.path.exists())

без_камеры = recorder.Recording("circle", "audio-device", None, folder=песочница)
check("recorder-refuses-a-circle-without-a-camera",
      без_камеры.error is not None, без_камеры.error)

import shutil  # noqa: E402

shutil.rmtree(песочница, ignore_errors=True)

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
