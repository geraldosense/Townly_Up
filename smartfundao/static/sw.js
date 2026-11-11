/* Townly Up — service worker (PWA).
 * - Guarda em cache os ficheiros estáticos para o site abrir mais depressa.
 * - Sem rede, as páginas mostram /offline em vez de um erro do navegador.
 * - Pedidos POST e a API nunca vêm da cache (dados sempre atuais).
 */
const VERSION = 'townly-v3';
const PRECACHE = [
  '/offline',
  '/static/css/style.css',
  '/static/js/map.js',
  '/static/img/logo.svg',
  '/static/img/icon-192.png',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')
      || url.pathname.startsWith('/media/')) {
    return; // deixa o navegador tratar normalmente
  }
  if (url.pathname.startsWith('/static/')) {
    // Ficheiros estáticos: rede primeiro (vê sempre a versão nova); sem ligação, usa a cache.
    event.respondWith(fetch(req).then(res => {
      if (res.ok) {  // nunca guardar erros (ex.: 404 de um ficheiro que ainda não existe)
        const copy = res.clone();
        caches.open(VERSION).then(cache => cache.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req)));
    return;
  }
  if (req.mode === 'navigate') {
    // Páginas: sempre da rede; sem ligação, mostra a página offline.
    event.respondWith(fetch(req).catch(() => caches.match('/offline')));
  }
});
