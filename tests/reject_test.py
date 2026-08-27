"""Что видит пользователь, если ввёл не то имя (сервер отвечает 403)."""
import os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import store
import tempfile
store.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="velix-rej-")) / "velix.json"

import gui

results = []
def check(name, ok, detail=""):
    results.append(ok)
    print(f"TEST {name}: {'OK' if ok else 'FAIL — ' + str(detail)}")

app = gui.VelixApp()
app._show_form(register=False)
app.server_entry.insert(0, "vexorter.duckdns.org")   # родительский домен, сервер его не пускает
app.login_entry.insert(0, "gosha")
app.password_entry.insert(0, "пароль123")


def after_connect():
    text = app.auth_error.cget("text")
    # Интерфейс по умолчанию английский: сообщение должно быть человеческим
    # и без обрывков протокола
    check("reject-message-friendly",
          "address" in text.lower() and "HTTP" not in text
          and "403" not in text, repr(text))
    check("reject-stays-on-login", app.auth_view.winfo_ismapped(), "ушли с экрана входа")
    check("reject-button-restored", app.primary_button.cget("state") == "normal",
          app.primary_button.cget("state"))
    print("текст на экране:", text)
    app.destroy()


app.after(400, app._on_primary)
app.after(6000, after_connect)
app.mainloop()
print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
