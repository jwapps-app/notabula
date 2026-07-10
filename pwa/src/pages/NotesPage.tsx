import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Sidebar, { SMART_VIEWS } from '../components/Sidebar'
import type { FolderSelection } from '../components/Sidebar'
import NoteList from '../components/NoteList'
import NoteEditor from '../components/NoteEditor'
import ShareDialog from '../components/ShareDialog'
import type { ShareTarget } from '../components/ShareDialog'
import { OfflineError, api } from '../lib/api'
import type { FolderOut, NoteListItem, NoteOut, SharedFolder, TagOut } from '../lib/api'
import {
  LOCAL_ID_PREFIX,
  cacheFolders,
  cacheNote,
  getCachedFolders,
  getCachedNote,
  getCachedNotes,
  hydrateNotes,
  queueCreate,
} from '../lib/offline'
import {
  decryptBody,
  getSessionPassphrase,
  setSessionPassphrase,
} from '../lib/noteCrypto'
import { registerSyncTriggers, syncPending } from '../lib/sync'

/** Decrypt a locked note for viewing, prompting for the passphrase as
 * needed. Returns null if the user gives up. */
async function unlockForViewing(note: NoteOut): Promise<NoteOut | null> {
  if (!note.cipher_body) return note
  let pass = getSessionPassphrase()
  for (;;) {
    if (!pass) {
      pass = window.prompt('This note is locked. Enter your account password:')
      if (!pass) return null
    }
    try {
      const body = await decryptBody(note.cipher_body, pass)
      setSessionPassphrase(pass)
      return { ...note, body }
    } catch {
      window.alert(
        'Wrong password. (Notes locked before a password change need the old password.)',
      )
      pass = null
    }
  }
}

type MobilePane = 'folders' | 'list' | 'editor'

/** First image URL in a cached ProseMirror body — the offline stand-in for
 * the server's notes.thumb column. */
function firstImageSrc(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const node = body as { type?: string; attrs?: { src?: string }; content?: unknown[] }
  if (node.type === 'image' && typeof node.attrs?.src === 'string' && node.attrs.src) {
    return node.attrs.src
  }
  for (const child of node.content ?? []) {
    const found = firstImageSrc(child)
    if (found) return found
  }
  return null
}

/** Shape a cached full note into a list row (mirrors the server's preview). */
function toListItem(note: NoteOut): NoteListItem {
  let text = note.body_text || ''
  if (note.title && text.startsWith(note.title)) text = text.slice(note.title.length)
  return {
    id: note.id,
    folder_id: note.folder_id,
    title: note.title,
    preview: note.locked ? 'Locked' : text.trim().replace(/\n/g, ' ').slice(0, 120),
    thumb: note.locked ? null : firstImageSrc(note.body),
    pinned: note.pinned,
    locked: note.locked,
    version: note.version,
    created_at: note.created_at,
    updated_at: note.updated_at,
    deleted_at: note.deleted_at,
    role: note.role,
    owner_name: note.owner_name ?? null,
  }
}

/** Rebuild the owner's tag list from cached note text — the offline
 * stand-in for GET /tags. */
function deriveTagsFromCache(notes: NoteOut[]): TagOut[] {
  const counts = new Map<string, number>()
  for (const n of notes) {
    if (n.role !== 'owner') continue
    const seen = new Set<string>()
    for (const m of (n.body_text || '').matchAll(/#([\w-]*[^\W\d_][\w-]*)/g)) {
      seen.add(m[1].toLowerCase())
    }
    for (const t of seen) counts.set(t, (counts.get(t) ?? 0) + 1)
  }
  return [...counts.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, note_count]) => ({ id: name, name, note_count }))
}

const NEEDS_CONNECTION =
  'This needs a connection to the server. Your note edits still save on this device.'

export default function NotesPage() {
  const [folders, setFolders] = useState<FolderOut[]>([])
  const [tags, setTags] = useState<TagOut[]>([])
  const [sharedFolders, setSharedFolders] = useState<SharedFolder[]>([])
  const [hasSharedNotes, setHasSharedNotes] = useState(false)
  const [selection, setSelection] = useState<FolderSelection | null>(null)
  const [notes, setNotes] = useState<NoteListItem[]>([])
  const [openNote, setOpenNote] = useState<NoteOut | null>(null)
  const [mobilePane, setMobilePane] = useState<MobilePane>('list')
  const [query, setQuery] = useState('')
  const [shareTarget, setShareTarget] = useState<ShareTarget | null>(null)
  const [offline, setOffline] = useState(false)
  const [sortBy, setSortBy] = useState<'updated' | 'created' | 'title'>(
    () => (localStorage.getItem('noteSort') as 'updated' | 'created' | 'title') || 'updated',
  )
  const [viewMode, setViewMode] = useState<'list' | 'gallery'>(
    () => (localStorage.getItem('noteView') as 'list' | 'gallery') || 'list',
  )
  const selectionRef = useRef<FolderSelection | null>(null)
  selectionRef.current = selection

  const isDeletedView = selection?.kind === 'deleted'
  const searching = query.trim().length > 0

  // Everything in the sidebar goes stale on the same events, so it all
  // refreshes together. Server unreachable → serve the offline cache.
  const refreshFolders = useCallback(async (): Promise<FolderOut[]> => {
    try {
      const [result, tagResult, shared, sharedNotes] = await Promise.all([
        api.listFolders(),
        api.listTags(),
        api.sharedFolders(),
        api.listNotes({ shared: true }),
      ])
      setOffline(false)
      setFolders(result)
      setTags(tagResult)
      setSharedFolders(shared)
      const folderIds = new Set(shared.map((f) => f.id))
      setHasSharedNotes(sharedNotes.some((n) => !folderIds.has(n.folder_id)))
      void cacheFolders(result)
      return result
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      setOffline(true)
      const cachedFolders = await getCachedFolders()
      const cachedNotes = await getCachedNotes()
      setFolders(cachedFolders)
      setTags(deriveTagsFromCache(cachedNotes))
      setSharedFolders([]) // folder shares need the server's owner names
      setHasSharedNotes(cachedNotes.some((n) => n.role !== 'owner'))
      return cachedFolders
    }
  }, [])

  // Lighter than refreshFolders: only the tag list changes when you type
  // #hashtags, so a content save refreshes tags alone (not folders, shares,
  // and the shared-notes probe).
  const refreshTags = useCallback(async () => {
    try {
      setTags(await api.listTags())
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      setTags(deriveTagsFromCache(await getCachedNotes()))
    }
  }, [])

  const refreshNotes = useCallback(async (sel: FolderSelection | null) => {
    if (!sel) return
    try {
      const items = await api.listNotes(
        sel.kind === 'deleted'
          ? { deleted: true }
          : sel.kind === 'tag'
            ? { tag: sel.name }
            : sel.kind === 'all'
              ? {} // every non-deleted note, across folders
              : sel.kind === 'view'
                ? { view: sel.view }
                : sel.kind === 'shared'
                  ? { shared: true }
                  : { folderId: sel.id }, // own or shared folder — same endpoint
      )
      setOffline(false)
      setNotes(items)
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      setOffline(true)
      const cached = await getCachedNotes()
      const filtered = cached.filter((n) => {
        switch (sel.kind) {
          case 'all':
            return n.role === 'owner'
          case 'view': {
            if (n.role !== 'owner') return false
            const body = JSON.stringify(n.body ?? '')
            switch (sel.view) {
              case 'media':
                return body.includes('"type":"image"')
              case 'links':
                return /https?:\/\//.test(n.body_text || '')
              case 'tasks':
                return body.includes('"checked":false')
              case 'locked':
                return n.locked
              case 'recent':
                return (
                  Date.now() - new Date(n.updated_at).getTime() <
                  7 * 24 * 60 * 60 * 1000
                )
            }
            return false
          }
          case 'folder':
            return n.folder_id === sel.id && n.role === 'owner'
          case 'sharedFolder':
            return n.folder_id === sel.id && n.role !== 'owner'
          case 'shared':
            return n.role !== 'owner'
          case 'tag':
            return (
              n.role === 'owner' &&
              new RegExp(`#${sel.name}\\b`, 'i').test(n.body_text || '')
            )
          case 'deleted':
            return false // trash isn't cached for offline use
        }
      })
      setNotes(filtered.map(toListItem))
    }
  }, [])

  // After any note mutation: re-run the active search, else reload the list.
  const refreshList = useCallback(async () => {
    const q = query.trim()
    if (!q) {
      await refreshNotes(selectionRef.current)
      return
    }
    try {
      setNotes(await api.search(q))
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      setOffline(true)
      const cached = await getCachedNotes()
      const needle = q.toLowerCase()
      setNotes(
        cached
          .filter(
            (n) =>
              n.title.toLowerCase().includes(needle) ||
              (n.body_text || '').toLowerCase().includes(needle),
          )
          .map(toListItem),
      )
    }
  }, [query, refreshNotes])

  // Pull the full offline cache up to date. Folders are cached by
  // refreshFolders (always called just before hydrate), so this only needs
  // the full note bodies.
  const hydrate = useCallback(async () => {
    try {
      await hydrateNotes(await api.syncNotes())
    } catch {
      // offline — the existing cache stands
    }
  }, [])

  // Initial load: push any offline edits, then land on All Notes.
  useEffect(() => {
    void (async () => {
      await syncPending().catch(() => {})
      await refreshFolders()
      setSelection({ kind: 'all' })
      void hydrate()
    })()
  }, [refreshFolders, hydrate])

  // Keyboard shortcuts: n = new note, / = focus search. Only when not
  // already typing somewhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const target = e.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      )
        return
      if (e.key === 'n') {
        e.preventDefault()
        void handleNewNote()
      } else if (e.key === '/') {
        e.preventDefault()
        document.querySelector<HTMLInputElement>('.search-box input')?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, folders])

  // When connectivity returns: push the queue, refresh everything.
  useEffect(() => {
    return registerSyncTriggers(() => {
      void refreshFolders()
      void refreshNotes(selectionRef.current)
      void hydrate()
    })
  }, [refreshFolders, refreshNotes, hydrate])

  // Coming back to the app (PWA switched in on iOS): re-read the list —
  // from cache when offline — so it never shows a stale snapshot.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshList()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [refreshList])

  useEffect(() => {
    setOpenNote(null)
    setQuery('')
    void refreshNotes(selection)
  }, [selection, refreshNotes])

  // Debounced search; clearing the query falls back to the current view.
  useEffect(() => {
    if (!searching) return
    const timer = setTimeout(() => void refreshList(), 250)
    return () => clearTimeout(timer)
  }, [query, searching, refreshList])

  useEffect(() => {
    if (!searching) void refreshNotes(selectionRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searching])

  const listTitle = useMemo(() => {
    if (!selection) return ''
    if (selection.kind === 'all') return 'All Notes'
    if (selection.kind === 'view')
      return SMART_VIEWS.find((v) => v.view === selection.view)?.label ?? ''
    if (selection.kind === 'deleted') return 'Recently Deleted'
    if (selection.kind === 'tag') return `#${selection.name}`
    if (selection.kind === 'shared') return 'Shared Notes'
    if (selection.kind === 'sharedFolder')
      return sharedFolders.find((f) => f.id === selection.id)?.name ?? ''
    return folders.find((f) => f.id === selection.id)?.name ?? ''
  }, [selection, folders, sharedFolders])

  /** Online-only actions fail loudly offline instead of queueing. */
  async function onlineOnly(action: () => Promise<void>) {
    try {
      await action()
    } catch (err) {
      if (err instanceof OfflineError) window.alert(NEEDS_CONNECTION)
      else throw err
    }
  }

  // --- Folder actions ----------------------------------------------------

  async function handleNewFolder() {
    const name = window.prompt('New folder name')?.trim()
    if (!name) return
    await onlineOnly(async () => {
      const folder = await api.createFolder(name)
      await refreshFolders()
      setSelection({ kind: 'folder', id: folder.id })
      setMobilePane('list')
    })
  }

  async function handleRenameTag(tag: TagOut) {
    const name = window
      .prompt(
        `Rename #${tag.name} in every note that uses it (${tag.note_count})?\n\nNew tag name:`,
        tag.name,
      )
      ?.trim()
      .replace(/^#/, '')
    if (!name || name.toLowerCase() === tag.name) return
    await onlineOnly(async () => {
      const result = await api.renameTag(tag.name, name)
      await Promise.all([refreshFolders(), refreshList()])
      if (selection?.kind === 'tag' && selection.name === tag.name) {
        setSelection({ kind: 'tag', name: name.toLowerCase() })
      }
      // The open note may have been rewritten — show the renamed hashtag.
      if (openNote && !openNote.locked && !openNote.id.startsWith(LOCAL_ID_PREFIX)) {
        setOpenNote(await api.getNote(openNote.id))
      }
      window.alert(`Renamed in ${result.updated} note(s).`)
    })
  }

  async function handleNewSubfolder(parent: FolderOut) {
    const name = window.prompt(`New folder inside “${parent.name}”`)?.trim()
    if (!name) return
    await onlineOnly(async () => {
      const folder = await api.createFolder(name, parent.id)
      await refreshFolders()
      setSelection({ kind: 'folder', id: folder.id })
      setMobilePane('list')
    })
  }

  async function handleBulkMove(ids: string[], folderId: string) {
    await onlineOnly(async () => {
      const byId = new Map(notes.map((n) => [n.id, n]))
      for (const id of ids) {
        const item = byId.get(id)
        if (item) {
          await api.updateNote(id, { base_version: item.version, folder_id: folderId })
        }
      }
      await Promise.all([refreshList(), refreshFolders()])
    })
  }

  /** Append #tag as a final line of each selected note — the server then
   * derives the tag from the text on save, same as typing it. */
  async function handleBulkTag(ids: string[]) {
    const name = window
      .prompt(`Add a tag to ${ids.length} note${ids.length === 1 ? '' : 's'}:`)
      ?.trim()
      .replace(/^#/, '')
    if (!name) return
    if (!/^[\w-]*[^\W\d_][\w-]*$/.test(name)) {
      window.alert('Tags may use letters, numbers, _ and - (at least one letter).')
      return
    }
    await onlineOnly(async () => {
      // Whole-tag, case-insensitive: notes already carrying it are skipped.
      const already = new RegExp(`#${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`, 'i')
      let updated = 0
      for (const id of ids) {
        const note = await api.getNote(id)
        // Locked bodies are ciphertext; trashed notes stay untouched.
        if (note.locked || note.deleted_at || already.test(note.body_text ?? '')) continue
        const doc = (note.body as { type?: string; content?: unknown[] }) ?? {}
        const content = Array.isArray(doc.content) ? [...doc.content] : []
        content.push({
          type: 'paragraph',
          content: [{ type: 'text', text: `#${name}` }],
        })
        await api.updateNote(id, {
          base_version: note.version,
          body: { type: 'doc', ...doc, content },
          body_text: `${note.body_text ?? ''}\n#${name}`.trimStart(),
        })
        updated++
      }
      if (openNote && ids.includes(openNote.id)) {
        setOpenNote(await api.getNote(openNote.id))
      }
      await Promise.all([refreshList(), refreshFolders()])
      window.alert(`Added #${name.toLowerCase()} to ${updated} note(s).`)
    })
  }

  async function handleBulkDelete(ids: string[]) {
    if (!window.confirm(`Delete ${ids.length} note${ids.length === 1 ? '' : 's'}? They move to Recently Deleted for 30 days.`))
      return
    await onlineOnly(async () => {
      for (const id of ids) {
        await api.deleteNote(id, false)
      }
      if (openNote && ids.includes(openNote.id)) setOpenNote(null)
      await Promise.all([refreshList(), refreshFolders()])
    })
  }

  async function handleRenameFolder(folder: FolderOut) {
    const name = window.prompt('Rename folder', folder.name)?.trim()
    if (!name || name === folder.name) return
    await onlineOnly(async () => {
      await api.renameFolder(folder.id, name)
      await refreshFolders()
    })
  }

  async function handleDeleteFolder(folder: FolderOut) {
    if (
      !window.confirm(
        `Delete the folder “${folder.name}”? Its notes are kept — they move to your Notes folder.`,
      )
    )
      return
    await onlineOnly(async () => {
      await api.deleteFolder(folder.id)
      const result = await refreshFolders()
      if (selection?.kind === 'folder' && selection.id === folder.id) {
        const def = result.find((f) => f.is_default)
        if (def) setSelection({ kind: 'folder', id: def.id })
      }
    })
  }

  // --- Note actions -------------------------------------------------------

  async function handleNewNote() {
    // Current folder if we're in one; otherwise the default folder (iOS-style).
    const folderId =
      selection?.kind === 'folder'
        ? selection.id
        : folders.find((f) => f.is_default)?.id
    if (!folderId) return
    try {
      const note = await api.createNote(folderId)
      void cacheNote(note)
      await Promise.all([refreshNotes(selection), refreshFolders()])
      setOpenNote(note)
      setMobilePane('editor')
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      // Offline: the note is born locally and syncs when the server returns.
      setOffline(true)
      const now = new Date().toISOString()
      const local: NoteOut = {
        id: `${LOCAL_ID_PREFIX}${crypto.randomUUID()}`,
        folder_id: folderId,
        title: '',
        body: null,
        body_text: '',
        pinned: false,
        locked: false,
        cipher_body: null,
        version: 0,
        created_at: now,
        updated_at: now,
        deleted_at: null,
        role: 'owner',
        owner_name: null,
      }
      await cacheNote(local)
      await queueCreate(local.id)
      await refreshNotes(selection)
      setOpenNote(local)
      setMobilePane('editor')
    }
  }

  async function handleSelectNote(id: string) {
    let note: NoteOut
    try {
      note = await api.getNote(id)
      void cacheNote(note) // cache the ciphertext form, never the decrypted one
    } catch (err) {
      if (!(err instanceof OfflineError)) throw err
      setOffline(true)
      const cached = await getCachedNote(id)
      if (!cached) {
        window.alert('This note is not available offline.')
        return
      }
      note = cached
    }
    // Locked notes open VEILED (title + lock screen); content appears only
    // after a deliberate "View Note" — like iOS.
    setOpenNote(note)
    setMobilePane('editor')
  }

  /** "View Note" on the lock screen: decrypt into the open editor. */
  async function handleUnlockView() {
    if (!openNote?.locked) return
    const unlocked = await unlockForViewing(openNote)
    if (unlocked) setOpenNote(unlocked)
  }

  /** "Lock now": drop the decrypted content from memory — veil returns. */
  function handleLockView() {
    setOpenNote((cur) => (cur ? { ...cur, body: null } : cur))
  }

  async function handleTogglePin(item: NoteListItem) {
    await onlineOnly(async () => {
      await api.updateNote(item.id, {
        base_version: item.version,
        pinned: !item.pinned,
      })
      await refreshList()
      if (openNote?.id === item.id) setOpenNote(await api.getNote(item.id))
    })
  }

  async function handleDeleteNote(item: Pick<NoteListItem, 'id' | 'title'>) {
    const name = item.title || 'this note'
    const message = isDeletedView
      ? `Permanently delete “${name}”? This cannot be undone.`
      : `Delete “${name}”? It moves to Recently Deleted for 30 days.`
    if (!window.confirm(message)) return
    await onlineOnly(async () => {
      await api.deleteNote(item.id, isDeletedView)
      if (openNote?.id === item.id) setOpenNote(null)
      await Promise.all([refreshList(), refreshFolders()])
    })
  }

  async function handleRestoreNote(item: NoteListItem) {
    await onlineOnly(async () => {
      await api.restoreNote(item.id)
      await Promise.all([refreshList(), refreshFolders()])
    })
  }

  const handleSaved = useCallback(
    (saved: NoteOut) => {
      // Keep the open note's version current and refresh the list preview.
      // Locked saves come back body-less (ciphertext only) — keep the
      // decrypted body we're editing in React state; cache stays cipher.
      setOpenNote((cur) =>
        cur && cur.id === saved.id
          ? { ...cur, ...saved, body: saved.locked ? cur.body : saved.body }
          : cur,
      )
      void cacheNote(saved)
      // Sidebar tags update live as you type #hashtags — but folder counts,
      // shares, and the shared-notes probe can't change from a content edit,
      // so refresh tags alone rather than the whole sidebar.
      void refreshList()
      void refreshTags()
    },
    [refreshList, refreshTags],
  )

  // Client-side sort (pinned always float); persisted preference.
  const sortedNotes = useMemo(() => {
    const compare = (a: NoteListItem, b: NoteListItem) => {
      const pin = Number(b.pinned) - Number(a.pinned)
      if (pin) return pin
      if (sortBy === 'title')
        return a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })
      if (sortBy === 'created') return b.created_at.localeCompare(a.created_at)
      return b.updated_at.localeCompare(a.updated_at)
    }
    return [...notes].sort(compare)
  }, [notes, sortBy])

  const changeSort = (s: 'updated' | 'created' | 'title') => {
    setSortBy(s)
    localStorage.setItem('noteSort', s)
  }

  const changeViewMode = (v: 'list' | 'gallery') => {
    setViewMode(v)
    localStorage.setItem('noteView', v)
  }

  // On phones, never strand the user on an empty editor pane.
  const effectivePane = mobilePane === 'editor' && !openNote ? 'list' : mobilePane

  return (
    <div
      className="shell"
      data-mobile-pane={effectivePane}
      data-view={viewMode}
      data-note-open={openNote ? 'true' : 'false'}
    >
      {offline && (
        <div className="offline-banner">
          Offline — reading from this device; edits will sync when the server
          is reachable
        </div>
      )}
      <Sidebar
        folders={folders}
        tags={tags}
        sharedFolders={sharedFolders}
        hasSharedNotes={hasSharedNotes}
        selection={selection ?? { kind: 'deleted' }}
        onSelect={(sel) => {
          setSelection(sel)
          setMobilePane('list')
        }}
        onNewFolder={() => void handleNewFolder()}
        onNewSubfolder={(f) => void handleNewSubfolder(f)}
        onRenameFolder={(f) => void handleRenameFolder(f)}
        onDeleteFolder={(f) => void handleDeleteFolder(f)}
        onShareFolder={(f) => setShareTarget({ type: 'folder', id: f.id, name: f.name })}
      />
      <NoteList
        title={listTitle}
        notes={sortedNotes}
        selectedId={openNote?.id ?? null}
        isDeletedView={isDeletedView}
        canCreate={selection?.kind === 'folder' || selection?.kind === 'all'}
        canBulk={
          !isDeletedView &&
          selection?.kind !== 'shared' &&
          selection?.kind !== 'sharedFolder'
        }
        folders={folders}
        sortBy={sortBy}
        onSortChange={changeSort}
        viewMode={viewMode}
        onViewModeChange={changeViewMode}
        onBulkMove={(ids, fid) => void handleBulkMove(ids, fid)}
        onBulkTag={(ids) => void handleBulkTag(ids)}
        onRenameTitle={
          selection?.kind === 'tag'
            ? () => {
                const tag = tags.find((t) => t.name === (selection as { name: string }).name)
                if (tag) void handleRenameTag(tag)
              }
            : undefined
        }
        onBulkDelete={(ids) => void handleBulkDelete(ids)}
        query={query}
        onQueryChange={setQuery}
        onSelect={(id) => void handleSelectNote(id)}
        onNewNote={() => void handleNewNote()}
        onTogglePin={(n) => void handleTogglePin(n)}
        onDelete={(n) => void handleDeleteNote(n)}
        onRestore={(n) => void handleRestoreNote(n)}
        onBack={() => setMobilePane('folders')}
      />
      {openNote ? (
        <NoteEditor
          key={openNote.id}
          note={openNote}
          readOnly={isDeletedView || openNote.role === 'viewer'}
          folders={folders}
          tagNames={tags.map((t) => t.name)}
          onSaved={handleSaved}
          onUnlockView={() => void handleUnlockView()}
          onLockView={handleLockView}
          onDelete={
            openNote.role === 'owner' &&
            !openNote.deleted_at &&
            !openNote.id.startsWith(LOCAL_ID_PREFIX)
              ? () => void handleDeleteNote(openNote)
              : undefined
          }
          onShare={
            openNote.role === 'owner' &&
            !openNote.locked &&
            !openNote.id.startsWith(LOCAL_ID_PREFIX)
              ? () =>
                  setShareTarget({
                    type: 'note',
                    id: openNote.id,
                    name: openNote.title || 'New Note',
                  })
              : undefined
          }
          onBack={() => {
            setMobilePane('list')
            // Re-read the list on return (cache when offline) so a note
            // created or edited in the editor is visible and freshly
            // sorted — never stale, whatever happened while typing.
            void refreshList()
          }}
        />
      ) : (
        <div className="pane pane-editor">
          <div className="empty-state">Select a note</div>
        </div>
      )}
      {shareTarget && (
        <ShareDialog
          target={shareTarget}
          onClose={() => {
            setShareTarget(null)
            void refreshFolders()
          }}
        />
      )}
    </div>
  )
}
