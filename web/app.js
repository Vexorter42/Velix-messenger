// Мобильный клиент Velix. Говорит с сервером тем же протоколом, что и
// оконный: JSON-кадры, а содержимое файлов — отдельным двоичным кадром следом.

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                "августа", "сентября", "октября", "ноября", "декабря"];
const AVATAR_COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862",
                       "#6ec9cb", "#65aadd", "#ee7aae"];
const MAX_MEDIA = 25 * 1024 * 1024;
// Что можно поставить на сообщение — короткий набор, как в Telegram
const EMOJI = ["👍", "❤", "😂", "🔥", "😢", "👎"];

const $ = (id) => document.getElementById(id);
const screens = {auth: $("auth"), list: $("list"), chat: $("chat"), profile: $("profile")};

let socket = null;
let user = {};
let registerMode = false;
let pendingHeader = null;      // описание вложения, ждущее свои байты
let lastSender = null;
let currentDate = null;
let conversation = 1;          // какая переписка открыта
let conversations = [];
let people = [];
let online = new Set();
let quotes = {};
let replyTo = null;
let pendingDirect = false;     // ждём номер только что созданной личной
let oldest = null;
let hasOlder = false;
let typingTimer = null;
let typingSent = 0;
const rows = new Map();        // номер сообщения -> его ряд в ленте
const reactions = new Map();   // номер сообщения -> {смайлик: [кто поставил]}
const reactionRows = new Map();// куда рисовать реакции
const mediaSlots = new Map();
const avatarSlots = new Map();
const avatarCache = new Map();

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

  socket.onopen = () => send(credentials);

  socket.onmessage = (event) => {
    if (typeof event.data !== "string") {
      handleBinary(event.data);
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "blob" || message.type === "update_blob") {
      pendingHeader = message;
      return;
    }
    handle(message);
  };

  socket.onclose = () => {
    $("status").textContent = "нет связи";
    if (!screens.chat.hidden) service("Соединение потеряно. Обновите страницу.");
  };

  socket.onerror = () => {
    $("auth-error").textContent = "Не удалось связаться с сервером.";
    $("primary").disabled = false;
  };
}

function send(message, payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({v: 4, ...message}));
  if (payload) socket.send(payload);
}

function handle(message) {
  switch (message.type) {
    case "welcome": onWelcome(message); break;
    case "authfail": onAuthFail(message.text); break;
    case "conversations": onConversations(message.items || []); break;
    case "people": onPeople(message); break;
    case "presence": onPresence(message); break;
    case "history": onHistory(message); break;
    case "text":
    case "media": onIncoming(message); break;
    case "deleted": onDeleted(message); break;
    case "reactions": onReactions(message); break;
    case "typing": onTyping(message); break;
    case "search": onSearch(message); break;
    case "profile": onProfile(message.user); break;
    case "push_key": subscribeToPush(message.key); break;
    case "system":
    case "error": service(message.text); break;
  }
}

function handleBinary(buffer) {
  const header = pendingHeader;
  pendingHeader = null;
  if (!header) return;

  const id = header.id;
  const url = URL.createObjectURL(new Blob([buffer]));

  if (avatarSlots.has(id)) {
    avatarCache.set(id, url);
    for (const element of avatarSlots.get(id)) {
      element.innerHTML = `<img src="${url}" alt="">`;
    }
    avatarSlots.delete(id);
    return;
  }

  const slot = mediaSlots.get(id);
  if (slot) {
    mediaSlots.delete(id);
    fillMedia(slot, header, url);
  }
}

// ------------------------------------------------------------------ вход

function onWelcome(message) {
  user = message.user || {};
  localStorage.setItem("velix.token", message.token || "");
  localStorage.setItem("velix.login", user.login || "");

  conversation = 1;
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
  $("auth-error").textContent = text || "Не пустили в чат.";
  $("primary").disabled = false;
  show("auth");
}

function onProfile(updated) {
  user = {...user, ...updated};
  $("profile-hint").textContent = "Сохранено";
  paintAvatar($("my-avatar"), user.name, user.avatar);
}

// -------------------------------------------------------- список переписок

function onConversations(items) {
  conversations = items;
  drawList();
}

function onPeople(message) {
  people = message.items || [];
  online = new Set(message.online || []);
  drawList();
}

function onPresence(message) {
  if (message.online) online.add(message.user);
  else online.delete(message.user);
  drawList();
}

function drawList() {
  const box = $("conversations");
  box.innerHTML = "";

  for (const item of conversations) {
    const row = document.createElement("div");
    row.className = "list-row";

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    paintAvatar(avatar, item.title || "Общий чат", item.avatar);
    row.append(avatar);

    const lines = document.createElement("div");
    lines.className = "list-lines";

    const title = document.createElement("strong");
    title.textContent = item.title || "Общий чат";
    lines.append(title);

    const preview = document.createElement("span");
    preview.className = "muted small";
    if (item.last) {
      const what = item.last.kind === "text" ? item.last.text : "вложение";
      preview.textContent = `${item.last.nick || ""}: ${what}`.slice(0, 42);
    } else {
      preview.textContent = "нет сообщений";
    }
    lines.append(preview);
    row.append(lines);

    row.addEventListener("click", () => openConversation(item.id, item.title));
    box.append(row);
  }

  const others = people.filter((person) => person.id !== user.id);
  const peopleBox = $("people");
  peopleBox.innerHTML = "";
  $("people-title").hidden = others.length === 0;

  for (const person of others) {
    const row = document.createElement("div");
    row.className = "list-row";

    const avatar = document.createElement("div");
    avatar.className = "avatar small";
    paintAvatar(avatar, person.name, person.avatar);
    row.append(avatar);

    const name = document.createElement("div");
    name.className = "list-lines";
    name.textContent = person.name;
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
  conversation = id;
  cancelReply();
  clearMessages();
  $("chat-title").textContent = title || "Общий чат";
  paintAvatar($("chat-avatar"), title || "Общий чат",
              (conversations.find((c) => c.id === id) || {}).avatar);
  show("chat");
  send({type: "open", conversation: id});
}

// -------------------------------------------------------------- сообщения

function clearMessages() {
  $("messages").innerHTML = "";
  rows.clear();
  reactionRows.clear();
  lastSender = null;
  currentDate = null;
  oldest = null;
}

function service(text) {
  const element = document.createElement("div");
  element.className = "service";
  element.textContent = text;
  $("messages").append(element);
  scrollDown();
}

function ensureDate(moment) {
  const date = moment.toLocaleDateString("ru-RU");
  if (date === currentDate) return;
  currentDate = date;

  const today = new Date().toLocaleDateString("ru-RU");
  service(date === today ? "Сегодня"
                         : `${moment.getDate()} ${MONTHS[moment.getMonth()]}`);
  lastSender = null;
}

function onHistory(message) {
  if (pendingDirect && message.conversation !== conversation) {
    // Это история личной переписки, которую мы только что попросили
    pendingDirect = false;
    conversation = message.conversation;
    const item = conversations.find((c) => c.id === conversation);
    $("chat-title").textContent = (item || {}).title || "Личная переписка";
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
    button.textContent = "Показать более старые";
    button.addEventListener("click", () => {
      if (oldest) send({type: "open", conversation, before: oldest});
    });
    $("messages").append(button);
  }
  if (!loadedItems.length) service("Пока тихо. Напишите первым.");
  for (const item of loadedItems) showItem(item);
  if (loadedItems.length) oldest = loadedItems[0].id;
}

let loadedItems = [];

function onIncoming(message) {
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

function showItem(item, localUrl) {
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
    gone.textContent = "сообщение удалено";
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
        (quoted.text || quoted.name || "вложение").slice(0, 60);
    bubble.append(strip);
  }

  if ((item.kind || "text") === "text") {
    const text = document.createElement("div");
    text.textContent = item.text || "";
    bubble.append(text);
  } else {
    const slot = document.createElement("div");
    bubble.append(slot);
    if (localUrl) {
      fillMedia(slot, item, localUrl);
    } else {
      slot.textContent = "загружаю…";
      slot.className = "muted small";
      if (item.media) {
        mediaSlots.set(item.media, slot);
        send({type: "fetch", id: item.media});
      }
    }
  }

  const time = document.createElement("div");
  time.className = "time";
  time.textContent = moment.toLocaleTimeString("ru-RU",
      {hour: "2-digit", minute: "2-digit"});
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

  const choice = prompt(
      "1 — реакция, 2 — ответить" + (own ? ", 3 — удалить" : ""), "1");
  if (choice === "1") pickEmoji(item.id);
  else if (choice === "2") startReply(item);
  else if (choice === "3" && own) send({type: "delete", id: item.id});
}

function startReply(item) {
  replyTo = item.id || null;
  if (!replyTo) return;
  $("reply-text").textContent = `Ответ ${item.nick}: ` +
      (item.text || item.name || "вложение").slice(0, 40);
  $("reply-bar").hidden = false;
}

function cancelReply() {
  replyTo = null;
  $("reply-bar").hidden = true;
}

function onDeleted(message) {
  const row = rows.get(message.id);
  if (!row) return;
  row.innerHTML = "";
  const gone = document.createElement("div");
  gone.className = "muted small";
  gone.textContent = "сообщение удалено";
  row.append(gone);
}

function onTyping(message) {
  if (message.conversation !== conversation) return;
  $("status").textContent = `${message.nick} печатает…`;
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => { $("status").textContent = ""; }, 3000);
}

function onSearch(message) {
  const items = message.items || [];
  clearMessages();
  service(items.length ? `Найдено: ${items.length}`
                       : `По запросу «${message.query}» ничего нет`);
  for (const item of items) showItem(item);
}

function fillMedia(slot, header, url) {
  slot.className = "";
  slot.textContent = "";

  if (header.kind === "image" || header.kind === "gif") {
    const picture = document.createElement("img");
    picture.src = url;
    picture.alt = header.name || "";
    picture.loading = "lazy";
    picture.addEventListener("load", scrollDown);
    slot.append(picture);
    return;
  }

  if (header.kind === "video") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    slot.append(video);
    return;
  }

  const link = document.createElement("a");
  link.className = "file";
  link.href = url;
  link.download = header.name || "файл";
  link.textContent = `Скачать ${header.name || "файл"}`;
  slot.append(link);
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

    send({type: "push_subscribe", subscription: subscription.toJSON()});
  } catch (error) {
    // Уведомления — приятное дополнение: не вышло, и ладно
    console.warn("подписка на уведомления не удалась", error);
  }
}

// -------------------------------------------------------------- отправка

async function sendFile(file) {
  if (!file) return;
  if (file.size > MAX_MEDIA) {
    service(`«${file.name}» больше 25 МБ, сервер такое не принимает.`);
    return;
  }

  const buffer = await file.arrayBuffer();
  const kind = file.type.startsWith("video/") ? "video"
             : file.type === "image/gif" ? "gif"
             : file.type.startsWith("image/") ? "image" : "file";

  send({type: "media", nick: user.name, kind, name: file.name, size: file.size,
        conversation, reply_to: replyTo}, buffer);

  showItem({nick: user.name, user: user.id, kind, name: file.name,
            size: file.size, at: new Date().toISOString()},
           URL.createObjectURL(file));
  cancelReply();
}

// ---------------------------------------------------------------- события

$("switch-mode").addEventListener("click", () => {
  registerMode = !registerMode;
  $("name").hidden = !registerMode;
  $("invite").hidden = !registerMode;
  $("primary").textContent = registerMode ? "Создать аккаунт" : "Войти";
  $("switch-mode").textContent = registerMode ? "У меня уже есть аккаунт"
                                              : "Создать аккаунт";
  $("auth-subtitle").textContent = registerMode ? "Нужен код приглашения"
                                                : "Вход в аккаунт";
  $("auth-error").textContent = "";
});

$("primary").addEventListener("click", () => {
  const login = $("login").value.trim();
  const password = $("password").value;
  if (!login || !password) {
    $("auth-error").textContent = "Заполните логин и пароль.";
    return;
  }

  $("primary").disabled = true;
  $("auth-error").textContent = "";
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

  field.value = "";
  send({type: "text", nick: user.name, text, conversation, reply_to: replyTo});
  showItem({nick: user.name, user: user.id, text, kind: "text",
            at: new Date().toISOString(), reply_to: replyTo});
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
  $("profile-hint").textContent = "Сохраняем…";
});

$("avatar-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  $("profile-hint").textContent = "Отправляем фото…";
  send({type: "avatar", name: file.name, size: file.size}, await file.arrayBuffer());
});

$("logout").addEventListener("click", () => {
  send({type: "logout"});
  localStorage.removeItem("velix.token");
  if (socket) socket.close();
  show("auth");
});

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
