import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { APP_NAME, APP_TAGLINE } from './src/constants/branding'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons/apple-touch-icon.png'],
      manifest: {
        name: APP_NAME,
        short_name: APP_NAME,
        description: APP_TAGLINE,
        theme_color: '#f7f7f2',
        background_color: '#f7f7f2',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
        // Android share sheet → new note (iOS Safari ignores share_target).
        share_target: {
          action: '/share',
          method: 'GET',
          params: { title: 'title', text: 'text', url: 'url' },
        },
      },
      workbox: {
        // SPA fallback for client-side routes; never for API/media/docs or
        // real downloadable files (e.g. the signed iOS shortcut — without
        // this the SW hands back index.html and iOS sees HTML, not the file).
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/media\//,
          /^\/docs/,
          /^\/openapi/,
          /\.shortcut$/,
        ],
        runtimeCaching: [
          {
            // Note attachments — immutable content-addressed files.
            urlPattern: /\/media\/.+/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'media',
              expiration: { maxEntries: 400, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            // API GETs — try network, fall back to cache when offline so
            // folders and recent notes still open without a connection.
            urlPattern: /\/api\/v1\/.+/i,
            method: 'GET',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 7 },
            },
          },
        ],
      },
    }),
  ],
  // Pinned port so this app never collides with other local projects.
  // strictPort fails loudly instead of silently drifting to another port.
  server: {
    port: 5175,
    strictPort: true,
    // Bind 0.0.0.0 so the app is reachable from other devices on the LAN.
    host: true,
    // Proxy API calls to the always-on compose stack (nginx on :8200), so
    // `npm run dev` needs no locally-running backend. Point at :8000 instead
    // if you're running uvicorn outside Docker.
    proxy: {
      '/api': { target: 'http://localhost:8200', changeOrigin: true },
      '/media': { target: 'http://localhost:8200', changeOrigin: true },
    },
  },
})
