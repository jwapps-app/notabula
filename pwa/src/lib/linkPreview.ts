/** Session-lived cache of link-unfurl results, so a note's preview cards
 * render instantly on re-open and each URL is fetched at most once. */
import { api } from './api'
import type { LinkPreviewOut } from './api'

// undefined = never fetched; null = fetched but nothing worth showing.
const cache = new Map<string, LinkPreviewOut | null>()
const pending = new Set<string>()

// Off while a LOCKED note is open. Its body is encrypted precisely so the
// server never sees it — asking the server to unfurl every URL inside it
// would hand over those URLs (which can carry document ids or bearer
// query strings) and persist them in the shared preview cache.
let enabled = true
export function setPreviewsEnabled(on: boolean): void {
  enabled = on
}

export function getPreview(url: string): LinkPreviewOut | null | undefined {
  return cache.get(url)
}

/** Kick off a fetch if needed; `onReady` fires once the result is cached
 * (so the editor can re-render its decorations). No-op if already loading. */
export function loadPreview(url: string, onReady: () => void): void {
  if (!enabled || cache.has(url) || pending.has(url)) return
  pending.add(url)
  api
    .linkPreview(url)
    .then((p) => {
      const usable = p.ok && (p.title || p.description || p.image_url) ? p : null
      cache.set(url, usable)
    })
    .catch(() => {
      // Offline or server error — remember the miss for this session so we
      // don't hammer it, but a reload will retry.
      cache.set(url, null)
    })
    .finally(() => {
      pending.delete(url)
      onReady()
    })
}
