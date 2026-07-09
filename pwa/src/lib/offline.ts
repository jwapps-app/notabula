/**
 * Offline store — IndexedDB cache of everything the app needs to work
 * with the server unreachable, plus the queue of local changes waiting
 * to sync.
 *
 * Stores:
 *  - notes:   full NoteOut records keyed by id (incl. body, role, owner)
 *  - folders: the sidebar folder list (single record)
 *  - pending: queued mutations, in insertion order
 *
 * Offline scope (deliberate): reading everything, editing note content,
 * and creating notes. Deletes, moves, pins, folder ops, sharing, and
 * image uploads need a connection — they fail loudly instead of queueing.
 */
import { openDB } from 'idb'
import type { DBSchema, IDBPDatabase } from 'idb'
import type { FolderOut, NoteOut } from './api'

/** Local-only ids for notes created offline, until the server assigns one. */
export const LOCAL_ID_PREFIX = 'local-'

export interface PendingOp {
  seq?: number
  /** 'create' pushes a local- note to the server; 'edit' pushes content. */
  type: 'create' | 'edit'
  noteId: string
  /** For edits: the server version this offline edit was based on. */
  baseVersion?: number
}

interface OfflineSchema extends DBSchema {
  notes: { key: string; value: NoteOut }
  folders: { key: string; value: { key: string; folders: FolderOut[] } }
  pending: { key: number; value: PendingOp; indexes: { byNote: string } }
}

let dbPromise: Promise<IDBPDatabase<OfflineSchema>> | null = null

function db(): Promise<IDBPDatabase<OfflineSchema>> {
  if (!dbPromise) {
    dbPromise = openDB<OfflineSchema>('notes-offline', 1, {
      upgrade(database) {
        database.createObjectStore('notes', { keyPath: 'id' })
        database.createObjectStore('folders', { keyPath: 'key' })
        const pending = database.createObjectStore('pending', {
          keyPath: 'seq',
          autoIncrement: true,
        })
        pending.createIndex('byNote', 'noteId')
      },
    })
  }
  return dbPromise
}

// --- Notes cache ---------------------------------------------------------

export async function cacheNote(note: NoteOut): Promise<void> {
  await (await db()).put('notes', note)
}

export async function getCachedNote(id: string): Promise<NoteOut | undefined> {
  return (await db()).get('notes', id)
}

export async function getCachedNotes(): Promise<NoteOut[]> {
  return (await db()).getAll('notes')
}

export async function removeCachedNote(id: string): Promise<void> {
  await (await db()).delete('notes', id)
}

/** Replace the cache with fresh server state — EXCEPT notes that have
 * pending local changes (their offline content must not be clobbered). */
export async function hydrateNotes(fresh: NoteOut[]): Promise<void> {
  const d = await db()
  const pendingIds = new Set((await d.getAll('pending')).map((p) => p.noteId))
  const tx = d.transaction('notes', 'readwrite')
  const existing = await tx.store.getAllKeys()
  const freshIds = new Set(fresh.map((n) => n.id))
  for (const id of existing) {
    // Drop cached notes the server no longer reports (deleted/unshared),
    // but never local creations or notes with queued edits.
    if (!freshIds.has(id) && !id.startsWith(LOCAL_ID_PREFIX) && !pendingIds.has(id)) {
      await tx.store.delete(id)
    }
  }
  for (const note of fresh) {
    if (!pendingIds.has(note.id)) await tx.store.put(note)
  }
  await tx.done
}

// --- Folders cache ---------------------------------------------------------

export async function cacheFolders(folders: FolderOut[]): Promise<void> {
  await (await db()).put('folders', { key: 'all', folders })
}

export async function getCachedFolders(): Promise<FolderOut[]> {
  const record = await (await db()).get('folders', 'all')
  return record?.folders ?? []
}

// --- Pending queue -----------------------------------------------------------

export async function queueEdit(noteId: string, baseVersion: number): Promise<void> {
  const d = await db()
  // One pending edit per note — the cached note always holds the latest
  // content, so a second offline save just keeps the earliest baseVersion.
  const existing = await d.getAllFromIndex('pending', 'byNote', noteId)
  if (existing.some((op) => op.type === 'edit' || op.type === 'create')) return
  await d.add('pending', { type: 'edit', noteId, baseVersion })
}

export async function queueCreate(noteId: string): Promise<void> {
  await (await db()).add('pending', { type: 'create', noteId })
}

export async function listPending(): Promise<PendingOp[]> {
  return (await db()).getAll('pending')
}

export async function removePending(seq: number): Promise<void> {
  await (await db()).delete('pending', seq)
}

export async function pendingCount(): Promise<number> {
  return (await db()).count('pending')
}

/** Wipe everything — called on logout and on user switch so cached notes
 * never outlive the session on a shared device. */
export async function clearOfflineCache(): Promise<void> {
  const d = await db()
  const tx = d.transaction(['notes', 'folders', 'pending'], 'readwrite')
  await Promise.all([
    tx.objectStore('notes').clear(),
    tx.objectStore('folders').clear(),
    tx.objectStore('pending').clear(),
  ])
  await tx.done
}
