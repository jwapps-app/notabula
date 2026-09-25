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
import type { NoteOut } from './api'
import {
  LOCAL_ID_PREFIX,
  cacheNote,
  getCachedNote,
  listPending,
  pendingCount,
  queueCreate,
  removeCachedNote,
  removePending,
} from './offline'

let syncing = false

/** Did the person keep editing this note while its upload was on the wire?
 * The queue holds a reference to the mutable cached note, not a snapshot,
 * so "what we sent" and "what's cached now" can differ. */
async function editedSince(sent: NoteOut): Promise<NoteOut | null> {
  const now = await getCachedNote(sent.id)
  if (!now) return null
  return now.updated_at !== sent.updated_at ? now : null
}

/** The content of a note the server no longer accepts (deleted, unshared)
 * is unique if it was never uploaded. Rather than dropping it, it becomes a
 * brand-new offline note of the person's own — nothing typed is lost. */
async function salvageAsNewNote(note: NoteOut): Promise<void> {
  const hasContent = note.locked ? !!note.cipher_body : !!note.body_text.trim()
  await removeCachedNote(note.id)
  if (!hasContent) return
  const local: NoteOut = {
    ...note,
    id: `${LOCAL_ID_PREFIX}${crypto.randomUUID()}`,
    title: note.title || 'Recovered note',
    role: 'owner',
    owner_name: null,
    deleted_at: null,
    version: 0,
  }
  await cacheNote(local)
  await queueCreate(local.id)
}

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
            // Deleted or unshared while we were offline. Anything typed
            // here was never uploaded, so it can't be "in the trash" —
            // keep it as a new note of our own instead of dropping it.
            await removePending(op.seq!)
            await salvageAsNewNote(note)
            applied = true
            continue
          } else if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
            // The server has permanently refused this content (too large,
            // malformed, no longer permitted). Retrying forever would only
            // block every op queued behind it — drop the op but keep the
            // cached note, so the text is still on screen to rescue.
            console.warn(`sync: server rejected note ${op.noteId} (${err.status}); leaving it local`)
            await removePending(op.seq!)
            continue
          } else {
            throw err
          }
        }
        // Acknowledge only what was actually sent. If typing continued
        // during the request, the cache now holds newer text: keep it (with
        // the server's new version so the next push isn't a 409) and leave
        // the op queued for another round.
        const newer = await editedSince(note)
        if (newer) {
          await cacheNote({ ...newer, version: updated.version })
          applied = true
          continue
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
