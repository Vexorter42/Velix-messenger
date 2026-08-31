"""Прогон всех проверок.

    python tests/run_all.py            — всё
    python tests/run_all.py --quick    — без оконных: они требуют экрана
    python tests/run_all.py --jobs 1   — по одной, как было раньше

Проверок больше семидесяти, и по одной они идут почти двадцать минут — при
том что заняты в основном ожиданием: сервер поднимается, окно рисуется,
кадр летит по сети. Поэтому гоняем их разом по нескольку.

Мешать друг другу они могут одним — портом: два сервера на один порт не
встанут. Порты у каждой проверки свои и написаны прямо в её тексте, так что
читаем их оттуда и следим, чтобы две проверки с общим портом не шли
одновременно. Всё остальное у них и так раздельное: своя песочница, свой
угол настроек, свой каталог сохранённого.

Оконные проверки поднимают настоящее окно Tk и потому не годятся для машины
без монитора; на своей машине гонять стоит всё. Окна при этом уезжают за край
экрана и на глаза не лезут — см. harness.py.

Те, что меряют время движений, помечены у себя ПООДИНОЧКЕ = True: под
нагрузкой от соседок их часы врут, поэтому им даётся тишина.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP = {"run_all.py", "harness.py", "peer_send.py"}

# Оконные узнаём по тому, что они ввозят: имя файла врёт (проверка про
# запоздавшую картинку окно поднимает, а «gui» в названии не имеет)
ОКОННОЕ = ("import gui", "customtkinter", "import videoplayer",
           "ffpyplayer", "pystray", "ImageGrab")

# Больше этого одновременно не запускаем: на четырёх ядрах десять проверок
# начинают мешать друг другу и врать таймингами
СРАЗУ = 4


def нужен_экран(текст):
    return any(признак in текст for признак in ОКОННОЕ)


def порты(текст):
    """Какие порты проверка занимает — по числам в её тексте."""
    return {int(one) for one in re.findall(r"\b(?:8\d{3}|9\d{3})\b", текст)}


def разобрать_доводы():
    быстро = "--quick" in sys.argv
    сразу = СРАЗУ
    if "--jobs" in sys.argv:
        место = sys.argv.index("--jobs")
        if место + 1 < len(sys.argv):
            сразу = max(1, int(sys.argv[место + 1]))
    return быстро, сразу


def собрать(быстро):
    """Список проверок: имя, занятые порты, гонять ли в одиночку."""
    список = []
    for path in sorted(HERE.glob("*_test*.py")):
        if path.name in SKIP or "retired" in path.name \
                or path.name.startswith("patch_"):
            continue
        текст = path.read_text(encoding="utf-8", errors="replace")
        if быстро and нужен_экран(текст):
            continue
        список.append((path.name, порты(текст), "ПООДИНОЧКЕ = True" in текст))
    # Одиночек вперёд: оставленные на хвост, они выстроились бы в конце
    # прогона в очередь из себя одних
    return sorted(список, key=lambda одна: not одна[2])


def показать(имя, result, сколько):
    tail = [line for line in (result.stdout or "").splitlines()
            if line.startswith("ИТОГО")]
    mark = "OK  " if result.returncode == 0 else "ПЛОХО"
    print(f"{mark} {имя:<28} {tail[-1] if tail else '(нет итога)'}"
          f"  {сколько:.0f}с", flush=True)
    if result.returncode != 0:
        for line in (result.stdout or "").splitlines():
            if "FAIL" in line:
                print(f"      {line}")
        error = (result.stderr or "").strip().splitlines()
        if error:
            print(f"      {error[-1][:160]}")


def main():
    быстро, сразу = разобрать_доводы()
    очередь = собрать(быстро)
    всего = len(очередь)
    print(f"проверок: {всего}{' (без оконных)' if быстро else ''}"
          f", разом по {сразу}\n", flush=True)

    начало = time.time()
    идут = []       # имя, процесс, порты, когда начали, в одиночку ли
    занятые = set()
    плохие = []

    while очередь or идут:
        # --- запускаем всё, чему ничто не мешает
        место = 0
        while место < len(очередь) and len(идут) < сразу:
            имя, свои, одна = очередь[место]
            if свои & занятые:
                место += 1      # порт занят соседкой — подождёт своей очереди
                continue
            if одна and идут:
                место += 1      # ей нужна тишина: дождёмся, пока все выйдут
                continue
            if not одна and any(кто[4] for кто in идут):
                место += 1      # сейчас в одиночку идёт другая
                continue
            очередь.pop(место)
            процесс = subprocess.Popen(
                [sys.executable, "-X", "utf8", имя], cwd=HERE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace")
            идут.append((имя, процесс, свои, time.time(), одна))
            занятые |= свои

        # --- ждём, пока хоть одна закончится
        дождались = False
        while not дождались:
            for место, (имя, процесс, свои, когда, _) in enumerate(идут):
                if процесс.poll() is None:
                    continue
                out, err = процесс.communicate()
                готово = subprocess.CompletedProcess(
                    имя, процесс.returncode, out, err)
                показать(имя, готово, time.time() - когда)
                if процесс.returncode != 0:
                    плохие.append(имя)
                идут.pop(место)
                занятые -= свои
                дождались = True
                break
            if not дождались:
                time.sleep(0.2)

    print(f"\nсорвалось: {len(плохие)} из {всего}"
          f", ушло {time.time() - начало:.0f}с")
    for name in плохие:
        print(" ", name)
    return 1 if плохие else 0


if __name__ == "__main__":
    sys.exit(main())
