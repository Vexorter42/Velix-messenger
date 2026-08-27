"""Консольный клиент: регистрация, вход, приём сообщений, выход."""
import os, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = Path(__file__).with_name("consolesandbox")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
# Набор писался под русский интерфейс, клиенту задаём его напрямую
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
           VELIX_LANG="ru")
ENV.pop("VELIX_ALLOWED_HOSTS", None)
# В песочнице регистрация открыта: коды приглашений проверяет отдельный набор
ENV["VELIX_OPEN_REGISTRATION"] = "1"

results = []
def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")

if SANDBOX.exists():
    shutil.rmtree(SANDBOX)
SANDBOX.mkdir()
for name in ("server.py", "storage.py", "protocol.py", "media.py", "accounts.py",
             "push.py", "i18n.py"):
    shutil.copy(REPO / name, SANDBOX / name)

server = subprocess.Popen([sys.executable, "server.py"], cwd=SANDBOX, env=ENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)


def run_client(answers, timeout=25):
    process = subprocess.Popen([sys.executable, "client.py"], cwd=REPO, env=ENV,
                               text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    try:
        return process.communicate(answers, timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()[0] + "\n[ЗАВИС]"


# --- сосед: отдельный процесс, а не поток. Рядом с блокирующим
# communicate() в главном потоке цикл asyncio в соседнем потоке на Windows
# не просыпается, и сообщения так и не уходили. Запускаем его после того,
# как tester зарегистрируется: соседу нужно позвать его в группу.
def wake_peer():
    subprocess.run([sys.executable, str(Path(__file__).with_name("peer_send.py"))],
                   env=ENV, timeout=30, capture_output=True)

def staged(prefix, pause=3.5):
    """Подаёт ввод по шагам: сразу отданный /exit успевает сработать
    раньше, чем клиент допечатает историю."""
    process = subprocess.Popen([sys.executable, 'client.py'], cwd=REPO, env=ENV,
                               text=True, encoding='utf-8', errors='replace',
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    process.stdin.write(prefix)
    process.stdin.flush()
    time.sleep(pause)
    process.stdin.write('/exit\n')
    process.stdin.flush()
    try:
        return process.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()[0] + '\n[ЗАВИС]'


# --- регистрация нового аккаунта
out = staged('localhost\ny\ntester\nпароль123\nТестер\nбез-кода\n')
check("console-registers", "Успешно подключено как Тестер" in out, out[-400:])
check("console-exits", "Выход из Velix" in out and "[ЗАВИС]" not in out, out[-200:])

# Теперь у tester есть аккаунт: сосед заводит с ним группу и пишет туда
wake_peer()

# --- повторный вход: переписка на месте, в неё можно писать
out = staged('localhost\nn\ntester\nпароль123\nпривет из теста\n', pause=5.0)
check("console-logs-in", "Успешно подключено как Тестер" in out, out[-400:])
check("console-lists-chats", "Общая" in out, out[-800:])

# --- неверный пароль
out = run_client("localhost\nn\ntester\nне тот пароль\n")
check("console-wrong-password", "Неверный логин или пароль" in out, out[-300:])

# --- сообщения и вложения соседа видны строкой
out = staged('localhost\nn\ntester\nпароль123\n', pause=5.0)
check("console-shows-peer-text", "привет из другого клиента" in out, out[-600:])
check("console-shows-media-line", "прислал" in out and "битая.png" in out, out[-600:])

server.terminate()
server.wait(timeout=5)
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
