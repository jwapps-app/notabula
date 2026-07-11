import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { APP_NAME, APP_TAGLINE } from './src/constants/branding'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      // Custom worker (src/sw.ts): same precache + runtime caching as the
      // generated one, plus Web Push handlers a generated worker can't have.
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,svg,png,webmanifest}'],
      },
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
