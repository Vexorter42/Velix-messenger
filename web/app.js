// Мобильный клиент Velix. Говорит с сервером тем же протоколом, что и
// оконный: JSON-кадры, а содержимое файлов — отдельным двоичным кадром следом.

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                "августа", "сентября", "октября", "ноября", "декабря"];
const AVATAR_COLORS = ["#e17076", "#faa774", "#a695e7", "#7bc862",
                       "#6ec9cb", "#65aadd", "#ee7aae"];
const MAX_MEDIA = 25 * 1024 * 1024;

const $ = (id) => document.getElementById(id);
const screens = {auth: $("auth"), chat: $("chat"), profile: $("profile")};

let socket = null;
let user = {};
let registerMode = false;
let pendingHeader = null;      // описание вложения, ждущее свои байты
let lastSender = null;
let currentDate = null;
const mediaSlots = new Map();  // id вложения -> куда его вставить
const avatarSlots = new Map(); // id аватарки -> список элементов
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
  socket.send(JSON.stringify({v: 3, ...message}));
  if (payload) socket.send(payload);
}

function handle(message) {
  switch (message.type) {
    case "welcome": onWelcome(message); break;
    case "authfail": onAuthFail(message.text); break;
    case "history": (message.items || []).forEach(showItem); break;
    case "text":
    case "media": showItem(message); break;
    case "profile": onProfile(message.user); break;
    case "system":
    case "error": service(message.text); break;
  }
}

function handleBinary(buffer) {
  const header = pendingHeader;
  pendingHeader = null;
  if (!header) return;

  const id = header.id;
  const blob = new Blob([buffer]);
  const url = URL.createObjectURL(blob);

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

  $("messages").innerHTML = "";
  lastSender = null;
  currentDate = null;
  $("status").textContent = `вы вошли как ${user.name}`;
  $("primary").disabled = false;
  $("password").value = "";
  show("chat");
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

// -------------------------------------------------------------- сообщения

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

function showItem(item, localUrl) {
  const moment = item.at ? new Date(item.at) : new Date();
  ensureDate(moment);

  const own = item.nick === user.name;
  const grouped = lastSender === item.nick + own;
  lastSender = item.nick + own;

  const row = document.createElement("div");
  row.className = `row${own ? " own" : ""}${grouped ? " grouped" : ""}`;

  if (!own) {
    if (grouped) {
      const spacer = document.createElement("div");
      spacer.className = "spacer";
      row.append(spacer);
    } else {
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      paintAvatar(avatar, item.nick, item.avatar);
      row.append(avatar);
    }
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

  if ((item.kind || "text") === "text") {
    const text = document.createElement("div");
    text.textContent = item.text || "";
    bubble.append(text);
  } else {
    const slot = document.createElement("div");
    bubble.append(slot);

    if (localUrl) {
      // Своё вложение показываем сразу, не спрашивая сервер
      fillMedia(slot, item, localUrl);
    } else {
      slot.textContent = "загружаю…";
      slot.className = "muted small";
      if (item.id) {
        mediaSlots.set(item.id, slot);
        send({type: "fetch", id: item.id});
      }
    }
  }

  const time = document.createElement("div");
  time.className = "time";
  time.textContent = moment.toLocaleTimeString("ru-RU",
      {hour: "2-digit", minute: "2-digit"});
  bubble.append(time);

  row.append(bubble);
  $("messages").append(row);
  scrollDown();
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

  send({type: "media", nick: user.name, kind, name: file.name, size: file.size},
       buffer);

  showItem({nick: user.name, kind, name: file.name, size: file.size,
            at: new Date().toISOString()}, URL.createObjectURL(file));
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
  send({type: "text", nick: user.name, text});
  showItem({nick: user.name, text, kind: "text", at: new Date().toISOString()});
});

$("file").addEventListener("change", (event) => {
  sendFile(event.target.files[0]);
  event.target.value = "";
});

$("profile-button").addEventListener("click", () => {
  $("profile-name").value = user.name || "";
  $("profile-bio").value = user.bio || "";
  $("profile-hint").textContent = "";
  paintAvatar($("my-avatar"), user.name, user.avatar);
  show("profile");
});

$("back-to-chat").addEventListener("click", () => show("chat"));

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
