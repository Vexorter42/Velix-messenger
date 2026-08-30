# -*- mode: python ; coding: utf-8 -*-
#
# Сборка окна Velix.
#
#     python -m PyInstaller --noconfirm Velix.spec
#
# Всё, что нужно знать сборщику, лежит здесь, а не в длинной строке запуска:
# так сборка повторяется одинаково и видно, что именно выкинуто и почему.

from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all

datas = [('icon.ico', '.')]
binaries = []
hiddenimports = []

# customtkinter и darkdetect везут с собой темы и данные, ffpyplayer — ffmpeg
# и SDL: без них видео не заиграет прямо в окне
for пакет in ('customtkinter', 'darkdetect', 'ffpyplayer'):
    свои_данные, свои_двоичные, свои_ввозы = collect_all(пакет)
    datas += свои_данные
    binaries += свои_двоичные
    hiddenimports += свои_ввозы

# Чего в окне нет и быть не должно.
#
# numpy сборщик прихватывает за компанию — Pillow умеет с ним дружить, если
# он есть, а вместе с ним приезжает openblas на двадцать мегабайт. Ни одна
# строчка Velix numpy не ввозит, проверено: без него всё то же самое.
ЛИШНИЕ_ПАКЕТЫ = [
    'numpy', 'scipy', 'pandas', 'matplotlib', 'IPython', 'notebook',
    'pytest', 'setuptools', 'pip', 'wheel',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
]

# Отдельные файлы, которые ни на что не влияют:
#   _avif      — картинки в формате AVIF, восемь мегабайт ради того, чего
#                в переписке не бывает; Pillow без него просто не умеет AVIF
#   ffplay/ffprobe — готовые программы для просмотра и разбора файлов. Играем
#                мы библиотекой, а не запуском чужого проигрывателя.
#
# А вот сам ffmpeg.exe нужен: им записываются голосовые и кружочки. Весит он
# 380 килобайт, потому что вся тяжесть — в тех же av*.dll, что уже лежат рядом
ЛИШНИЕ_ФАЙЛЫ = {'ffplay.exe', 'ffprobe.exe'}
ЛИШНИЕ_НАЧАЛА = ('PIL/_avif', 'PIL\\_avif')


def без_лишнего(что):
    """Убирает из списка то, что в окне не нужно."""
    оставим = []
    for запись in что:
        имя = запись[0]
        if Path(имя).name in ЛИШНИЕ_ФАЙЛЫ or имя.startswith(ЛИШНИЕ_НАЧАЛА):
            continue
        оставим.append(запись)
    return TOC(оставим)


a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=ЛИШНИЕ_ПАКЕТЫ,
    noarchive=False,
    optimize=0,
)

a.binaries = без_лишнего(a.binaries)
a.datas = без_лишнего(a.datas)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Velix',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
