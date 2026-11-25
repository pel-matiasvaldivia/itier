"""App móvil (PWA): se instala desde el navegador en iPhone y Android, sin tiendas de aplicaciones.

  GET /manifest.webmanifest   nombre, iconos y colores de la app
  GET /sw.js                  service worker: caché mínima de estáticos, pantalla sin conexión y avisos push
  GET /offline                lo que se ve si el teléfono no tiene señal
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, Response

router = APIRouter()

MANIFEST = {
    "name": "iTiers",
    "short_name": "iTiers",
    "description": "Guardia y soporte de TI: alertas, tickets y equipos desde el celular.",
    "lang": "es",
    "id": "/guardia",
    "start_url": "/guardia",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#F4F6FB",
    "theme_color": "#4F46E5",
    "icons": [
        {"src": "/static/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
    "shortcuts": [
        {"name": "Guardia", "url": "/guardia", "icons": [{"src": "/static/pwa/icon-192.png", "sizes": "192x192"}]},
        {"name": "Tickets", "url": "/tickets", "icons": [{"src": "/static/pwa/icon-192.png", "sizes": "192x192"}]},
        {"name": "Equipos", "url": "/equipos", "icons": [{"src": "/static/pwa/icon-192.png", "sizes": "192x192"}]},
    ],
}

SW = r"""/* iTiers · service worker. Liviano a propósito: solo cachea lo estático y muestra una pantalla propia sin conexión. */
const V = 'itiers-v1';
const SHELL = ['/offline', '/static/favicon.svg', '/static/pwa/icon-192.png'];

self.addEventListener('install', e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin !== location.origin) return;
  if (u.pathname.startsWith('/static/')) {            // estáticos: del caché, y se refresca en segundo plano
    e.respondWith(caches.open(V).then(c => c.match(r).then(hit => {
      const red = fetch(r).then(resp => { if (resp.ok) c.put(r, resp.clone()); return resp; }).catch(() => hit);
      return hit || red;
    })));
    return;
  }
  if (r.mode === 'navigate') {                         // páginas: siempre de la red (datos vivos); sin señal, pantalla offline
    e.respondWith(fetch(r).catch(() => caches.match('/offline')));
  }
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { title: 'iTiers', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'iTiers', {
    body: d.body || '', icon: '/static/pwa/icon-192.png', badge: '/static/pwa/icon-192.png',
    tag: d.tag || undefined, renotify: !!d.tag, data: { url: d.url || '/guardia' },
    requireInteraction: !!d.urgente
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/guardia';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    for (const c of cs) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
"""

OFFLINE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#4F46E5"><title>Sin conexión · iTiers</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#F4F6FB;color:#0F172A;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;text-align:center;padding:24px}
.c{max-width:340px}.i{width:72px;height:72px;border-radius:18px;background:linear-gradient(135deg,#4F46E5,#0EA5E9);margin:0 auto 18px}
h1{font-size:1.4rem;margin:0 0 8px}p{color:#52607A;margin:0 0 20px}
button{font:inherit;font-weight:700;border:0;border-radius:12px;padding:.8rem 1.4rem;background:#4F46E5;color:#fff}</style></head>
<body><div class="c"><div class="i"></div><h1>Sin conexión</h1><p>No hay señal. Cuando vuelva, tocá reintentar: los avisos de guardia te siguen llegando.</p>
<button onclick="location.reload()">Reintentar</button></div></body></html>"""


@router.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    return JSONResponse(MANIFEST, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/sw.js")
def sw() -> Response:
    return Response(SW, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/offline", response_class=HTMLResponse)
def offline() -> HTMLResponse:
    return HTMLResponse(OFFLINE, headers={"Cache-Control": "no-cache"})
