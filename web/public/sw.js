/* SelfieTL service worker
 * Caches the app shell for fast launches when used as a Home Screen app.
 * Always bypasses the cache for /api/* so dynamic data is fresh.
 */
const SHELL_CACHE = "selfietl-shell-v3";
const SCOPE_PATH = new URL(self.registration.scope).pathname.replace(/\/$/, "");
const scoped = (path) => `${SCOPE_PATH}${path}` || "/";
const SHELL_FILES = [
  scoped("/"),
  scoped("/index.html"),
  scoped("/manifest.webmanifest"),
  scoped("/icon.svg"),
  scoped("/icon-192.png"),
  scoped("/icon-512.png"),
  scoped("/apple-touch-icon.png"),
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith(scoped("/api/"))) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(scoped("/index.html")).then((cached) => cached || Response.error())),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request)
        .then((response) => {
          if (response.ok && (url.pathname.startsWith(scoped("/assets/")) || SHELL_FILES.includes(url.pathname))) {
            const clone = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => Response.error());
    }),
  );
});
