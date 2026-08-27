"""Разбор адреса сервера: домен, IP, порт, IPv6."""
import os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import gui
import client

cases = [
    ("",                            "wss://localhost:8765"),
    ("localhost",                   "wss://localhost:8765"),
    ("  192.168.0.225  ",           "wss://192.168.0.225:8765"),
    ("vexorter.duckdns.org",        "wss://vexorter.duckdns.org:8765"),
    ("vexorter.duckdns.org:9000",   "wss://vexorter.duckdns.org:9000"),
    ("192.168.0.225:12345",         "wss://192.168.0.225:12345"),
    ("[::1]",                       "wss://[::1]:8765"),
    ("[::1]:9000",                  "wss://[::1]:9000"),
    ("fe80::1",                     "wss://[fe80::1]:8765"),
    ("host:неполадка",              "wss://host:неполадка:8765"),
]

ok = 0
for source, expected in cases:
    got_gui = gui.build_uri(source)
    got_cli = client.build_uri(source)
    good = got_gui == expected and got_cli == expected
    ok += good
    mark = "OK" if good else f"FAIL (получилось {got_gui!r} / {got_cli!r}, ждали {expected!r})"
    print(f"TEST uri {source!r:32} -> {mark}")

# У защищённого адреса всегда есть запасной незащищённый: старые сервера
# без сертификата должны продолжать работать
for source, expected in cases:
    variants = gui.connection_uris(source)
    good = (variants[0] == expected
            and variants[-1] == expected.replace("wss://", "ws://", 1))
    ok += good
    mark = "OK" if good else f"FAIL (получилось {variants!r})"
    print(f"TEST fallback {source!r:28} -> {mark}")

print(f"\nИТОГО: {ok}/{len(cases) * 2} разборов верны")
sys.exit(0 if ok == len(cases) * 2 else 1)
