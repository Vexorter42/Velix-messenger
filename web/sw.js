// Сервис-воркер: нужен, чтобы приложение ставилось на домашний экран и
// чтобы уведомления приходили, когда вкладка закрыта.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  // Текст уведомления присылает сервер — уже на языке подписчика
  let payload = {title: "Velix", body: ""};
  try {
    payload = {...payload, ...event.data.json()};
  } catch (error) {
    // Пришло что-то неожиданное — покажем общее уведомление
  }

  event.waitUntil(self.registration.showNotification(payload.title, {
    body: payload.body,
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: payload.tag || "velix",
    renotify: true,
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({type: "window",
                                                 includeUncontrolled: true});
    for (const client of clients) {
      if ("focus" in client) return client.focus();
    }
    return self.clients.openWindow("./");
  })());
});
