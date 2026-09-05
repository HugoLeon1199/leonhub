/* LEON Hub offline shell.

   HTML is never cached. The apps are single files that change on every deploy,
   and a cached page is served from Cache Storage indefinitely -- it does not
   expire the way an HTTP response does. That combination shipped eleven app
   deploys that readers could not see: the pages were correct on the server and
   byte-identical to the repo, while browsers kept replaying an older copy.
   Bumping VERSION by hand was the intended remedy and it was forgotten, which
   is exactly what a manual step in a deploy path does.

   So the rule is now structural rather than remembered: navigation and HTML
   requests always go to the network, and only the static shell assets
   (manifest, icon) are cached. Published market data under data/ stays
   uncached for its own reason -- a stale price is indistinguishable from a
   live one, and the point-in-time warehouse exists so numbers carry their
   timestamp.

   What this costs: the hub no longer opens offline. That is the right trade.
   An offline shell that renders month-old prices is worse than a page that
   fails honestly, and the tabs are useless without live data anyway. */
const VERSION = "leon-v5";
const CORE = ["./manifest.webmanifest", "./assets/leon-icon.svg"];

self.addEventListener("install", event =>
  event.waitUntil(
    caches.open(VERSION)
      .then(cache => cache.addAll(CORE))
      .then(() => self.skipWaiting())
  ));

self.addEventListener("activate", event =>
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== VERSION).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  ));

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  // Anything that renders or carries data goes straight to the network. A
  // navigation request covers the hub itself; the extension test covers the
  // iframed tabs, which are ordinary document fetches.
  const isDocument =
    event.request.mode === "navigate" ||
    /\.html$/.test(url.pathname) ||
    url.pathname.endsWith("/");
  const isData = /\/data\/.*\.json$/.test(url.pathname);
  if (isDocument || isData) return;

  // Everything left is a static asset keyed by its own path: cache it, and
  // fall back to the cache only when the network is unavailable.
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then(cache => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
