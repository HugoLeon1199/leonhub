/* LEON Hub offline shell. The app shell (HTML/CSS/SVG) is cached so the hub
   opens offline. Published market data under data/ is never cached: a stale
   price served from disk is indistinguishable from a live one, and the whole
   point of the point-in-time warehouse is that numbers carry their timestamp.
   Offline, a data request fails honestly and the app renders its error state. */
const VERSION="leon-v4-20260904";
const CORE=["./","./hub/","./manifest.webmanifest","./assets/leon-icon.svg"];
self.addEventListener("install",event=>event.waitUntil(caches.open(VERSION).then(cache=>cache.addAll(CORE)).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==VERSION).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET")return;
  const url=new URL(event.request.url);
  if(url.origin!==location.origin)return;
  // Market data bypasses the cache entirely, in both directions.
  if(/\/data\/.*\.json$/.test(url.pathname))return;
  event.respondWith(fetch(event.request).then(response=>{
    if(response.ok){const copy=response.clone();caches.open(VERSION).then(cache=>cache.put(event.request,copy))}
    return response;
  }).catch(()=>caches.match(event.request).then(hit=>hit||caches.match("./hub/"))));
});
