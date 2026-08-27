// Мобильный клиент Velix. Говорит с сервером тем же протоколом, что и
// оконный: JSON-кадры, а содержимое файлов — отдельным двоичным кадром следом.

const AVATAR_COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862",
                       "#6ec9cb", "#65aadd", "#ee7aae"];
const MAX_MEDIA = 25 * 1024 * 1024;

// Пределы вложений называет сервер в приветствии; здесь — на случай,
// если он о них умолчал (старая версия)
let limits = {file: 500 * 1024 * 1024, video: 1024 * 1024 * 1024,
              image: MAX_MEDIA, chunk: 4 * 1024 * 1024};
const uploads = new Map();     // что сейчас уходит на сервер
// Что можно поставить на сообщение — короткий набор, как в Telegram
const EMOJI = ["👍", "❤", "😂", "🔥", "😢", "👎"];

const $ = (id) => document.getElementById(id);
const screens = {auth: $("auth"), list: $("list"), chat: $("chat"), profile: $("profile")};

let socket = null;
let user = {};
let registerMode = false;
let pendingHeader = null;      // описание вложения, ждущее свои байты
let pendingParts = [];         // куски вложения, приехавшие до сих пор
let lastSender = null;
let currentDate = null;
let conversation = null;       // какая переписка открыта
let conversations = [];
let people = [];
let online = new Set();
let quotes = {};
let replyTo = null;
let editing = null;            // какое своё сообщение правим
let drafts = {};               // недописанное по переписке
let outbox = [];               // написанное, пока не было связи
let pendingDirect = false;     // ждём номер только что созданной личной
let oldest = null;
let hasOlder = false;
let typingTimer = null;
let typingSent = 0;
let typingWho = null;          // кто печатает прямо сейчас
const seen = new Map();        // кто когда был в сети последний раз
const rows = new Map();        // номер сообщения -> его ряд в ленте
const reactions = new Map();   // номер сообщения -> {смайлик: [кто поставил]}
const reactionRows = new Map();// куда рисовать реакции
const mediaSlots = new Map();
const gallery = [];            // что можно листать в полном экране
const tickRows = new Map();    // номер (или свой временный) -> значок галочек
const states = new Map();      // номер -> sent | delivered | read
const keptMedia = new Map();   // содержимое картинок: их могут копировать
const unread = new Map();      // переписка -> сколько пришло, пока не смотрели
let localNumber = 0;           // свои сообщения до ответа сервера
let pendingGroup = false;      // ждём номер только что созданной группы
const avatarSlots = new Map();
const avatarCache = new Map();

function titleOf(item) {
  // Общий чат заведён на сервере с русским названием, а показывать его
  // надо на языке человека
  if ((item || {}).id === 1) return t("Общий чат");
  return (item || {}).title || t("Общий чат");
}

let recoverMode = false;

function drawAuthMode() {
  $("name").hidden = !registerMode;
  $("invite").hidden = !registerMode;
  $("recovery-code").hidden = !recoverMode;
  $("forgot").hidden = registerMode || recoverMode;
  $("password").placeholder = recoverMode ? t("Новый пароль") : t("Пароль");

  if (recoverMode) {
    $("primary").textContent = t("Сменить пароль");
    $("switch-mode").textContent = t("Вернуться ко входу");
    $("auth-subtitle").textContent = t("Восстановление пароля");
    return;
  }

  $("primary").textContent = registerMode ? t("Создать аккаунт") : t("Войти");
  $("switch-mode").textContent = registerMode ? t("У меня уже есть аккаунт")
                                              : t("Создать аккаунт");
  $("auth-subtitle").textContent = registerMode ? t("Нужен код приглашения")
                                                : t("Вход в аккаунт");
}

function showRecovery(code) {
  // Код виден ровно один раз: дальше на сервере лежит только его хеш
  const sheet = document.createElement("div");
  sheet.className = "sheet";
  const card = document.createElement("div");
  card.className = "sheet-card";
  sheet.append(card);

  const title = document.createElement("strong");
  title.textContent = t("Сохраните код восстановления");
  card.append(title);

  const value = document.createElement("p");
  value.className = "recovery-code";
  value.textContent = code;
  card.append(value);

  const explain = document.createElement("p");
  explain.className = "muted small";
  explain.textContent = t("По нему меняют пароль, если его забыли. Другого "
      + "способа нет: почту мы не спрашиваем, а сервер стоит у вас дома.");
  card.append(explain);

  const copy = document.createElement("button");
  copy.className = "sheet-button";
  copy.textContent = t("Копировать");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.textContent = t("Код скопирован");
    } catch (error) {
      copy.textContent = t("Не удалось скопировать: {error}", {error});
    }
  });
  card.append(copy);

  const done = document.createElement("button");
  done.className = "primary";
  done.textContent = t("Понятно");
  done.addEventListener("click", () => sheet.remove());
  card.append(done);

  document.body.append(sheet);
}

function show(name) {
  for (const [key, element] of Object.entries(screens)) element.hidden = key !== name;
}

function avatarColor(name) {
  let sum = 0;
  for (const character of (name || "?")) sum += character.codePointAt(0);
  return AVATAR_COLORS[sum % AVATAR_COLORS.length];
}

function paintAvatar(element, name, avatarId) {
  element.textContent = (name || "?").trim().charAt(0).toUpperCase();
  element.style.background = avatarColor(name);
  // Кружок в шапке один на все переписки: помечаем, чьё фото он ждёт,
  // иначе запоздавшая картинка легла бы на уже другого человека
  element.dataset.avatar = avatarId || "";
  if (!avatarId) return;

  const cached = avatarCache.get(avatarId);
  if (cached) {
    element.innerHTML = `<img src="${cached}" alt="">`;
    return;
  }
  if (!avatarSlots.has(avatarId)) {
    avatarSlots.set(avatarId, []);
    send({type: "fetch", id: avatarId});
  }
  avatarSlots.get(avatarId).push(element);
}

// ------------------------------------------------------------ соединение

function connect(credentials) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/`);
  socket.binaryType = "arraybuffer";

  socket.onopen = () => {
    send(credentials);
    // Написанному без связи есть куда уйти; входу даём пройти первым
    setTimeout(flushOutbox, 1200);
  };

  socket.onmessage = (event) => {
    if (typeof event.data !== "string") {
      handleBinary(event.data);
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "blob" || message.type === "update_blob") {
      pendingHeader = message;
      pendingParts = [];
      return;
    }
    handle(message);
  };

  socket.onclose = () => {
    $("status").textContent = t("нет связи");
    scheduleReconnect();
  };

  socket.onerror = () => {
    $("auth-error").textContent = t("Не удалось связаться с сервером.");
    $("primary").disabled = false;
  };
}

let reconnectAttempt = 0;
let reconnectTimer = null;

/**
 * Связь на телефоне пропадает постоянно: экран гаснет, сеть переключается.
 * Раньше тут предлагали обновить страницу — теперь возвращаемся сами, с
 * растущей паузой, чтобы не долбить сервер.
 */
function scheduleReconnect() {
  const token = localStorage.getItem("velix.token");
  if (!token || reconnectTimer) {
    return;
  }
  const pause = Math.min(1000 * 2 ** reconnectAttempt, 30000);
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect({type: "auth", token});
  }, pause);
}

// Вернулись на вкладку — проверяем связь сразу, не дожидаясь паузы
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    return;
  }
  if (!socket || socket.readyState > WebSocket.OPEN) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    reconnectAttempt = 0;
    scheduleReconnect();
  } else if (conversation !== null) {
    markRead(loadedItems.filter((item) => item.id && item.user !== user.id)
                        .map((item) => item.id));
    // Пока вкладка была спрятана, сюда писали и это считалось
    // непрочитанным. Человек вернулся и смотрит — счёт обнуляем.
    unread.delete(conversation);
    drawList();
  }
});

function send(message, payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify({v: 6, ...message}));
  if (payload) socket.send(payload);
  return true;
}

// --- черновики: недописанное переживает переключение и закрытие вкладки

function keepDraft() {
  if (conversation === null) return;
  const field = $("text");
  if (!field || editing !== null) return;
  const текст = field.value.trim();
  if (текст) drafts[conversation] = текст;
  else delete drafts[conversation];
  try {
    localStorage.setItem("velix-drafts", JSON.stringify(drafts));
  } catch (ignored) {
    // Место кончилось или хранилище закрыто — черновик всё равно на экране
  }
}

function restoreDraft() {
  const field = $("text");
  if (field) field.value = drafts[conversation] || "";
}

function loadDrafts() {
  try {
    drafts = JSON.parse(localStorage.getItem("velix-drafts") || "{}");
  } catch (ignored) {
    drafts = {};
  }
}

// --- очередь: написанное без связи уходит, когда она вернётся

function flushOutbox() {
  while (outbox.length) {
    if (!send(outbox[0])) return;
    const кадр = outbox.shift();
    paintTick(кадр.local, "sending");
  }
}

function handle(message) {
  switch (message.type) {
    case "welcome": onWelcome(message); break;
    case "authfail": onAuthFail(fromServer(message)); break;
    case "ack": onAck(message); break;
    case "upload_ready":
      pushChunks(message.local, message.ticket, message.chunk);
      break;
    case "upload_progress": onUploadProgress(message); break;
    case "receipts": onReceipts(message); break;
    case "conversation": onConversation(message); break;
    case "conversations": onConversations(message.items || []); break;
    case "people": onPeople(message); break;
    case "presence": onPresence(message); break;
    case "history": onHistory(message); break;
    case "text":
    case "media": onIncoming(message); break;
    case "deleted": onDeleted(message); break;
    case "reactions": onReactions(message); break;
    case "typing": onTyping(message); break;
    case "edited": onEdited(message); break;
    case "search": onSearch(message); break;
    case "profile": onProfile(message.user); break;
    case "push_key": subscribeToPush(message.key); break;
    case "system":
    case "error": service(fromServer(message)); break;
  }
}

function handleBinary(buffer) {
  const header = pendingHeader;
  if (!header) return;

  // Большое вложение приезжает кусками: собираем, пока не наберётся всё
  pendingParts.push(buffer);
  const ждём = Math.max(1, header.parts || 1);
  if (pendingParts.length < ждём) {
    return;
  }
  pendingHeader = null;
  buffer = pendingParts.length === 1 ? pendingParts[0]
                                     : new Blob(pendingParts);
  pendingParts = [];

  const id = header.id;
  const url = URL.createObjectURL(new Blob([buffer]));

  if (avatarSlots.has(id)) {
    avatarCache.set(id, url);
    for (const element of avatarSlots.get(id)) {
      if (element.dataset.avatar === id) {
        element.innerHTML = `<img src="${url}" alt="">`;
      }
    }
    avatarSlots.delete(id);
    return;
  }

  const slot = mediaSlots.get(id);
  if (slot) {
    mediaSlots.delete(id);
    fillMedia(slot, header, url);
  } else {
    galleryUrl(id, url);
  }

  // Человек мог открыть полный экран раньше, чем вложение доехало
  const viewer = document.querySelector(".viewer");
  if (viewer && viewer.dataset.waiting === String(id)) {
    viewer.remove();
    showFull(url, header.kind, id);
  }
}

// ------------------------------------------------------------------ вход

async function pushChunks(local, ticket, chunk) {
  /* Читаем файл по кускам: держать гигабайт в памяти браузера незачем. */
  const upload = uploads.get(local);
  if (!upload) return;

  upload.ticket = ticket;
  for (let начало = 0; начало < upload.size; начало += chunk) {
    const кусок = await upload.file.slice(начало, начало + chunk).arrayBuffer();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    send({type: "chunk", ticket}, кусок);

    // Даём сокету разгрестись, иначе браузер соберёт всё в очередь и
    // память кончится ровно так же, как если бы мы слали одним куском
    while (socket.bufferedAmount > chunk * 2) {
      await new Promise((готово) => setTimeout(готово, 50));
    }
  }
}

function onUploadProgress(message) {
  const upload = [...uploads.values()].find(
      (one) => one.ticket === message.ticket);
  if (!upload) return;
  const доля = Math.round(100 * message.sent / Math.max(message.size, 1));
  if (upload.line) {
    upload.line.textContent = t("Отправляю «{name}» — {percent}%",
                               {name: upload.name, percent: доля});
  } else {
    upload.line = service(t("Отправляю «{name}» — {percent}%",
                           {name: upload.name, percent: доля}));
  }
}

function onWelcome(message) {
  reconnectAttempt = 0;
  user = message.user || {};
  loadDrafts();
  if (message.limits) {
    limits = Object.assign({}, limits, message.limits);
  }
  localStorage.setItem("velix.token", message.token || "");
  localStorage.setItem("velix.login", user.login || "");

  // Код приходит только при регистрации и после смены пароля
  recoverMode = false;
  drawAuthMode();
  if (message.recovery) showRecovery(message.recovery);

  conversation = null;
  conversations = [];
  people = [];
  online = new Set();
  quotes = {};
  clearMessages();
  $("primary").disabled = false;
  $("password").value = "";
  show("list");

  // Уведомления спрашиваем один раз и только если человек их не запрещал
  if ("Notification" in window && Notification.permission !== "denied") {
    send({type: "push_key"});
  }
}

function onAuthFail(text) {
  localStorage.removeItem("velix.token");
  $("auth-error").textContent = text || t("Не пустили в чат.");
  $("primary").disabled = false;
  show("auth");
}

function onProfile(updated) {
  user = {...user, ...updated};
  $("profile-hint").textContent = t("Сохранено");
  paintAvatar($("my-avatar"), user.name, user.avatar);
}

// -------------------------------------------------------- список переписок

function onConversations(items) {
  conversations = items;
  drawList();
  if (conversation === null && items.length) {
    openConversation(items[0].id, titleOf(items[0]));
  }
}

function onConversation(message) {
  // Появилась новая переписка — например, группа, куда нас позвали
  const item = message.item || {};
  if (!item.id) return;

  conversations = conversations.filter((known) => known.id !== item.id);
  conversations.push(item);
  drawList();
  if (pendingGroup || conversation === null) {
    pendingGroup = false;
    openConversation(item.id, titleOf(item));
    show("chat");
  }
}

function groupMenu(item) {
  // Что можно сделать с группой: сменить фото, удалить (если она ваша)
  if (item.kind !== "group") {
    return;
  }

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  const card = document.createElement("div");
  card.className = "sheet-card";
  sheet.append(card);

  const action = (label, handler) => {
    const button = document.createElement("button");
    button.className = "sheet-button";
    button.textContent = label;
    button.addEventListener("click", () => { sheet.remove(); handler(); });
    card.append(button);
  };

  action(t("Фото группы"), () => {
    groupPhotoTarget = item.id;
    $("avatar-file").click();
  });
  action(t("Позвать людей"), () => inviteToGroup(item));
  if (item.owner === user.id) {
    action(t("Удалить группу"), () => {
      if (confirm(t("Переписка и вложения пропадут у всех. Отменить это нельзя."))) {
        send({type: "delete_group", conversation: item.id});
        show("list");
      }
    });
  }
  action(t("Отмена"), () => {});

  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) sheet.remove();
  });
  document.body.append(sheet);
}

let groupPhotoTarget = null;

function inviteToGroup(item) {
  /* Кого позвать в уже заведённую группу: тех, кого там ещё нет. */
  const уже = new Set(item.members || []);
  const свободные = people.filter((person) => person.id !== user.id
      && !уже.has(person.id));
  if (!свободные.length) {
    service(t("Все уже в группе."));
    return;
  }

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  const card = document.createElement("div");
  card.className = "sheet-card";
  sheet.append(card);

  const выбраны = new Set();
  for (const person of свободные) {
    const кнопка = document.createElement("button");
    кнопка.className = "sheet-button";
    кнопка.textContent = `${person.name} · @${person.login || ""}`;
    кнопка.addEventListener("click", () => {
      if (выбраны.has(person.id)) {
        выбраны.delete(person.id);
        кнопка.classList.remove("chosen");
      } else {
        выбраны.add(person.id);
        кнопка.classList.add("chosen");
      }
    });
    card.append(кнопка);
  }

  const позвать = document.createElement("button");
  позвать.className = "sheet-button";
  позвать.textContent = t("Позвать");
  позвать.addEventListener("click", () => {
    if (выбраны.size) {
      send({type: "members", conversation: item.id, members: [...выбраны]});
    }
    sheet.remove();
  });
  card.append(позвать);

  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) sheet.remove();
  });
  document.body.append(sheet);
}

function newGroup() {
  // Заводим группу: название и галочки против имён — в той же карточке,
  // что и меню сообщения
  const others = people.filter((person) => person.id !== user.id);
  if (!others.length) {
    service(t("Пока некого позвать в группу."));
    return;
  }

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  const card = document.createElement("div");
  card.className = "sheet-card";
  sheet.append(card);

  const name = document.createElement("input");
  name.type = "text";
  name.placeholder = t("Название группы");
  card.append(name);

  const chosen = new Map();
  for (const person of others) {
    const line = document.createElement("label");
    line.className = "sheet-check";
    const box = document.createElement("input");
    box.type = "checkbox";
    line.append(box, document.createTextNode(" " + person.name));
    card.append(line);
    chosen.set(person.id, box);
  }

  const create = document.createElement("button");
  create.className = "primary";
  create.textContent = t("Создать");
  create.addEventListener("click", () => {
    const members = [...chosen.entries()]
        .filter(([, box]) => box.checked).map(([id]) => id);
    if (!name.value.trim() || !members.length) return;
    pendingGroup = true;
    send({type: "group", title: name.value.trim(), members});
    sheet.remove();
  });

  const cancel = document.createElement("button");
  cancel.className = "sheet-button";
  cancel.textContent = t("Отмена");
  cancel.addEventListener("click", () => sheet.remove());

  card.append(create, cancel);
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) sheet.remove();
  });
  document.body.append(sheet);
  name.focus();
}

function onPeople(message) {
  people = message.items || [];
  online = new Set(message.online || []);
  for (const person of people) if (person.seen) seen.set(person.id, person.seen);
  drawList();
  updateStatus();
}

function onPresence(message) {
  if (message.online) {
    online.add(message.user);
  } else {
    online.delete(message.user);
    if (message.seen) seen.set(message.user, message.seen);
  }
  drawList();
  updateStatus();
}

// Строчка под названием переписки: кто печатает, кто в сети, когда заходил
function updateStatus() {
  const item = conversations.find((one) => one.id === conversation) || {};
  const status = $("status");
  if (!status) return;

  status.classList.toggle("typing", Boolean(typingWho));
  if (typingWho) {
    status.textContent = t("{name} печатает…", {name: typingWho});
    return;
  }
  if (item.kind === "direct") {
    status.textContent = online.has(item.user)
        ? t("в сети")
        : t("был(а) в сети {when}", {when: seenText(seen.get(item.user))});
    return;
  }
  if (item.kind === "group" && (item.members || []).length) {
    status.textContent = t("участников: {count}", {count: item.members.length});
    return;
  }
  status.textContent = "";
}

function seenText(stamp) {
  if (!stamp) return t("давно");
  const when = new Date(stamp);
  if (Number.isNaN(when.getTime())) return t("давно");

  const now = new Date();
  if (now - when < 90 * 1000) return t("только что");

  const clock = when.toTimeString().slice(0, 5);
  const днями = (day) => new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const разница = Math.round((днями(now) - днями(when)) / 86400000);
  if (разница <= 0) return t("сегодня в {time}", {time: clock});
  if (разница === 1) return t("вчера в {time}", {time: clock});
  if (when.getFullYear() === now.getFullYear()) {
    return t("{date} в {time}", {date: monthDay(when), time: clock});
  }
  return when.toLocaleDateString();
}

function drawList() {
  const box = $("conversations");
  box.innerHTML = "";

  if (!conversations.length) {
    const hint = document.createElement("p");
    hint.className = "muted small section";
    hint.textContent = t("Создайте группу или найдите человека по @username.");
    box.append(hint);
  }

  const запрос = ($("search").value || "").trim()
      .replace("@", "").toLowerCase();

  for (const item of conversations) {
    if (запрос && !titleOf(item).toLowerCase().includes(запрос)) {
      continue;
    }
    const row = document.createElement("div");
    row.className = "list-row";

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    paintAvatar(avatar, titleOf(item), item.avatar);
    row.append(avatar);

    const lines = document.createElement("div");
    lines.className = "list-lines";

    const title = document.createElement("strong");
    // У группы значок: иначе её не отличить от человека
    title.textContent = (item.kind === "group" ? "\u{1F465} " : "")
        + titleOf(item);
    lines.append(title);

    const preview = document.createElement("span");
    preview.className = "muted small";
    if (item.last) {
      const what = item.last.kind === "text" ? item.last.text : t("вложение");
      preview.textContent = `${item.last.nick || ""}: ${what}`.slice(0, 42);
    } else {
      preview.textContent = t("нет сообщений");
    }
    lines.append(preview);
    row.append(lines);

    const waiting = unread.get(item.id) || 0;
    if (waiting > 0) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = String(waiting);
      row.append(badge);
    }

    row.addEventListener("click", () => openConversation(item.id, titleOf(item)));

    let hold = null;
    row.addEventListener("pointerdown", () => {
      hold = setTimeout(() => groupMenu(item), 500);
    });
    for (const event of ["pointerup", "pointercancel", "pointerleave"]) {
      row.addEventListener(event, () => clearTimeout(hold));
    }
    box.append(row);
  }

  // Всех подряд не показываем: людей находят поиском по @username
  const others = запрос
      ? people.filter((person) => person.id !== user.id
          && (String(person.login || "").toLowerCase().includes(запрос)
              || String(person.name || "").toLowerCase().includes(запрос)))
      : [];
  const peopleBox = $("people");
  peopleBox.innerHTML = "";
  $("people-title").hidden = others.length === 0;
  $("people-title").textContent = t("ЛЮДИ");

  for (const person of others) {
    const row = document.createElement("div");
    row.className = "list-row";

    const avatar = document.createElement("div");
    avatar.className = "avatar small";
    paintAvatar(avatar, person.name, person.avatar);
    row.append(avatar);

    const name = document.createElement("div");
    name.className = "list-lines";
    const кто = document.createElement("strong");
    кто.textContent = person.name;
    const ник = document.createElement("span");
    ник.className = "muted small";
    ник.textContent = "@" + (person.login || "");
    name.append(кто, ник);
    row.append(name);

    const dot = document.createElement("span");
    dot.className = online.has(person.id) ? "dot on" : "dot";
    row.append(dot);

    row.addEventListener("click", () => {
      pendingDirect = true;
      send({type: "direct", user: person.id});
    });
    peopleBox.append(row);
  }
}

function openConversation(id, title) {
  keepDraft();
  conversation = id;
  unread.delete(id);
  cancelReply();
  clearMessages();
  $("chat-title").textContent = title || t("Общий чат");
  paintAvatar($("chat-avatar"), title || t("Общий чат"),
              (conversations.find((c) => c.id === id) || {}).avatar);
  show("chat");
  restoreDraft();
  typingWho = null;
  updateStatus();
  send({type: "open", conversation: id});
}

// -------------------------------------------------------------- сообщения

function clearMessages() {
  $("messages").innerHTML = "";
  emptyHint = null;
  rows.clear();
  reactionRows.clear();
  gallery.length = 0;
  lastSender = null;
  currentDate = null;
  oldest = null;
}

function humanSize(size) {
  if (size < 1024) return `${size} ${t("Б")}`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} ${t("КБ")}`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} ${t("МБ")}`;
  return `${(size / 1024 ** 3).toFixed(1)} ${t("ГБ")}`;
}

function service(text) {
  const element = document.createElement("div");
  element.className = "service";
  element.textContent = text;
  $("messages").append(element);
  scrollDown();
  return element;
}

let emptyHint = null;

function clearHint() {
  // Подсказка «пока тихо» уходит, как только появляется первое сообщение
  if (emptyHint) emptyHint.remove();
  emptyHint = null;
}

function ensureDate(moment) {
  const date = moment.toDateString();
  if (date === currentDate) return;
  currentDate = date;

  const today = new Date().toDateString();
  service(date === today ? t("Сегодня") : monthDay(moment));
  lastSender = null;
}

function onHistory(message) {
  if (pendingDirect && message.conversation !== conversation) {
    // Это история личной переписки, которую мы только что попросили
    pendingDirect = false;
    conversation = message.conversation;
    const item = conversations.find((c) => c.id === conversation);
    $("chat-title").textContent = (item || {}).title || t("Личная переписка");
    paintAvatar($("chat-avatar"), (item || {}).title, (item || {}).avatar);
    show("chat");
  } else if (message.conversation !== conversation) {
    return;
  }

  Object.assign(quotes, message.quotes || {});
  for (const [key, value] of Object.entries(message.reactions || {})) {
    reactions.set(Number(key), value);
  }
  hasOlder = Boolean(message.more);
  const items = message.items || [];

  const previous = message.before ? [...loadedItems] : [];
  loadedItems = message.before ? items.concat(previous) : items;

  clearMessages();
  if (hasOlder) {
    const button = document.createElement("button");
    button.className = "link";
    button.textContent = t("Показать более старые");
    button.addEventListener("click", () => {
      if (oldest) send({type: "open", conversation, before: oldest});
    });
    $("messages").append(button);
  }
  if (!loadedItems.length) emptyHint = service(t("Пока тихо. Напишите первым."));
  for (const item of loadedItems) showItem(item);
  if (loadedItems.length) oldest = loadedItems[0].id;
  markRead(loadedItems.filter((item) => item.id && item.user !== user.id)
                      .map((item) => item.id));
}

let loadedItems = [];

function onIncoming(message) {
  countUnread(message);
  if (message.conversation !== conversation) {
    bumpPreview(message);
    return;
  }
  showItem(message);
  bumpPreview(message);
}

function bumpPreview(message) {
  for (const item of conversations) {
    if (item.id === message.conversation) {
      item.last = {text: message.text || "", kind: message.kind || "text",
                   at: message.at, nick: message.nick};
    }
  }
  if (!screens.list.hidden) drawList();
}

function countUnread(message) {
  // Своё сообщение и то, что пришло в открытую переписку, непрочитанным
  // не считается
  if (message.user === user.id) {
    return;
  }
  const where = message.conversation;
  if (where === conversation && !document.hidden && !screens.chat.hidden) {
    return;
  }
  unread.set(where, (unread.get(where) || 0) + 1);
  drawList();
}

function showItem(item, localUrl) {
  clearHint();
  const moment = item.at ? new Date(item.at) : new Date();
  ensureDate(moment);

  const own = item.user === user.id || (item.user === undefined && item.own);
  const grouped = lastSender === String(item.nick) + own;
  lastSender = String(item.nick) + own;

  const row = document.createElement("div");
  row.className = `row${own ? " own" : ""}${grouped ? " grouped" : ""}`;
  if (item.id) rows.set(item.id, row);

  if (item.kind === "deleted") {
    const gone = document.createElement("div");
    gone.className = "muted small";
    gone.textContent = t("сообщение удалено");
    row.append(gone);
    $("messages").append(row);
    scrollDown();
    return;
  }

  if (!own) {
    const holder = document.createElement("div");
    if (grouped) {
      holder.className = "spacer";
    } else {
      holder.className = "avatar";
      paintAvatar(holder, item.nick, item.avatar);
    }
    row.append(holder);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (!own && !grouped) {
    const sender = document.createElement("div");
    sender.className = "sender";
    sender.textContent = item.nick;
    sender.style.color = avatarColor(item.nick);
    bubble.append(sender);
  }

  const quoted = quotes[String(item.reply_to)];
  if (quoted) {
    const strip = document.createElement("div");
    strip.className = "quote";
    strip.textContent = `${quoted.nick || ""}: ` +
        (quoted.text || quoted.name || t("вложение")).slice(0, 60);
    bubble.append(strip);
  }

  if ((item.kind || "text") === "text") {
    const text = document.createElement("div");
    text.className = "body";
    text.textContent = item.text || "";
    bubble.append(text);
  } else {
    const slot = document.createElement("div");
    bubble.append(slot);
    rememberMedia(item.kind, item.media, item.name);
    if (localUrl) {
      fillMedia(slot, item, localUrl);
    } else {
      slot.textContent = t("загружаю…");
      slot.className = "muted small";
      if (item.media) {
        mediaSlots.set(item.media, slot);
        send({type: "fetch", id: item.media});
      }
    }
  }

  const time = document.createElement("div");
  time.className = "time";
  if (item.edited) time.append(t("изменено") + " · ");
  time.textContent = moment.toLocaleTimeString([],
      {hour: "2-digit", minute: "2-digit"});
  if (own) {
    // Галочки: одна — сервер принял, две — дошло до всех, голубые — прочли
    const mark = document.createElement("span");
    const key = item.id || item.local;
    time.append(" ", mark);
    if (key !== undefined) {
      tickRows.set(key, mark);
      if (item.waiting && !states.get(key)) states.set(key, "waiting");
      paintTick(key, item.state || states.get(key)
                     || (item.id ? "sent" : "sending"));
    }
  }
  bubble.append(time);

  // Долгое нажатие на пузыре — ответить или удалить своё
  let timer = null;
  bubble.addEventListener("pointerdown", () => {
    timer = setTimeout(() => messageMenu(item, own), 500);
  });
  for (const event of ["pointerup", "pointercancel", "pointerleave"]) {
    bubble.addEventListener(event, () => clearTimeout(timer));
  }

  if (item.id) {
    const marks = document.createElement("div");
    marks.className = "reactions";
    marks.hidden = true;
    bubble.append(marks);
    reactionRows.set(item.id, marks);
    drawReactions(item.id);
  }

  row.append(bubble);
  $("messages").append(row);
  scrollDown();
}

const TICKS = {waiting: "🕓", sending: "·", sent: "✓",
               delivered: "✓✓", read: "✓✓"};

function paintTick(key, state) {
  states.set(key, state);
  const mark = tickRows.get(key);
  if (!mark) return;
  mark.textContent = TICKS[state] || "✓";
  mark.className = state === "read" ? "tick read" : "tick";
}

function onAck(message) {
  // Сервер принял сообщение и назвал его настоящий номер
  if (!message.id) return;

  const upload = uploads.get(message.local);
  if (upload) {
    // Большое вложение доехало: строчку о ходе убираем, показываем сам файл
    uploads.delete(message.local);
    if (upload.line) upload.line.remove();
    const item = {nick: user.name, user: user.id, id: message.id,
                  kind: kindOf(upload.name), name: upload.name,
                  size: upload.size, media: message.media,
                  at: message.at || new Date().toISOString(),
                  local: message.local, conversation};
    loadedItems.push(item);
    showItem(item, URL.createObjectURL(upload.file));
  }
  for (const item of loadedItems) {
    if (message.local && item.local === message.local) item.id = message.id;
  }
  const mark = tickRows.get(message.local);
  tickRows.delete(message.local);
  states.delete(message.local);
  if (mark) tickRows.set(message.id, mark);
  paintTick(message.id, states.get(message.id) || "sent");
}

function onReceipts(message) {
  for (const [key, state] of Object.entries(message.items || {})) {
    paintTick(Number(key), state);
  }
}

function markRead(ids) {
  if (!ids.length || conversation === null || document.hidden) return;
  send({type: "read", conversation, ids});
}

function drawReactions(messageId) {
  const holder = reactionRows.get(messageId);
  if (!holder) return;

  holder.innerHTML = "";
  const summary = reactions.get(messageId) || {};
  const entries = Object.entries(summary).sort();
  holder.hidden = entries.length === 0;

  for (const [emoji, who] of entries) {
    const button = document.createElement("button");
    button.className = who.includes(user.id) ? "reaction mine" : "reaction";
    button.textContent = `${emoji} ${who.length}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      send({type: "react", id: messageId, emoji});
    });
    holder.append(button);
  }
}

function onReactions(message) {
  reactions.set(message.id, message.reactions || {});
  drawReactions(message.id);
}

function pickEmoji(messageId) {
  const sheet = document.createElement("div");
  sheet.className = "sheet";
  for (const emoji of EMOJI) {
    const button = document.createElement("button");
    button.className = "sheet-emoji";
    button.textContent = emoji;
    button.addEventListener("click", () => {
      send({type: "react", id: messageId, emoji});
      sheet.remove();
    });
    sheet.append(button);
  }
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) sheet.remove();
  });
  document.body.append(sheet);
}

function messageMenu(item, own) {
  if (!item.id) return;

  const sheet = document.createElement("div");
  sheet.className = "sheet";

  const card = document.createElement("div");
  card.className = "sheet-card";
  sheet.append(card);

  const close = () => sheet.remove();
  const action = (label, handler) => {
    const button = document.createElement("button");
    button.className = "sheet-button";
    button.textContent = label;
    button.addEventListener("click", () => { close(); handler(); });
    card.append(button);
  };

  action(t("Ответить"), () => startReply(item));
  action(t("Реакция"), () => pickEmoji(item.id));
  action(t("Копировать"), () => copyItem(item));
  if (own && (item.kind || "text") === "text") {
    action(t("Изменить"), () => startEdit(item));
  }
  if (own) action(t("Удалить"), () => send({type: "delete", id: item.id}));
  action(t("Отмена"), () => {});

  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) close();
  });
  document.body.append(sheet);
}

async function copyItem(item) {
  // Текст ложится текстом, картинка — картинкой: так её можно вставить
  // в другой чат, а не пересылать ссылкой
  try {
    if ((item.kind || "text") === "text") {
      await navigator.clipboard.writeText(item.text || "");
      service(t("Скопировано"));
      return;
    }

    const data = keptMedia.get(item.media);
    if (!data) {
      service(t("Сначала откройте вложение"));
      return;
    }
    if (item.kind === "image" && window.ClipboardItem) {
      const png = await (await fetch(data)).blob();
      await navigator.clipboard.write([new ClipboardItem({[png.type]: png})]);
    } else {
      await navigator.clipboard.writeText(item.name || "");
    }
    service(t("Скопировано"));
  } catch (error) {
    service(t("Не удалось скопировать: {error}", {error}));
  }
}

function startReply(item) {
  replyTo = item.id || null;
  if (!replyTo) return;
  $("reply-text").textContent = t("Ответ {name}: ", {name: item.nick}) +
      (item.text || item.name || t("вложение")).slice(0, 40);
  $("reply-bar").hidden = false;
}

function cancelReply() {
  replyTo = null;
  editing = null;
  $("reply-bar").hidden = true;
}

function onDeleted(message) {
  const row = rows.get(message.id);
  if (!row) return;
  row.innerHTML = "";
  const gone = document.createElement("div");
  gone.className = "muted small";
  gone.textContent = t("сообщение удалено");
  row.append(gone);
}

function startEdit(item) {
  // Текст возвращается в ту же строку ввода: человек и так пишет внизу
  replyTo = null;
  editing = item.id;
  $("reply-text").textContent = t("Правим: {text}",
                                  {text: (item.text || "").slice(0, 40)});
  $("reply-bar").hidden = false;
  const field = $("text");
  field.value = item.text || "";
  field.focus();
}

function onEdited(message) {
  for (const item of loadedItems) {
    if (item.id === message.id) {
      item.text = message.text || "";
      item.edited = message.edited;
      break;
    }
  }
  if (message.conversation !== conversation) return;

  const row = rows.get(message.id);
  if (!row) return;
  const body = row.querySelector(".body");
  if (body) body.textContent = message.text || "";
  const time = row.querySelector(".time");
  if (time && !time.textContent.startsWith(t("изменено"))) {
    time.prepend(t("изменено") + " · ");
  }
}

function onTyping(message) {
  if (message.conversation !== conversation) return;
  typingWho = message.nick;
  updateStatus();
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => { typingWho = null; updateStatus(); }, 3000);
}

function onSearch(message) {
  const items = message.items || [];
  clearMessages();
  service(items.length ? t("Найдено: {count}", {count: items.length})
                       : t("По запросу «{query}» ничего нет", {query: message.query}));
  for (const item of items) showItem(item);
}

// Порядок листания — как в ленте: стрелка влево показывает то, что было выше
function rememberMedia(kind, media, name) {
  if (!media || !["image", "gif", "video"].includes(kind)) return;
  if (gallery.some((one) => one.media === media)) return;
  gallery.push({media, kind, name: name || t("вложение"), url: null});
}

function galleryUrl(media, url) {
  const item = gallery.find((one) => one.media === media);
  if (item) item.url = url;
}

function fillMedia(slot, header, url) {
  slot.className = "";
  slot.textContent = "";
  rememberMedia(header.kind, header.media, header.name);
  galleryUrl(header.media, url);

  if (header.kind === "image" || header.kind === "gif") {
    const picture = document.createElement("img");
    picture.src = url;
    picture.alt = header.name || "";
    picture.loading = "lazy";
    picture.addEventListener("load", scrollDown);
    picture.addEventListener("click", () => showFull(url, header.kind, header.media));
    slot.append(picture);
    if (header.media) keptMedia.set(header.media, url);
    return;
  }

  if (header.kind === "video") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    video.addEventListener("dblclick", () =>
        showFull(url, "video", header.media));
    slot.append(video);
    return;
  }

  const link = document.createElement("a");
  link.className = "file";
  link.href = url;
  link.download = header.name || t("файл");
  link.textContent = header.size
      ? `${t("Скачать {name}", {name: header.name || t("файл")})} · `
        + humanSize(header.size)
      : t("Скачать {name}", {name: header.name || t("файл")});
  slot.append(link);
}

function showFull(url, kind, media) {
  // Вложение во весь экран. Листается стрелками, колесом и пальцем;
  // закрывается по нажатию мимо картинки, по крестику и по Escape
  const прежний = document.querySelector(".viewer");
  if (прежний) прежний.remove();

  let список = media ? gallery.filter((one) => one.url || one.media === media) : [];
  let место = список.findIndex((one) => one.media === media);
  if (место < 0) {
    список = [{media, kind, url, name: ""}];
    место = 0;
  }

  const viewer = document.createElement("div");
  viewer.className = "viewer";

  const stage = document.createElement("div");
  stage.className = "viewer-stage";
  viewer.append(stage);

  const counter = document.createElement("div");
  counter.className = "viewer-counter";
  viewer.append(counter);

  const close = document.createElement("button");
  close.className = "viewer-close";
  close.textContent = "\u2715";
  close.addEventListener("click", () => закрыть());
  viewer.append(close);

  function шагнуть(куда) {
    if (список.length < 2) return;
    место = (место + куда + список.length) % список.length;
    нарисовать();
  }

  function нарисовать() {
    const item = список[место];
    stage.textContent = "";
    counter.textContent = список.length > 1
        ? (место + 1) + " / " + список.length : "";

    if (!item.url) {
      const ждём = document.createElement("p");
      ждём.className = "muted";
      ждём.textContent = t("загружаю…");
      stage.append(ждём);
      if (item.media) {
        viewer.dataset.waiting = String(item.media);
        send({type: "fetch", id: item.media});
      }
      return;
    }

    viewer.dataset.waiting = "";
    if (item.kind === "video") {
      const video = document.createElement("video");
      video.src = item.url;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      stage.append(video);
      return;
    }

    const picture = document.createElement("img");
    picture.src = item.url;
    picture.alt = item.name || "";
    stage.append(picture);
  }

  if (список.length > 1) {
    for (const пара of [["\u2039", -1], ["\u203a", 1]]) {
      const arrow = document.createElement("button");
      arrow.className = "viewer-arrow " + (пара[1] < 0 ? "left" : "right");
      arrow.textContent = пара[0];
      arrow.addEventListener("click", (event) => {
        event.stopPropagation();
        шагнуть(пара[1]);
      });
      viewer.append(arrow);
    }
  }

  function поклавише(event) {
    if (event.key === "Escape") закрыть();
    else if (event.key === "ArrowLeft") шагнуть(-1);
    else if (event.key === "ArrowRight") шагнуть(1);
  }

  function закрыть() {
    document.removeEventListener("keydown", поклавише);
    viewer.remove();
  }

  // Палец: смахивание вбок листает, как в галерее телефона
  let палец = null;
  viewer.addEventListener("touchstart", (event) => {
    палец = event.touches[0] ? event.touches[0].clientX : null;
  }, {passive: true});
  viewer.addEventListener("touchend", (event) => {
    const конец = event.changedTouches[0];
    if (палец === null || !конец) return;
    const сдвиг = конец.clientX - палец;
    палец = null;
    if (Math.abs(сдвиг) > 60) шагнуть(сдвиг < 0 ? 1 : -1);
  }, {passive: true});

  viewer.addEventListener("click", (event) => {
    if (event.target === viewer) закрыть();
  });
  document.addEventListener("keydown", поклавише);
  viewer.velixStep = шагнуть;
  document.body.append(viewer);
  нарисовать();
}

function scrollDown() {
  const messages = $("messages");
  messages.scrollTop = messages.scrollHeight;
}

// -------------------------------------------------------- уведомления

function decodeKey(base64) {
  // Ключ приходит в base64url, а браузеру нужен массив байтов
  const padded = (base64 + "=".repeat((4 - base64.length % 4) % 4))
      .replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function subscribeToPush(key) {
  if (!key || !("serviceWorker" in navigator) || !("PushManager" in window)) return;

  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return;

    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    const subscription = existing || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeKey(key),
    });

    // Язык кладём в подписку: уведомление придёт на том же языке,
    // на котором человек читает приложение
    send({type: "push_subscribe",
          subscription: {...subscription.toJSON(), language}});
  } catch (error) {
    // Уведомления — приятное дополнение: не вышло, и ладно
    console.warn("подписка на уведомления не удалась", error);
  }
}

// -------------------------------------------------------------- отправка

function kindOf(name) {
  const конец = String(name || "").toLowerCase().split(".").pop();
  if (конец === "gif") return "gif";
  if (["png", "jpg", "jpeg", "webp", "bmp"].includes(конец)) return "image";
  if (["mp4", "mov", "webm", "mkv", "avi", "m4v"].includes(конец)) return "video";
  return "file";
}

function limitFor(kind) {
  if (kind === "video") return limits.video;
  if (kind === "image" || kind === "gif") return limits.image;
  return limits.file;
}

async function sendFile(file) {
  if (!file) return;

  const kind = file.type.startsWith("video/") ? "video"
             : file.type === "image/gif" ? "gif"
             : file.type.startsWith("image/") ? "image" : "file";

  const предел = limitFor(kind);
  if (file.size > предел) {
    service(t("«{name}» весит {size}, а больше {limit} сервер не принимает.",
              {name: file.name, size: humanSize(file.size),
               limit: humanSize(предел)}));
    return;
  }

  if (conversation === null) return;

  const local = `l${++localNumber}`;

  // Картинку отправляем целиком — её ещё и ужмут на сервере. Всё
  // остальное едет кусками: гигабайтное видео одним кадром не передать
  if (kind === "image" || kind === "gif") {
    const buffer = await file.arrayBuffer();
    send({type: "media", nick: user.name, kind, name: file.name,
          size: file.size, conversation, reply_to: replyTo, local}, buffer);
    const item = {nick: user.name, user: user.id, kind, name: file.name,
                  size: file.size, at: new Date().toISOString(), local,
                  conversation};
    loadedItems.push(item);
    showItem(item, URL.createObjectURL(file));
    cancelReply();
    return;
  }

  // Большое вложение показываем не сразу: сперва пусть доедет. Пока оно
  // едет, в переписке видно, сколько уже ушло
  uploads.set(local, {file, name: file.name, size: file.size});
  uploads.get(local).line = service(t("Отправляю «{name}» — {percent}%",
                                      {name: file.name, percent: 0}));
  send({type: "upload", name: file.name, size: file.size, conversation,
        reply_to: replyTo, local});
  cancelReply();
}

// ---------------------------------------------------------------- события

$("switch-mode").addEventListener("click", () => {
  if (recoverMode) {
    recoverMode = false;
  } else {
    registerMode = !registerMode;
  }
  drawAuthMode();
  $("auth-error").textContent = "";
});

$("forgot").addEventListener("click", () => {
  recoverMode = true;
  registerMode = false;
  drawAuthMode();
  $("auth-error").textContent = "";
});

$("primary").addEventListener("click", () => {
  const login = $("login").value.trim();
  const password = $("password").value;
  if (!login || !password) {
    $("auth-error").textContent = t("Заполните логин и пароль.");
    return;
  }

  if (recoverMode && !$("recovery-code").value.trim()) {
    $("auth-error").textContent = t("Заполните логин, код и новый пароль.");
    return;
  }

  $("primary").disabled = true;
  $("auth-error").textContent = "";

  if (recoverMode) {
    connect({type: "recover", login, password,
             code: $("recovery-code").value.trim()});
    return;
  }
  connect(registerMode
    ? {type: "register", login, password, name: $("name").value.trim() || login,
       invite: $("invite").value.trim()}
    : {type: "login", login, password});
});

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const field = $("text");
  const text = field.value.trim();
  if (!text) return;

  if (conversation === null) return;

  if (editing !== null) {
    // Правка уходит вместо нового сообщения; лента поменяется, когда
    // сервер подтвердит — так все увидят одно и то же
    send({type: "edit", id: editing, text});
    field.value = "";
    cancelReply();
    return;
  }

  field.value = "";
  delete drafts[conversation];
  keepDraft();
  const local = `l${++localNumber}`;
  const кадр = {type: "text", nick: user.name, text, conversation,
                reply_to: replyTo, local};
  const ушло = send(кадр);
  if (!ушло) outbox.push(кадр);
  const item = {nick: user.name, user: user.id, text, kind: "text", local,
                waiting: !ушло,
                at: new Date().toISOString(), reply_to: replyTo, conversation};
  loadedItems.push(item);
  showItem(item);
  cancelReply();
});

$("text").addEventListener("input", () => {
  const now = Date.now();
  if (now - typingSent < 2000) return;
  typingSent = now;
  send({type: "typing", conversation});
});

$("file").addEventListener("change", (event) => {
  sendFile(event.target.files[0]);
  event.target.value = "";
});

$("cancel-reply").addEventListener("click", cancelReply);
$("back-to-list").addEventListener("click", () => { drawList(); show("list"); });

$("search").addEventListener("input", () => drawList());

$("search").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const query = $("search").value.trim();
  if (query.length < 2) return;
  show("chat");
  send({type: "search", query});
});

$("profile-button").addEventListener("click", () => {
  $("profile-name").value = user.name || "";
  $("profile-bio").value = user.bio || "";
  $("profile-hint").textContent = "";
  paintAvatar($("my-avatar"), user.name, user.avatar);
  show("profile");
});

$("back-to-chat").addEventListener("click", () => { drawList(); show("list"); });

$("save-profile").addEventListener("click", () => {
  send({type: "profile", name: $("profile-name").value.trim(),
        bio: $("profile-bio").value.trim()});
  $("profile-hint").textContent = t("Сохраняем…");
});

$("avatar-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;

  // Та же кнопка ставит и фото группы — смотря откуда её позвали
  if (groupPhotoTarget !== null) {
    const room = groupPhotoTarget;
    groupPhotoTarget = null;
    send({type: "avatar", conversation: room, name: file.name, size: file.size},
         await file.arrayBuffer());
    return;
  }

  $("profile-hint").textContent = t("Отправляем фото…");
  send({type: "avatar", name: file.name, size: file.size}, await file.arrayBuffer());
});

$("logout").addEventListener("click", () => {
  send({type: "logout"});
  localStorage.removeItem("velix.token");
  if (socket) socket.close();
  show("auth");
});

$("new-group").addEventListener("click", newGroup);

$("language").value = language;
$("language").addEventListener("change", (event) => {
  setLanguage(event.target.value);
  applyLanguage();
  drawAuthMode();
  drawList();
  if (loadedItems.length || !screens.chat.hidden) {
    const item = conversations.find((c) => c.id === conversation);
    $("chat-title").textContent = titleOf(item);
    onHistory({conversation, items: loadedItems, more: hasOlder});
  }
  // Подписку пересылаем: сервер должен знать новый язык уведомлений
  if ("Notification" in window && Notification.permission === "granted") {
    send({type: "push_key"});
  }
});

// Кнопка «назад» в приложении для Android: из переписки — к списку,
// из списка — выход. Обёртка спрашивает об этом страницу.
window.velixBack = () => {
  if (document.querySelector(".viewer, .sheet")) {
    document.querySelector(".viewer, .sheet").remove();
    return true;
  }
  if (!screens.chat.hidden || !screens.profile.hidden) {
    drawList();
    show("list");
    return true;
  }
  return false;
};

applyLanguage();
drawAuthMode();
// Заголовок переписки живёт отдельно: он меняется по ходу дела
$("chat-title").textContent = t("Общий чат");

// Сохранённый токен пускает без пароля
const savedToken = localStorage.getItem("velix.token");
if (savedToken) {
  $("login").value = localStorage.getItem("velix.login") || "";
  connect({type: "auth", token: savedToken});
} else {
  show("auth");
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
