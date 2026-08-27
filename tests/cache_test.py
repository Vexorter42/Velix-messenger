"""Кэш вложений на диске."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store  # noqa: E402
import mediacache  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


уголок = Path(tempfile.mkdtemp(prefix="velix-cache-"))
store.config_dir = lambda: уголок

check("cache-empty-at-start", mediacache.get("нет такого") is None)
check("cache-size-zero", mediacache.size() == 0, mediacache.size())

данные = b"a" * 4096
check("cache-put", mediacache.put("abc123", данные))
check("cache-get", mediacache.get("abc123") == данные)
check("cache-size-counts", mediacache.size() == 4096, mediacache.size())

# Имя файла делаем сами: что бы ни прислал сервер, из кэша не выйти
mediacache.put("../../побег", b"escape")
check("cache-no-escape", not (уголок.parent / "побег").exists()
      and not (уголок / ".." / "побег").exists())
check("cache-strange-id-ignored", mediacache.get("../../побег") is None
      or mediacache.get("../../побег") == b"escape")

# Слишком большое не кладём
огромное = b"x" * (mediacache.BIGGEST_ITEM + 1)
check("cache-skips-huge", not mediacache.put("ffff", огромное))
check("cache-huge-not-there", mediacache.get("ffff") is None)

# Переполнение: выбрасывается то, к чему дольше всего не обращались
mediacache.forget()
for номер, буква in enumerate("abcdef"):
    mediacache.put(буква * 8, bytes([номер]) * 1024)
    time.sleep(0.02)
mediacache.get("aaaaaaaa")                 # к первому обратились только что
выброшено = mediacache.prune(limit=3 * 1024)
check("cache-prunes", выброшено >= 3, выброшено)
check("cache-keeps-fresh", mediacache.get("aaaaaaaa") is not None,
      "выбросили то, чем только что пользовались")
check("cache-under-limit", mediacache.size() <= 3 * 1024, mediacache.size())

# Полная очистка
mediacache.forget()
check("cache-forget", mediacache.size() == 0, mediacache.size())

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
