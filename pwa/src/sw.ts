/// <reference lib="webworker" />
/**
 * Custom service worker (vite-plugin-pwa injectManifest mode).
 *
 * Reproduces everything the generated worker did — precache, SPA
 * navigation fallback, media/api runtime caches — and adds what a
 * generated worker can't: Web Push. `push` shows the notification the
 * server sent; `notificationclick` focuses the app (or opens it) on the
 * relevant note.
 */
import { clientsClaim } from 'workbox-core'
import { ExpirationPlugin } from 'workbox-expiration'
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { CacheFirst, NetworkFirst } from 'workbox-strategies'

declare let self: ServiceWorkerGlobalScope

// autoUpdate behavior: a new worker takes over immediately.
self.skipWaiting()
clientsClaim()

precacheAndRoute(self.__WB_MANIFEST)

// SPA fallback for client-side routes; never for API/media/docs or real
// downloadable files (e.g. the signed iOS shortcut).
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('/index.html'), {
    denylist: [/^\/api\//, /^\/media\//, /^\/docs/, /^\/openapi/, /\.shortcut$/],
  }),
)

// Note attachments — immutable content-addressed files.
registerRoute(
  ({ url }) => /\/media\/.+/i.test(url.pathname),
  new CacheFirst({
    cacheName: 'media',
    plugins: [
      new ExpirationPlugin({ maxEntries: 400, maxAgeSeconds: 60 * 60 * 24 * 30 }),
    ],
  }),
)

// API GETs — try network, fall back to cache when offline so folders and
// recent notes still open without a connection.
registerRoute(
  ({ url, request }) =>
    /\/api\/v1\/.+/i.test(url.pathname) && request.method === 'GET',
  new NetworkFirst({
    cacheName: 'api',
    networkTimeoutSeconds: 5,
    plugins: [
      new ExpirationPlugin({ maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 7 }),
    ],
  }),
)

// --- Web Push -------------------------------------------------------------

interface PushMessage {
  title?: string
  body?: string
  data?: { type?: string; note_id?: string }
}

self.addEventListener('push', (event) => {
  let msg: PushMessage = {}
  try {
    msg = event.data?.json() ?? {}
  } catch {
    msg = { body: event.data?.text() }
  }
  event.waitUntil(
    self.registration.showNotification(msg.title || 'Notabula', {
      body: msg.body || '',
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      tag: msg.data?.note_id || undefined, // updates collapse per note
      data: msg.data || {},
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const noteId = (event.notification.data as PushMessage['data'])?.note_id
  const target = noteId ? `/?note=${noteId}` : '/'
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      })
      const existing = wins.find((w): w is WindowClient => 'focus' in w)
      if (existing) {
        await existing.focus()
        if (noteId) existing.postMessage({ type: 'open-note', noteId })
        return
      }
      await self.clients.openWindow(target)
    })(),
  )
})
