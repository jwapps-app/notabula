/** Session-lived cache of link-unfurl results, so a note's preview cards
 * render instantly on re-open and each URL is fetched at most once. */
import { api } from './api'
import type { LinkPreviewOut } from './api'

// undefined = never fetched; null = fetched but nothing worth showing.
const cache = new Map<string, LinkPreviewOut | null>()
const pending = new Set<string>()

export function getPreview(url: string): LinkPreviewOut | null | undefined {
  return cache.get(url)
}

/** Kick off a fetch if needed; `onReady` fires once the result is cached
 * (so the editor can re-render its decorations). No-op if already loading. */
export function loadPreview(url: string, onReady: () => void): void {
  if (cache.has(url) || pending.has(url)) return
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
