package org.vexorter.velix;

import java.util.HashMap;
import java.util.Map;

/**
 * Язык приложения: английский по умолчанию, русский по выбору.
 *
 * Ключом перевода служит сама русская строка — как в оконном и веб-клиенте.
 * Сообщения об ошибках сервер шлёт с кодом, и здесь же лежит их разбор.
 */
class Lang {

    static final String[] CODES = {"en", "ru"};
    static final String[] NAMES = {"English", "Русский"};

    private static String current = "en";
    private static final Map<String, String> EN = new HashMap<>();
    private static final Map<String, String> SERVER = new HashMap<>();

    static void set(String code) {
        current = "ru".equals(code) ? "ru" : "en";
    }

    static String current() {
        return current;
    }

    /** Перевод строки. {name} подставляется парами ключ-значение. */
    static String t(String text, String... pairs) {
        String result = "en".equals(current) && EN.containsKey(text)
                ? EN.get(text) : text;
        for (int index = 0; index + 1 < pairs.length; index += 2) {
            result = result.replace("{" + pairs[index] + "}", pairs[index + 1]);
        }
        return result;
    }

    /** Текст ошибки от сервера на языке человека. */
    static String fromServer(org.json.JSONObject frame) {
        String code = frame.optString("code", "");
        String template = SERVER.get(code);
        if (template == null) {
            return frame.optString("text", "");
        }
        org.json.JSONObject args = frame.optJSONObject("args");
        if (args != null) {
            java.util.Iterator<String> keys = args.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                template = template.replace("{" + key + "}", args.optString(key));
            }
        }
        return t(template);
    }

    static String monthDay(int day, int month) {
        String[] months = "ru".equals(current)
                ? new String[]{"января", "февраля", "марта", "апреля", "мая",
                               "июня", "июля", "августа", "сентября", "октября",
                               "ноября", "декабря"}
                : new String[]{"January", "February", "March", "April", "May",
                               "June", "July", "August", "September", "October",
                               "November", "December"};
        String name = months[Math.max(0, Math.min(11, month - 1))];
        return "ru".equals(current) ? day + " " + name : name + " " + day;
    }

    private static void add(String russian, String english) {
        EN.put(russian, english);
    }

    static {
        // --- вход
        add("Адрес сервера", "Server address");
        add("Логин", "Username");
        add("Пароль", "Password");
        add("Как вас зовут", "Your name");
        add("Код приглашения", "Invite code");
        add("Войти", "Sign in");
        add("Создать аккаунт", "Create account");
        add("У меня уже есть аккаунт", "I already have an account");
        add("Вход в аккаунт", "Sign in");
        add("Нужен код приглашения", "An invite code is required");
        add("Код восстановления", "Recovery code");
        add("Забыли пароль?", "Forgot your password?");
        add("Восстановление пароля", "Password recovery");
        add("Новый пароль", "New password");
        add("Сменить пароль", "Change password");
        add("Вернуться ко входу", "Back to sign-in");
        add("Заполните логин, код и новый пароль.",
            "Fill in the username, the code and the new password.");
        add("Сохраните код восстановления", "Save your recovery code");
        add("По нему меняют пароль, если его забыли. Другого способа нет: "
            + "почту мы не спрашиваем, а сервер стоит у вас дома.",
            "It is what changes your password if you forget it. There is "
            + "no other way: we ask for no email, and the server sits in "
            + "your home.");
        add("Код скопирован", "Code copied");
        add("Понятно", "Got it");
        add("Код восстановления не подошёл.", "That recovery code did not work.");
        add("Заполните логин и пароль.", "Fill in the username and password.");
        add("Подключаемся…", "Connecting…");

        // --- списки
        add("Переписки", "Chats");
        add("УЧАСТНИКИ", "MEMBERS");
        add("Новая группа", "New group");
        add("Фото группы", "Group photo");
        add("Фото", "Photo");
        add("Видео или файл", "Video or file");
        add("Скачиваю «{name}»…", "Downloading “{name}”…");
        add("«{name}» сохранён в Загрузки", "“{name}” saved to Downloads");
        add("Не удалось сохранить файл.", "Could not save the file.");
        add("Видео", "Video");
        add("Настройки", "Settings");
        add("Поиск: @username или слово", "Search: @username or a word");
        add("ЛЮДИ", "PEOPLE");
        add("Создайте группу или найдите человека по @username.",
            "Create a group, or find someone by @username.");
        add("Позвать людей", "Invite people");
        add("Позвать", "Invite");
        add("Все уже в группе.", "Everyone is in the group already.");
        add("Сменить фото", "Change photo");
        add("АККАУНТ", "ACCOUNT");
        add("ПРИЛОЖЕНИЕ", "APP");
        add("Мой профиль", "My profile");
        add("Имя и пара слов о себе", "Name and a few words about you");
        add("Фото профиля", "Profile photo");
        add("Кружок, который видят остальные", "The circle everyone else sees");
        add("Сервер", "Server");
        add("Версия", "Version");
        add("Обновить до {version}", "Update to {version}");
        add("Скачиваю обновление…", "Downloading the update…");
        add("Обновление не установилось", "The update did not install");
        add("Установить обновление", "Install the update");
        add("Файл", "File");
        add("Отправляю «{name}» — {percent}%", "Sending “{name}” — {percent}%");
        add("«{name}» весит {size}, а больше {limit} сервер не принимает.",
            "“{name}” is {size}; the server takes at most {limit}.");
        add("Б", "B");
        add("КБ", "KB");
        add("МБ", "MB");
        add("ГБ", "GB");
        add("Удалить группу", "Delete group");
        add("Переписка и вложения пропадут у всех. Отменить это нельзя.",
            "The conversation and its attachments disappear for everyone. This cannot be undone.");
        add("Velix на связи", "Velix is connected");
        add("Сообщения", "Messages");
        add("Название группы", "Group name");
        add("Кого позвать", "Who to invite");
        add("Создать", "Create");
        add("Отмена", "Cancel");
        add("Пока некого позвать в группу.", "There is nobody to invite yet.");
        add("Создайте группу или напишите кому-нибудь из списка участников.",
            "Create a group, or write to someone from the members list.");
        add("нет сообщений", "no messages");
        add("вложение", "attachment");
        add("Профиль", "Profile");
        add("Выйти из аккаунта", "Sign out");
        add("Язык", "Language");
        add("Сохранить", "Save");
        add("Пара слов о себе", "A few words about yourself");
        add("Сохранено", "Saved");
        add("Сменить фото", "Change photo");

        // --- переписка
        add("Написать сообщение…", "Write a message…");
        add("Пока тихо. Напишите первым.", "No messages yet. Be the first to write.");
        add("Сегодня", "Today");
        add("сообщение удалено", "message deleted");
        add("Показать более старые", "Show older messages");
        add("Ответить", "Reply");
        add("Реакция", "Reaction");
        add("Копировать", "Copy");
        add("Копировать текст", "Copy text");
        add("Копировать фото", "Copy photo");
        add("Копировать файл", "Copy file");
        add("Закрепить", "Pin");
        add("Открепить", "Unpin");
        add("Переслать", "Forward");
        add("Куда переслать", "Forward to");
        add("Удалить", "Delete");
        add("Изменить", "Edit");
        add("Вложения переписки", "Attachments in this conversation");
        add("всего: {count}", "in total: {count}");
        add("Пока ничего не присылали", "Nothing has been sent yet");
        add("Правим: {text}", "Editing: {text}");
        add("изменено", "edited");
        add("Скопировано", "Copied");
        add("Переслано от {name}", "Forwarded from {name}");
        add("Ответ {name}", "Reply to {name}");
        add("{name} печатает…", "{name} is typing…");
        add("в сети", "online");
        add("был(а) в сети {when}", "last seen {when}");
        add("участников: {count}", "members: {count}");
        add("только что", "just now");
        add("сегодня в {time}", "today at {time}");
        add("вчера в {time}", "yesterday at {time}");
        add("{date} в {time}", "{date} at {time}");
        add("давно", "a long time ago");
        add("▶ Смотреть", "▶ Watch");
        add("Видео не открылось", "The video would not play");
        add("нет связи", "no connection");
        add("Связь потеряна, переподключаемся…", "Connection lost, reconnecting…");
        add("загружаю…", "loading…");
        add("Файл: {name}", "File: {name}");
        add("«{name}» больше 25 МБ, сервер такое не принимает.",
            "“{name}” is over 25 MB, the server does not accept that.");
        add("Не удалось прочитать файл.", "Could not read the file.");

        // --- ошибки сервера
        add("Нужен код приглашения — попросите его у того, кто держит чат.",
            "An invite code is required — ask whoever runs the chat for one.");
        add("Код приглашения не подошёл: его либо нет, либо им уже воспользовались.",
            "That invite code did not work: it does not exist or has already been used.");
        add("Код приглашения только что заняли. Попросите новый.",
            "That invite code was just used up. Ask for a new one.");
        add("Такой логин уже занят.", "That username is already taken.");
        add("Неверный логин или пароль.", "Wrong username or password.");
        add("Слишком много неудачных попыток. Попробуйте через {minutes} мин.",
            "Too many failed attempts. Try again in {minutes} min.");
        add("Клиент устарел: обновите Velix, этот сервер говорит на новом языке.",
            "This client is out of date: update Velix, this server speaks a newer protocol.");
        add("Сессия больше не действует, войдите заново.",
            "This session is no longer valid, sign in again.");
        add("Сначала нужно войти в аккаунт. Если у вас старая версия Velix — обновите её.",
            "You need to sign in first. If your Velix is old, update it.");
        add("Логин: от 3 до 24 символов, латиница, цифры, точка, дефис или подчёркивание.",
            "Username: 3 to 24 characters — latin letters, digits, dot, hyphen or underscore.");
        add("Пароль должен быть не короче {least} символов.",
            "The password must be at least {least} characters long.");
        add("Пароль длиннее {most} символов не принимаем.",
            "Passwords longer than {most} characters are not accepted.");
        add("У группы должно быть название.", "A group needs a name.");
        add("Выберите, кого позвать в группу.", "Choose who to invite to the group.");
        add("Позвать можно только в группу.", "You can only invite people to a group.");
        add("Написать самому себе не выйдет.", "You cannot write to yourself.");
        add("Такого человека нет.", "There is no such person.");
        add("Удалить можно только своё сообщение.", "You can only delete your own message.");
        add("Удалить можно только группу.", "Only a group can be deleted.");
        add("Фото ставится только группе.", "Only a group can have a photo.");
        add("Удалить группу может тот, кто её завёл.",
            "A group can be deleted by whoever created it.");
        add("Панель доступна только хозяину чата.",
            "The panel is only for whoever runs the chat.");
        add("Себя удалить нельзя.", "You cannot delete yourself.");
        add("Сообщение не найдено.", "Message not found.");
        add("Эта переписка вам недоступна.", "This conversation is not available to you.");
        add("Вложение не найдено.", "Attachment not found.");
        add("Файл больше {limit}, не приняли.",
            "The file is over {limit}, so it was not accepted.");
        add("Аватарка больше {limit}, не приняли.",
            "The photo is over {limit}, so it was not accepted.");

        SERVER.put("invite_required",
                "Нужен код приглашения — попросите его у того, кто держит чат.");
        SERVER.put("invite_bad",
                "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.");
        SERVER.put("invite_taken", "Код приглашения только что заняли. Попросите новый.");
        SERVER.put("recovery_bad", "Код восстановления не подошёл.");
        SERVER.put("login_taken", "Такой логин уже занят.");
        SERVER.put("bad_credentials", "Неверный логин или пароль.");
        SERVER.put("locked_out",
                "Слишком много неудачных попыток. Попробуйте через {minutes} мин.");
        SERVER.put("client_too_old",
                "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.");
        SERVER.put("session_expired", "Сессия больше не действует, войдите заново.");
        SERVER.put("server_slip", "Не получилось. Попробуйте ещё раз.");
        SERVER.put("login_required",
                "Сначала нужно войти в аккаунт. Если у вас старая версия Velix — обновите её.");
        SERVER.put("bad_login",
                "Логин: от 3 до 24 символов, латиница, цифры, точка, дефис или подчёркивание.");
        SERVER.put("short_password", "Пароль должен быть не короче {least} символов.");
        SERVER.put("long_password", "Пароль длиннее {most} символов не принимаем.");
        SERVER.put("group_needs_title", "У группы должно быть название.");
        SERVER.put("group_needs_members", "Выберите, кого позвать в группу.");
        SERVER.put("group_only", "Позвать можно только в группу.");
        SERVER.put("group_only_delete", "Удалить можно только группу.");
        SERVER.put("group_only_photo", "Фото ставится только группе.");
        SERVER.put("not_group_owner", "Удалить группу может тот, кто её завёл.");
        SERVER.put("not_admin", "Панель доступна только хозяину чата.");
        SERVER.put("admin_self", "Себя удалить нельзя.");
        SERVER.put("self_dm", "Написать самому себе не выйдет.");
        SERVER.put("no_such_person", "Такого человека нет.");
        SERVER.put("not_your_message", "Удалить можно только своё сообщение.");
        SERVER.put("message_not_found", "Сообщение не найдено.");
        SERVER.put("no_access", "Эта переписка вам недоступна.");
        SERVER.put("attachment_missing", "Вложение не найдено.");
        SERVER.put("file_too_big", "Файл больше {limit}, не приняли.");
        SERVER.put("avatar_too_big", "Аватарка больше {limit}, не приняли.");
    }
}
