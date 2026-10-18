/* Second Brain — service worker (C2, layer 1).
   Goal: with no connection at all, the app opens, lists what was synced, and
   shows every page that was already fetched.

   Strategy per resource class:
     navigation  -> network first, cached shell as the fallback
     news.json   -> network first, last copy on failure (fresh when online)
     full/*.json -> cache first (a page's text never changes once captured)
     static      -> cache first, refresh in the background
*/
const SHELL = "sb-shell-v1";
const FULL = "sb-full-v1";

const SHELL_FILES = ["./", "./index.html", "./styles.css", "./app.js", "./manifest.webmanifest"];

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    await Promise.all(SHELL_FILES.map(f => c.add(new Request(f, {cache: "reload"})).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(n => n !== SHELL && n !== FULL).map(n => caches.delete(n)));
    await self.clients.claim();
  })());
});

async function networkFirst(req, cacheName){
  const c = await caches.open(cacheName);
  try{
    const r = await fetch(req);
    if(r && r.ok) c.put(req, r.clone());
    return r;
  }catch(err){
    const hit = await c.match(req);
    if(hit) return hit;
    throw err;
  }
}

async function cacheFirst(req, cacheName){
  const c = await caches.open(cacheName);
  const hit = await c.match(req);
  if(hit) return hit;
  const r = await fetch(req);
  if(r && r.ok) c.put(req, r.clone());
  return r;
}

async function staleWhileRevalidate(req, cacheName){
  const c = await caches.open(cacheName);
  const hit = await c.match(req);
  const fetching = fetch(req).then(r => {
    if(r && r.ok) c.put(req, r.clone());
    return r;
  }).catch(() => null);
  return hit || (await fetching) || Response.error();
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if(req.method !== "GET") return;
  const url = new URL(req.url);
  if(url.origin !== self.location.origin) return;   // never touch other origins

  if(req.mode === "navigate"){
    e.respondWith(networkFirst(req, SHELL).catch(async () => {
      const c = await caches.open(SHELL);
      return (await c.match("./index.html")) || (await c.match("./")) || Response.error();
    }));
    return;
  }
  if(url.pathname.endsWith("/data/news.json")){
    e.respondWith(networkFirst(req, SHELL));
    return;
  }
  if(url.pathname.includes("/data/full/")){
    e.respondWith(cacheFirst(req, FULL));
    return;
  }
  e.respondWith(staleWhileRevalidate(req, SHELL));
});
