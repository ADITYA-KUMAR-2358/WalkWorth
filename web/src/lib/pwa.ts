// Walkworth is an online app. Earlier builds installed an offline service
// worker; remove it and its caches so nobody is served a stale app.

export function removeOldServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.getRegistrations().then((regs) => regs.forEach((r) => r.unregister())).catch(() => undefined);
  if ("caches" in window) caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => undefined);
}
