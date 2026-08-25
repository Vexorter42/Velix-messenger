// Язык мобильного клиента: английский по умолчанию, русский по выбору.
// Ключ перевода — сама русская строка, как и в оконном клиенте.

const LANGUAGES = ["en", "ru"];
const LANGUAGE_NAMES = {en: "English", ru: "Русский"};

const MONTHS_BY_LANGUAGE = {
  ru: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
       "августа", "сентября", "октября", "ноября", "декабря"],
  en: ["January", "February", "March", "April", "May", "June", "July",
       "August", "September", "October", "November", "December"],
};

const EN = {
  // --- экраны
  "Вход в аккаунт": "Sign in",
  "Нужен код приглашения": "An invite code is required",
  "Код восстановления": "Recovery code",
  "Забыли пароль?": "Forgot your password?",
  "Восстановление пароля": "Password recovery",
  "Новый пароль": "New password",
  "Сменить пароль": "Change password",
  "Вернуться ко входу": "Back to sign-in",
  "Заполните логин, код и новый пароль.":
      "Fill in the username, the code and the new password.",
  "Сохраните код восстановления": "Save your recovery code",
  "По нему меняют пароль, если его забыли. Другого способа нет: почту мы не спрашиваем, а сервер стоит у вас дома.":
      "It is what changes your password if you forget it. There is no other way: we ask for no email, and the server sits in your home.",
  "Код скопирован": "Code copied",
  "Понятно": "Got it",
  "Код восстановления не подошёл.": "That recovery code did not work.",
  "Логин": "Username",
  "Пароль": "Password",
  "Как вас зовут": "Your name",
  "Код приглашения": "Invite code",
  "Войти": "Sign in",
  "Создать аккаунт": "Create account",
  "У меня уже есть аккаунт": "I already have an account",
  "Профиль": "Profile",
  "Поиск по переписке": "Search messages",
  "УЧАСТНИКИ": "MEMBERS",
  "Назад": "Back",
  "Общий чат": "Main chat",
  "Личная переписка": "Direct chat",
  "Написать сообщение…": "Write a message…",
  "Сменить фото": "Change photo",
  "Пара слов о себе": "A few words about yourself",
  "Сохранить": "Save",
  "Выйти из аккаунта": "Sign out",
  "Язык": "Language",

  // --- связь и ошибки
  "нет связи": "no connection",
  "Соединение потеряно. Обновите страницу.": "Connection lost. Reload the page.",
  "Не удалось связаться с сервером.": "Could not reach the server.",
  "Не пустили в чат.": "Not allowed into the chat.",
  "Заполните логин и пароль.": "Fill in the username and password.",

  // --- переписка
  "Сохранено": "Saved",
  "Сохраняем…": "Saving…",
  "Отправляем фото…": "Sending the photo…",
  "вложение": "attachment",
  "Ответить": "Reply",
  "Реакция": "Reaction",
  "Удалить": "Delete",
  "Копировать": "Copy",
  "Отмена": "Cancel",
  "Скопировано": "Copied",
  "Сначала откройте вложение": "Open the attachment first",
  "Не удалось скопировать: {error}": "Could not copy: {error}",
  "Новая группа": "New group",
  "Фото группы": "Group photo",
  "Удалить группу": "Delete group",
  "Переписка и вложения пропадут у всех. Отменить это нельзя.":
      "The conversation and its attachments disappear for everyone. This cannot be undone.",
  "Удалить можно только группу.": "Only a group can be deleted.",
  "Фото ставится только группе.": "Only a group can have a photo.",
  "Удалить группу может тот, кто её завёл.":
      "A group can be deleted by whoever created it.",
  "Панель доступна только хозяину чата.":
      "The panel is only for whoever runs the chat.",
  "Себя удалить нельзя.": "You cannot delete yourself.",
  "Название группы": "Group name",
  "Создать": "Create",
  "Позвать {name}?": "Invite {name}?",
  "Пока некого позвать в группу.": "There is nobody to invite yet.",
  "Создайте группу или напишите кому-нибудь из списка участников.":
      "Create a group, or write to someone from the members list.",
  "нет сообщений": "no messages",
  "Сегодня": "Today",
  "Показать более старые": "Show older messages",
  "Пока тихо. Напишите первым.": "No messages yet. Be the first to write.",
  "сообщение удалено": "message deleted",
  "загружаю…": "loading…",
  "файл": "file",
  "Скачать {name}": "Download {name}",
  "1 — реакция, 2 — ответить": "1 — reaction, 2 — reply",
  ", 3 — удалить": ", 3 — delete",
  "Ответ {name}: ": "Reply to {name}: ",
  "{name} печатает…": "{name} is typing…",
  "Найдено: {count}": "Found: {count}",
  "По запросу «{query}» ничего нет": "Nothing found for “{query}”",
  "«{name}» больше 25 МБ, сервер такое не принимает.":
      "“{name}” is over 25 MB, the server does not accept that.",

  // --- ошибки сервера
  "Нужен код приглашения — попросите его у того, кто держит чат.":
      "An invite code is required — ask whoever runs the chat for one.",
  "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.":
      "That invite code did not work: it does not exist or has already been used.",
  "Код приглашения только что заняли. Попросите новый.":
      "That invite code was just used up. Ask for a new one.",
  "Такой логин уже занят.": "That username is already taken.",
  "Неверный логин или пароль.": "Wrong username or password.",
  "Слишком много неудачных попыток. Попробуйте через {minutes} мин.":
      "Too many failed attempts. Try again in {minutes} min.",
  "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.":
      "This client is out of date: update Velix, this server speaks a newer protocol.",
  "Сессия больше не действует, войдите заново.":
      "This session is no longer valid, sign in again.",
  "Сначала нужно войти в аккаунт. Если у вас старая версия Velix — обновите её.":
      "You need to sign in first. If your Velix is old, update it.",
  "Логин: от 3 до 24 символов, латиница, цифры, точка, дефис или подчёркивание.":
      "Username: 3 to 24 characters — latin letters, digits, dot, hyphen or underscore.",
  "Пароль должен быть не короче {least} символов.":
      "The password must be at least {least} characters long.",
  "Пароль длиннее {most} символов не принимаем.":
      "Passwords longer than {most} characters are not accepted.",
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
};

// Ошибки сервер шлёт с кодом: текст в кадре русский, а показать надо на
// языке человека.
const SERVER_MESSAGES = {
  invite_required: "Нужен код приглашения — попросите его у того, кто держит чат.",
  invite_bad: "Код приглашения не подошёл: его либо нет, либо им уже воспользовались.",
  invite_taken: "Код приглашения только что заняли. Попросите новый.",
  recovery_bad: "Код восстановления не подошёл.",
  login_taken: "Такой логин уже занят.",
  bad_credentials: "Неверный логин или пароль.",
  locked_out: "Слишком много неудачных попыток. Попробуйте через {minutes} мин.",
  client_too_old: "Клиент устарел: обновите Velix, этот сервер говорит на новом языке.",
  session_expired: "Сессия больше не действует, войдите заново.",
  login_required: "Сначала нужно войти в аккаунт. "
                  + "Если у вас старая версия Velix — обновите её.",
  bad_login: "Логин: от 3 до 24 символов, латиница, цифры, точка, дефис"
             + " или подчёркивание.",
  short_password: "Пароль должен быть не короче {least} символов.",
  long_password: "Пароль длиннее {most} символов не принимаем.",
  self_dm: "Написать самому себе не выйдет.",
  no_such_person: "Такого человека нет.",
  not_your_message: "Удалить можно только своё сообщение.",
  message_not_found: "Сообщение не найдено.",
  group_only_delete: "Удалить можно только группу.",
  group_only_photo: "Фото ставится только группе.",
  not_group_owner: "Удалить группу может тот, кто её завёл.",
  not_admin: "Панель доступна только хозяину чата.",
  admin_self: "Себя удалить нельзя.",
  no_access: "Эта переписка вам недоступна.",
  attachment_missing: "Вложение не найдено.",
  update_unavailable: "Обновление недоступно.",
  expected_file: "Ожидались данные файла.",
  expected_image: "Ожидались данные картинки.",
  file_too_big: "Файл больше {limit}, не приняли.",
  avatar_too_big: "Аватарка больше {limit}, не приняли.",
};

let language = localStorage.getItem("velix.language") || "en";
if (!LANGUAGES.includes(language)) language = "en";

function setLanguage(code) {
  language = LANGUAGES.includes(code) ? code : "en";
  localStorage.setItem("velix.language", language);
  document.documentElement.lang = language;
  return language;
}

function t(text, values) {
  let result = language === "en" ? (EN[text] || text) : text;
  if (values) {
    for (const [key, value] of Object.entries(values)) {
      result = result.split(`{${key}}`).join(value);
    }
  }
  return result;
}

function monthDay(moment) {
  const name = MONTHS_BY_LANGUAGE[language][moment.getMonth()];
  return language === "ru" ? `${moment.getDate()} ${name}`
                           : `${name} ${moment.getDate()}`;
}

function fromServer(message) {
  const template = SERVER_MESSAGES[message.code];
  if (!template) return message.text || "";
  return t(template, message.args || {});
}

// Надписи в разметке переводятся по атрибутам: в самом атрибуте лежит
// русский оригинал, он же ключ словаря.
function applyLanguage() {
  document.documentElement.lang = language;
  for (const element of document.querySelectorAll("[data-t]")) {
    element.textContent = t(element.dataset.t);
  }
  for (const element of document.querySelectorAll("[data-t-placeholder]")) {
    element.placeholder = t(element.dataset.tPlaceholder);
  }
  for (const element of document.querySelectorAll("[data-t-title]")) {
    element.title = t(element.dataset.tTitle);
  }
}
