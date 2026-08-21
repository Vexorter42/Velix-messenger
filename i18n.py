"""Язык интерфейса: английский по умолчанию, русский по выбору.

Ключом перевода служит сама русская строка. Так в коде видно, что написано
на экране, русская «локализация» получается тождественной и не требует
второго словаря, а забытая строка сразу заметна: она останется русской.
Проверка `i18n_test` следит, чтобы такого не случалось.

Язык хранится в настройках клиента и применяется на лету.
"""

LANGUAGES = ("en", "ru")
NAMES = {"en": "English", "ru": "Русский"}
DEFAULT = "en"

_current = DEFAULT


def set_language(code):
    """Переключает язык. Незнакомый код откатывает к английскому."""
    global _current
    _current = code if code in LANGUAGES else DEFAULT
    return _current


def language():
    return _current


def t(text, **values):
    """Переводит строку и подставляет значения в фигурных скобках."""
    return in_language(_current, text, **values)


def in_language(code, text, **values):
    """То же самое, но для чужого языка — например, для адресата уведомления.

    Сервер обслуживает сразу всех, поэтому переключать общий язык ему нельзя.
    """
    result = ENGLISH.get(text, text) if code == "en" else text
    return result.format(**values) if values else result


MONTHS = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
}


def month_day(day, month):
    """«5 марта» по-русски и «March 5» по-английски."""
    name = MONTHS[_current][month - 1]
    return f"{day} {name}" if _current == "ru" else f"{name} {day}"


# Сообщения об ошибках сервер шлёт с кодом: языка клиента он не знает и
# пишет по-русски, а клиент показывает то же самое на своём языке.
SERVER_MESSAGES = {
    "invite_required": "Нужен код приглашения — попросите его у того, кто держит чат.",
    "invite_bad": "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.",
    "invite_taken": "Код приглашения только что заняли. Попросите новый.",
    "login_taken": "Такой логин уже занят.",
    "bad_credentials": "Неверный логин или пароль.",
    "locked_out": "Слишком много неудачных попыток. Попробуйте через {minutes} мин.",
    "client_too_old": "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.",
    "session_expired": "Сессия больше не действует, войдите заново.",
    "login_required": "Сначала нужно войти в аккаунт. "
                      "Если у вас старая версия Velix — обновите её.",
    "bad_login": "Логин: от 3 до 24 символов, латиница, цифры, точка, дефис"
                 " или подчёркивание.",
    "short_password": "Пароль должен быть не короче {least} символов.",
    "long_password": "Пароль длиннее {most} символов не принимаем.",
    "self_dm": "Написать самому себе не выйдет.",
    "no_such_person": "Такого человека нет.",
    "not_your_message": "Удалить можно только своё сообщение.",
    "message_not_found": "Сообщение не найдено.",
    "no_access": "Эта переписка вам недоступна.",
    "attachment_missing": "Вложение не найдено.",
    "update_unavailable": "Обновление недоступно.",
    "expected_file": "Ожидались данные файла.",
    "expected_image": "Ожидались данные картинки.",
    "file_too_big": "Файл больше {limit}, не приняли.",
    "avatar_too_big": "Аватарка больше {limit}, не приняли.",
}


def from_server(message):
    """Текст ошибки от сервера на языке клиента.

    Старый сервер кода не пришлёт — тогда показываем то, что он написал.
    """
    template = SERVER_MESSAGES.get(message.get("code"))
    if template is None:
        return message.get("text", "")
    return t(template, **(message.get("args") or {}))


ENGLISH = {
    # --- подключение
    "Сервер недоступен. Проверьте, запущен ли он.":
        "The server is not responding. Check that it is running.",
    "Сертификат сервера выписан на другое имя. Проверьте, правильно ли введён адрес.":
        "The server certificate is issued to a different name. Check the address.",
    "Сервер не принял защищённое соединение.":
        "The server refused a secure connection.",
    "Не удалось найти сервер по этому адресу.":
        "No server was found at this address.",
    "Не удалось подключиться: {error}": "Could not connect: {error}",
    "Сервер не принимает подключение по этому адресу. Проверьте, что он введён точно.":
        "The server does not accept connections at this address. "
        "Check that you typed it exactly.",
    "Сервер ответил кодом {code}.": "The server answered with code {code}.",
    "По этому адресу отвечает не Velix. Проверьте адрес и порт.":
        "Something other than Velix answers at this address. "
        "Check the address and the port.",
    "Ошибка соединения: {error}": "Connection error: {error}",
    "Нет связи с сервером.": "No connection to the server.",
    "нет связи с сервером": "no connection to the server",
    "Соединение потеряно. Нажмите «Сменить», чтобы войти заново.":
        "Connection lost. Press “Switch” to sign in again.",
    "⚠ без шифрования · ": "⚠ not encrypted · ",
    "вы вошли как {name}": "signed in as {name}",
    "Соединение без шифрования: сервер не умеет wss://. "
    "Переписку в такой сети можно перехватить.":
        "Unencrypted connection: this server does not speak wss://. "
        "On such a network the conversation can be intercepted.",

    # --- вход и регистрация
    "Адрес сервера": "Server address",
    "Логин": "Username",
    "Пароль": "Password",
    "Как вас зовут": "Your name",
    "Код приглашения": "Invite code",
    "ВОЙТИ": "SIGN IN",
    "СОЗДАТЬ АККАУНТ": "CREATE ACCOUNT",
    "ПОДКЛЮЧЕНИЕ…": "CONNECTING…",
    "Создать аккаунт": "Create account",
    "У меня уже есть аккаунт": "I already have an account",
    "К списку аккаунтов": "Back to accounts",
    "Выберите аккаунт": "Choose an account",
    "Войти в другой аккаунт": "Sign in to another account",
    "Вход в аккаунт": "Sign in",
    "Нужен код приглашения": "An invite code is required",
    "Заполните логин и пароль.": "Fill in the username and password.",
    "Входим как {name}…": "Signing in as {name}…",

    # --- чат
    "Общий чат": "Main chat",
    "Профиль": "Profile",
    "Настройки": "Settings",
    "Сменить": "Switch",
    "Поиск по переписке": "Search messages",
    "Написать сообщение…": "Write a message…",
    "Пока тихо. Напишите первым.": "No messages yet. Be the first to write.",
    "УЧАСТНИКИ": "MEMBERS",
    "нет сообщений": "no messages",
    "вложение": "attachment",
    "Показать более старые": "Show older messages",
    "сообщение удалено": "message deleted",
    "файл": "file",
    "Файл": "File",
    "Видео": "Video",
    "Ответить": "Reply",
    "Реакция": "Reaction",
    "Удалить": "Delete",
    "Ответ {name}: {text}": "Reply to {name}: {text}",
    "Найдено: {count}": "Found: {count}",
    "По запросу «{query}» ничего нет": "Nothing found for “{query}”",
    "{name} печатает…": "{name} is typing…",
    "Сегодня": "Today",
    "Я": "Me",
    "Вы": "You",

    # --- вложения
    "загружаю картинку…": "loading image…",
    "Открыть": "Open",
    "Загружаю…": "Loading…",
    "не удалось показать картинку: {error}": "could not show the image: {error}",
    "Не удалось открыть файл: {error}": "Could not open the file: {error}",
    "Не удалось прочитать файл: {error}": "Could not read the file: {error}",
    "«{name}» весит {size}, а больше {limit} сервер не принимает.":
        "“{name}” is {size}, and the server does not accept anything over {limit}.",
    "Отправляем фото…": "Sending the photo…",
    "Что отправляем?": "What are we sending?",
    "Картинки и видео": "Images and video",
    "Картинки": "Images",
    "Все файлы": "All files",
    "Выберите фото": "Choose a photo",
    "вставка.png": "pasted.png",

    # --- профиль
    "Сменить фото": "Change photo",
    "Пара слов о себе": "A few words about yourself",
    "СОХРАНИТЬ": "SAVE",
    "Назад в чат": "Back to chat",
    "Сохранено": "Saved",
    "Сохраняем…": "Saving…",
    "Имя не может быть пустым.": "The name cannot be empty.",

    # --- настройки
    "Язык": "Language",
    "Тёмное оформление": "Dark theme",
    "Прятать в трей при закрытии": "Hide to tray on close",
    "Запускать вместе с Windows": "Start with Windows",
    "Версия {version}": "Version {version}",
    "Обновлений нет": "No updates",
    "У вас последняя версия": "You have the latest version",
    "Есть версия {version}": "Version {version} is available",
    " — обновитесь через git": " — update it with git",
    "Обновить до {version}": "Update to {version}",
    "Сервер прислал пустой файл.": "The server sent an empty file.",
    "Обновление установлено, перезапускаюсь…": "Update installed, restarting…",
    "Перезапуск…": "Restarting…",
    "Velix будет запускаться при входе в Windows.":
        "Velix will start when you sign in to Windows.",
    "Автозапуск выключен.": "Autostart is off.",
    "Значок в трее недоступен: не установлен pystray.":
        "Tray icon unavailable: pystray is not installed.",
    "Автозапуск настраивается только в Windows.":
        "Autostart can only be set up on Windows.",
    "Настройки сохраняются сразу.": "Settings are saved as you change them.",

    # --- трей и окно
    "Velix свернулся в трей": "Velix moved to the tray",
    "Программа продолжает работать. Значок рядом с часами открывает окно обратно.":
        "The app keeps running. The icon next to the clock brings the window back.",
    "Открыть Velix": "Open Velix",
    "Выйти": "Quit",

    # --- обновление на месте и автозапуск
    "не удалось записать новый файл: {error}":
        "could not write the new file: {error}",
    "не удалось освободить место под новую версию: {error}":
        "could not make room for the new version: {error}",
    "не удалось поставить новую версию: {error}":
        "could not install the new version: {error}",
    "новая версия установлена, но не запустилась: {error}":
        "the new version was installed but did not start: {error}",
    "Автозапуск умеет настраиваться только в Windows.":
        "Autostart can only be set up on Windows.",
    "Не удалось прописать автозапуск: {error}":
        "Could not turn on autostart: {error}",
    "Не удалось убрать автозапуск: {error}":
        "Could not turn off autostart: {error}",

    # --- консольный клиент
    "--- Добро пожаловать в Velix ---": "--- Welcome to Velix ---",
    "Адрес сервера, можно с портом (Enter — localhost): ":
        "Server address, port optional (Enter for localhost): ",
    "Создать новый аккаунт? (y/N): ": "Create a new account? (y/N): ",
    "Логин: ": "Username: ",
    "Пароль: ": "Password: ",
    "Как вас зовут: ": "Your name: ",
    "Код приглашения: ": "Invite code: ",
    "Подключение к {server}...": "Connecting to {server}...",
    "Выход из Velix...": "Leaving Velix...",
    "фото": "photo",
    "гифку": "GIF",
    "видео": "video",
    "без имени": "unnamed",
    "{nick} прислал {label}: {name} ({size}) — открыть можно в оконном клиенте":
        "{nick} sent {label}: {name} ({size}) — open it in the desktop client",
    "--- последние сообщения ---": "--- recent messages ---",
    "--- конец истории ---": "--- end of history ---",
    "[Система]: {text}": "[System]: {text}",
    "[Ошибка]: {text}": "[Error]: {text}",
    "[Система]: соединение без шифрования, переписку можно перехватить.":
        "[System]: unencrypted connection, the conversation can be intercepted.",
    "[Система]: профиль обновлён": "[System]: profile updated",
    "[Система]: Соединение с сервером потеряно.":
        "[System]: Connection to the server lost.",
    "[Система]: Сообщение не отправлено, соединение закрыто.":
        "[System]: The message was not sent, the connection is closed.",
    "[Система]: Успешно подключено как {name}! "
    "Можно писать сообщения. (для выхода введите /exit)":
        "[System]: Connected as {name}! "
        "You can write messages. (type /exit to leave)",
    "не пустили в чат": "not allowed into the chat",
    "Не удалось подключиться к {uri}: {error}": "Could not connect to {uri}: {error}",

    # --- сообщения сервера
    "Нужен код приглашения — попросите его у того, кто держит чат.":
        "An invite code is required — ask whoever runs the chat for one.",
    "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.":
        "That invite code did not work: it does not exist or has already been used.",
    "Код приглашения только что заняли. Попросите новый.":
        "That invite code was just used up. Ask for a new one.",
    "Такой логин уже занят.": "That username is already taken.",
    "Логин: от 3 до 24 символов, латиница, цифры, точка, дефис или подчёркивание.":
        "Username: 3 to 24 characters — latin letters, digits, dot, hyphen "
        "or underscore.",
    "Пароль должен быть не короче {least} символов.":
        "The password must be at least {least} characters long.",
    "Пароль длиннее {most} символов не принимаем.":
        "Passwords longer than {most} characters are not accepted.",
    "прислал вложение": "sent an attachment",
    "Неверный логин или пароль.": "Wrong username or password.",
    "Слишком много неудачных попыток. Попробуйте через {minutes} мин.":
        "Too many failed attempts. Try again in {minutes} min.",
    "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.":
        "This client is out of date: update Velix, this server speaks a newer protocol.",
    "Сессия больше не действует, войдите заново.":
        "This session is no longer valid, sign in again.",
    "Сначала нужно войти в аккаунт. Если у вас старая версия Velix — обновите её.":
        "You need to sign in first. If your Velix is old, update it.",
    "Написать самому себе не выйдет.": "You cannot write to yourself.",
    "Такого человека нет.": "There is no such person.",
    "Удалить можно только своё сообщение.": "You can only delete your own message.",
    "Сообщение не найдено.": "Message not found.",
    "Эта переписка вам недоступна.": "This conversation is not available to you.",
    "Вложение не найдено.": "Attachment not found.",
    "Обновление недоступно.": "No update is available.",
    "Ожидались данные файла.": "File data was expected.",
    "Ожидались данные картинки.": "Image data was expected.",
    "Файл больше {limit}, не приняли.":
        "The file is over {limit}, so it was not accepted.",
    "Аватарка больше {limit}, не приняли.":
        "The photo is over {limit}, so it was not accepted.",
}
