/**
 * Sync engine — pushes offline changes to the server when it's reachable.
 *
 * Conflict policy (matches the app's philosophy): last writer wins, and
 * the edit history preserves whatever was overwritten. If a queued edit
 * hits a 409 (someone else saved while we were offline), we fetch the
 * current version and re-apply the offline content on top — the other
 * person's session stays in history, redline-diffable and restorable.
 */
import { ApiError, OfflineError, api } from './api'
import {
  cacheNote,
  getCachedNote,
  listPending,
  pendingCount,
  removeCachedNote,
  removePending,
} from './offline'

let syncing = false

/** Push the pending queue. Returns true if anything was applied. */
export async function syncPending(): Promise<boolean> {
  if (syncing) return false
  syncing = true
  let applied = false
  try {
    const ops = await listPending()
    for (const op of ops) {
      const note = await getCachedNote(op.noteId)
      if (!note) {
        await removePending(op.seq!)
        continue
      }
      // Locked notes travel as ciphertext; the server never sees plaintext.
      const content = note.locked
        ? { title: note.title, cipher_body: note.cipher_body ?? undefined }
        : { title: note.title, body: note.body, body_text: note.body_text }

      if (op.type === 'create') {
        // Create on the server (it assigns the real id), then push content.
        const created = await api.createNote(note.folder_id)
        const updated = await api.updateNote(created.id, {
          base_version: created.version,
          ...content,
        })
        await removeCachedNote(op.noteId) // retire the local- id
        await cacheNote(updated)
      } else {
        let updated
        try {
          updated = await api.updateNote(op.noteId, {
            base_version: op.baseVersion ?? note.version,
            ...content,
          })
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) {
            // Rebase: someone saved meanwhile. Their session is already in
            // history; our offline content becomes the newest save.
            const fresh = await api.getNote(op.noteId)
            updated = await api.updateNote(op.noteId, {
              base_version: fresh.version,
              ...content,
            })
          } else if (err instanceof ApiError && err.status === 404) {
            // Note was deleted/unshared while we were offline — drop the op
            // (the content survives in the owner's trash/history).
            await removePending(op.seq!)
            await removeCachedNote(op.noteId)
            continue
          } else {
            throw err
          }
        }
        await cacheNote(updated)
      }
      await removePending(op.seq!)
      applied = true
    }
  } catch (err) {
    // Server still unreachable — keep the queue and try again later.
    if (!(err instanceof OfflineError)) throw err
  } finally {
    syncing = false
  }
  return applied
}

/** Run sync whenever connectivity might be back; `onSynced` refreshes the UI.
 *
 * Two triggers: the browser's 'online' event (network came back), and a
 * 30s retry while anything is queued — the browser never fires an event
 * for "the server came back up", so we have to knock. */
export function registerSyncTriggers(onSynced: () => void): () => void {
  const run = () => {
    void syncPending().then((applied) => {
      if (applied) onSynced()
    })
  }
  window.addEventListener('online', run)
  const timer = setInterval(() => {
    void pendingCount().then((n) => {
      if (n > 0) run()
    })
  }, 30_000)
  return () => {
    window.removeEventListener('online', run)
    clearInterval(timer)
  }
}
