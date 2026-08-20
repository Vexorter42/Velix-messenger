// Простейший сервис-воркер: нужен, чтобы приложение ставилось на домашний
// экран. Ничего не кеширует — чат всё равно живёт на связи с сервером.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
