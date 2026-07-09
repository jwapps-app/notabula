import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import type { Editor } from '@tiptap/react'
import type { EditorView } from '@tiptap/pm/view'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import TaskItem from '@tiptap/extension-task-item'
import TaskList from '@tiptap/extension-task-list'
import Placeholder from '@tiptap/extension-placeholder'
import Underline from '@tiptap/extension-underline'
import Highlight from '@tiptap/extension-highlight'
import { HashtagHighlight } from '../lib/hashtagHighlight'
import { Linkify } from '../lib/linkify'
import { ApiError, OfflineError, api } from '../lib/api'
import type { FolderOut, NoteOut, RevisionDetail } from '../lib/api'
import { LOCAL_ID_PREFIX, cacheNote, queueEdit } from '../lib/offline'
import {
  encryptBody,
  getSessionPassphrase,
  setSessionPassphrase,
} from '../lib/noteCrypto'
import HistoryDialog from './HistoryDialog'
import Icon from './Icon'

interface Props {
  note: NoteOut
  readOnly: boolean
  folders: FolderOut[]
  /** Existing tag names, for # autocomplete. */
  tagNames: string[]
  /** Called after every successful save so the list pane can refresh. */
  onSaved: (note: NoteOut) => void
  /** Lock-screen actions for locked notes. */
  onUnlockView?: () => void
  onLockView?: () => void
  /** Present when the current user may delete this note (owner, saved). */
  onDelete?: () => void
  /** Present only when the current user owns the note. */
  onShare?: () => void
  onBack: () => void
}

interface TagSuggestState {
  items: string[]
  query: string
  from: number
  left: number
  top: number
}

/** Upload image files and insert them at the current selection. */
async function uploadAndInsert(view: EditorView, files: File[]): Promise<void> {
  for (const file of files) {
    try {
      const uploaded = await api.uploadAttachment(file)
      const node = view.state.schema.nodes.image.create({ src: uploaded.url })
      view.dispatch(view.state.tr.replaceSelectionWith(node).scrollIntoView())
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Image upload failed')
    }
  }
}

function imageFiles(list: FileList | undefined | null): File[] {
  return Array.from(list ?? []).filter((f) => f.type.startsWith('image/'))
}

/** Reorder every checklist so unchecked items come first (iOS-style). */
function sortChecklists(editor: Editor) {
  const doc = editor.getJSON()
  let changed = false
  const walk = (node: { type?: string; content?: unknown[]; attrs?: unknown }) => {
    if (node.type === 'taskList' && Array.isArray(node.content)) {
      const items = node.content as { attrs?: { checked?: boolean } }[]
      const sorted = [...items].sort(
        (a, b) => Number(a.attrs?.checked === true) - Number(b.attrs?.checked === true),
      )
      if (JSON.stringify(sorted) !== JSON.stringify(items)) {
        node.content = sorted
        changed = true
      }
    }
    ;(node.content as { type?: string }[] | undefined)?.forEach(walk)
  }
  walk(doc)
  // emitUpdate=true → the reorder autosaves like any edit
  if (changed) editor.commands.setContent(doc, true)
}

const SAVE_DEBOUNCE_MS = 700

/** First line of the document = the note's title, like Apple Notes.
 * Skips non-text leading nodes (e.g. an image at the top of the note). */
function deriveTitle(editor: Editor): string {
  let title = ''
  editor.state.doc.forEach((node) => {
    if (!title) title = node.textContent.trim()
  })
  return title.slice(0, 400)
}

function ToolbarButton({
  editor,
  label,
  title,
  isActive,
  onRun,
}: {
  editor: Editor
  label: ReactNode
  title: string
  isActive: () => boolean
  onRun: () => void
}) {
  // Re-render on selection change is handled by the parent's editor state.
  void editor
  return (
    <button
      type="button"
      className={`tb-btn${isActive() ? ' active' : ''}`}
      title={title}
      onMouseDown={(e) => {
        e.preventDefault() // keep focus in the editor
        onRun()
      }}
    >
      {label}
    </button>
  )
}

export default function NoteEditor({
  note,
  readOnly,
  folders,
  tagNames,
  onSaved,
  onUnlockView,
  onLockView,
  onDelete,
  onShare,
  onBack,
}: Props) {
  const [status, setStatus] = useState<
    'idle' | 'saving' | 'saved' | 'conflict' | 'offline'
  >('idle')
  const [historyOpen, setHistoryOpen] = useState(false)
  // Notes created offline live under a local- id until the sync assigns one.
  const isLocal = note.id.startsWith(LOCAL_ID_PREFIX)
  // A locked note without decrypted content shows the lock screen instead
  // of the editor — content appears only after a deliberate "View Note".
  const isVeiled = note.locked && note.body == null
  const versionRef = useRef(note.version)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // Bump to force toolbar re-render on selection/content changes.
  const [, setTick] = useState(0)

  // --- Tag autocomplete: typing # suggests existing tags at the caret ---
  const [tagSuggest, setTagSuggest] = useState<TagSuggestState | null>(null)
  const tagNamesRef = useRef(tagNames)
  tagNamesRef.current = tagNames
  const tagSuggestRef = useRef<TagSuggestState | null>(null)
  tagSuggestRef.current = tagSuggest

  function computeTagSuggest(ed: Editor): TagSuggestState | null {
    const { state } = ed
    if (!state.selection.empty || !ed.isEditable) return null
    const $from = state.selection.$from
    if (!$from.parent.isTextblock) return null
    const textBefore = $from.parent.textBetween(0, $from.parentOffset, undefined, '￼')
    const m = /#([\w-]*)$/.exec(textBefore)
    if (!m) return null
    const query = m[1].toLowerCase()
    const items = tagNamesRef.current
      .filter((t) => t.toLowerCase().startsWith(query) && t.toLowerCase() !== query)
      .slice(0, 6)
    if (items.length === 0) return null
    const coords = ed.view.coordsAtPos(state.selection.from)
    return {
      items,
      query: m[1],
      from: state.selection.from - m[1].length,
      left: coords.left,
      top: coords.bottom + 4,
    }
  }

  const editor = useEditor({
    extensions: [
      StarterKit,
      Image,
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: 'Start writing…' }),
      Underline,
      Highlight,
      HashtagHighlight,
      Linkify,
    ],
    editorProps: {
      handleDOMEvents: {
        // Click an image → view it full size in a new tab.
        click: (_view, event) => {
          const img = (event.target as HTMLElement).closest?.('img')
          if (img?.getAttribute('src')?.startsWith('/media/')) {
            window.open(img.getAttribute('src')!, '_blank', 'noopener')
            return true
          }
          return false
        },
      },
      handleKeyDown: (_view, event) => {
        const suggest = tagSuggestRef.current
        if (!suggest) return false
        if (event.key === 'Escape') {
          setTagSuggest(null)
          return true
        }
        if (event.key === 'Tab') {
          applyTagSuggest(suggest.items[0])
          return true
        }
        return false
      },
      // Paste or drag an image straight into the note, like Apple Notes.
      handlePaste: (view, event) => {
        const files = imageFiles(event.clipboardData?.files)
        if (files.length === 0) return false
        event.preventDefault()
        void uploadAndInsert(view, files)
        return true
      },
      handleDrop: (view, event) => {
        const files = imageFiles(event.dataTransfer?.files)
        if (files.length === 0) return false
        event.preventDefault()
        void uploadAndInsert(view, files)
        return true
      },
    },
    editable: !readOnly,
    onSelectionUpdate: ({ editor }) => {
      setTick((t) => t + 1)
      setTagSuggest(computeTagSuggest(editor))
    },
    onTransaction: () => setTick((t) => t + 1),
    onBlur: () => {
      // Delay so a mousedown on a suggestion lands before the menu closes.
      setTimeout(() => setTagSuggest(null), 150)
    },
    onUpdate: ({ editor }) => {
      if (readOnly) return
      pendingRef.current = true
      setStatus('saving')
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => void save(editor), SAVE_DEBOUNCE_MS)
    },
  })

  /** Replace the partial #tag at the caret with the chosen tag name. */
  function applyTagSuggest(name: string) {
    const suggest = tagSuggestRef.current
    if (!editor || !suggest) return
    editor
      .chain()
      .focus()
      .insertContentAt(
        { from: suggest.from, to: suggest.from + suggest.query.length },
        `${name} `,
      )
      .run()
    setTagSuggest(null)
  }

  /** The wire form of the current content — ciphertext for locked notes. */
  async function outgoingContent(ed: Editor) {
    if (note.locked) {
      const pass = getSessionPassphrase()
      if (!pass) throw new Error('missing-passphrase')
      return {
        title: deriveTitle(ed),
        cipher_body: await encryptBody(ed.getJSON(), pass),
      }
    }
    return {
      title: deriveTitle(ed),
      body: ed.getJSON(),
      body_text: ed.getText({ blockSeparator: '\n' }),
    }
  }

  /** Persist the current content to the device and queue it for sync. */
  async function saveOffline(ed: Editor) {
    const content = await outgoingContent(ed)
    const local: NoteOut = {
      ...note,
      title: content.title,
      body: 'body' in content ? content.body : null,
      body_text: 'body_text' in content ? content.body_text! : '',
      cipher_body: 'cipher_body' in content ? content.cipher_body! : null,
      updated_at: new Date().toISOString(),
      version: versionRef.current,
    }
    await cacheNote(local)
    // Local creations already have a queued create op carrying the content.
    if (!isLocal) await queueEdit(note.id, versionRef.current)
    setStatus('offline')
    // Hand back the decrypted body for React state so editing continues.
    onSaved(note.locked ? { ...local, body: ed.getJSON() } : local)
  }

  async function save(ed: Editor) {
    if (!pendingRef.current) return
    pendingRef.current = false
    if (isLocal) {
      // Born offline — stays device-local until the sync engine creates it.
      await saveOffline(ed)
      return
    }
    try {
      const updated = await api.updateNote(note.id, {
        base_version: versionRef.current,
        ...(await outgoingContent(ed)),
      })
      versionRef.current = updated.version
      setStatus('saved')
      onSaved(updated)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Edited elsewhere — reload the authoritative copy rather than clobber.
        setStatus('conflict')
        const fresh = await api.getNote(note.id)
        versionRef.current = fresh.version
        if (!fresh.locked) {
          ed.commands.setContent((fresh.body as object) ?? '', false)
        }
        onSaved(fresh)
        setStatus('saved')
      } else if (err instanceof OfflineError) {
        // Server unreachable — the note is safe on this device.
        await saveOffline(ed)
      } else {
        setStatus('idle')
      }
    }
  }

  /** Lock this note: encrypted with your ACCOUNT password, client-side —
   * the server only ever stores ciphertext. */
  async function lockNote() {
    if (!editor) return
    let pass = getSessionPassphrase()
    if (!pass) {
      pass = window.prompt(
        'Enter your account password to lock this note.\n\n' +
          'The note is encrypted with it on this device — the server ' +
          'cannot read it. (If your password is ever reset by an admin, ' +
          'locked notes need the old password.) The title stays visible.',
      )
      if (!pass) return
      try {
        await api.verifyPassword(pass)
      } catch {
        window.alert('That does not match your account password — the note was not locked.')
        return
      }
    }
    try {
      const cipher = await encryptBody(editor.getJSON(), pass)
      const updated = await api.updateNote(note.id, {
        base_version: versionRef.current,
        locked: true,
        cipher_body: cipher,
        title: deriveTitle(editor),
      })
      setSessionPassphrase(pass)
      versionRef.current = updated.version
      onSaved(updated)
      setStatus('saved')
    } catch (err) {
      window.alert(
        err instanceof OfflineError
          ? 'Locking needs a connection to the server.'
          : err instanceof Error
            ? err.message
            : 'Could not lock the note',
      )
    }
  }

  /** Remove the lock: the decrypted content goes back to the server. */
  async function unlockNote() {
    if (!editor) return
    if (
      !window.confirm(
        'Remove the lock? The note becomes a normal note again — searchable and shareable.',
      )
    )
      return
    try {
      const updated = await api.updateNote(note.id, {
        base_version: versionRef.current,
        locked: false,
        title: deriveTitle(editor),
        body: editor.getJSON(),
        body_text: editor.getText({ blockSeparator: '\n' }),
      })
      versionRef.current = updated.version
      onSaved(updated)
      setStatus('saved')
    } catch (err) {
      window.alert(
        err instanceof OfflineError
          ? 'Unlocking needs a connection to the server.'
          : err instanceof Error
            ? err.message
            : 'Could not unlock the note',
      )
    }
  }

  async function moveToFolder(folderId: string) {
    try {
      const updated = await api.updateNote(note.id, {
        base_version: versionRef.current,
        folder_id: folderId,
      })
      versionRef.current = updated.version
      onSaved(updated)
    } catch (err) {
      window.alert(
        err instanceof OfflineError
          ? 'Moving notes needs a connection to the server.'
          : err instanceof Error
            ? err.message
            : 'Move failed',
      )
    }
  }

  // Load content when switching notes; flush any pending save for the old
  // one. Also re-runs when a locked note is unveiled ("View Note") or
  // veiled again ("Lock Now") — but NOT on ordinary body updates, so the
  // caret never jumps mid-typing.
  useEffect(() => {
    if (!editor) return
    versionRef.current = note.version
    if (timerRef.current) clearTimeout(timerRef.current)
    // emitUpdate=false: loading a note must not fire onUpdate and autosave.
    editor.commands.setContent(isVeiled ? '' : ((note.body as object) ?? ''), false)
    editor.setEditable(!readOnly && !isVeiled, false) // no phantom onUpdate
    pendingRef.current = false
    setTagSuggest(null)
    setStatus('idle')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.id, editor, readOnly, isVeiled])

  // The note changed OUTSIDE this editor (e.g. a tag rename rewrote it):
  // our own saves move versionRef forward before onSaved, so a version we
  // don't recognize means fresher content arrived — load it.
  useEffect(() => {
    if (!editor || isVeiled || note.version === versionRef.current) return
    versionRef.current = note.version
    if (timerRef.current) clearTimeout(timerRef.current)
    pendingRef.current = false
    editor.commands.setContent((note.body as object) ?? '', false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.version, editor])

  // Flush pending edits when the tab hides (PWA lifecycle).
  useEffect(() => {
    const flush = () => {
      if (editor && pendingRef.current) void save(editor)
    }
    window.addEventListener('visibilitychange', flush)
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('visibilitychange', flush)
      window.removeEventListener('pagehide', flush)
      flush()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, note.id])

  if (!editor) return <div className="pane pane-editor" />

  const dateLine = new Date(note.updated_at).toLocaleString(undefined, {
    dateStyle: 'long',
    timeStyle: 'short',
  })

  // Locked + not yet viewed → lock screen, like iOS. Only the title shows.
  if (isVeiled) {
    return (
      <div className="pane pane-editor">
        <div className="editor-toolbar">
          <button className="back-btn" onClick={onBack}>
            ‹ Notes
          </button>
        </div>
        <div className="lock-screen">
          <div className="lock-screen-glyph">
            <Icon name="lock" size={44} strokeWidth={1.5} />
          </div>
          <h2>{note.title || 'Locked note'}</h2>
          <p>This note is locked.</p>
          <button
            type="button"
            className="lock-screen-view"
            onClick={() => onUnlockView?.()}
          >
            View Note
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="pane pane-editor">
      <div className="editor-toolbar">
        <button
          className="back-btn"
          onClick={() =>
            void (async () => {
              // Flush the debounced save first so the list the user lands
              // on already contains this edit (cache included, offline).
              if (timerRef.current) clearTimeout(timerRef.current)
              if (pendingRef.current) await save(editor)
              onBack()
            })()
          }
        >
          ‹ Notes
        </button>
        {!readOnly && (
          <>
            <ToolbarButton
              editor={editor}
              label="B"
              title="Bold"
              isActive={() => editor.isActive('bold')}
              onRun={() => editor.chain().focus().toggleBold().run()}
            />
            <ToolbarButton
              editor={editor}
              label="I"
              title="Italic"
              isActive={() => editor.isActive('italic')}
              onRun={() => editor.chain().focus().toggleItalic().run()}
            />
            <ToolbarButton
              editor={editor}
              label="U"
              title="Underline"
              isActive={() => editor.isActive('underline')}
              onRun={() => editor.chain().focus().toggleUnderline().run()}
            />
            <ToolbarButton
              editor={editor}
              label="S"
              title="Strikethrough"
              isActive={() => editor.isActive('strike')}
              onRun={() => editor.chain().focus().toggleStrike().run()}
            />
            <ToolbarButton
              editor={editor}
              label={<Icon name="pen" size={16} />}
              title="Highlight"
              isActive={() => editor.isActive('highlight')}
              onRun={() => editor.chain().focus().toggleHighlight().run()}
            />
            <ToolbarButton
              editor={editor}
              label="H1"
              title="Heading"
              isActive={() => editor.isActive('heading', { level: 1 })}
              onRun={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
            />
            <ToolbarButton
              editor={editor}
              label="H2"
              title="Subheading"
              isActive={() => editor.isActive('heading', { level: 2 })}
              onRun={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
            />
            <ToolbarButton
              editor={editor}
              label="•"
              title="Bulleted list"
              isActive={() => editor.isActive('bulletList')}
              onRun={() => editor.chain().focus().toggleBulletList().run()}
            />
            <ToolbarButton
              editor={editor}
              label="1."
              title="Numbered list"
              isActive={() => editor.isActive('orderedList')}
              onRun={() => editor.chain().focus().toggleOrderedList().run()}
            />
            <ToolbarButton
              editor={editor}
              label={<Icon name="check-square" size={16} />}
              title="Checklist"
              isActive={() => editor.isActive('taskList')}
              onRun={() => editor.chain().focus().toggleTaskList().run()}
            />
            <ToolbarButton
              editor={editor}
              label="❝"
              title="Block quote"
              isActive={() => editor.isActive('blockquote')}
              onRun={() => editor.chain().focus().toggleBlockquote().run()}
            />
            <ToolbarButton
              editor={editor}
              label={<Icon name="checks-down" size={16} />}
              title="Move checked items to bottom"
              isActive={() => false}
              onRun={() => sortChecklists(editor)}
            />
            <button
              type="button"
              className="tb-btn"
              title="Insert photo"
              onMouseDown={(e) => {
                e.preventDefault()
                fileInputRef.current?.click()
              }}
            >
              <Icon name="image" size={16} />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(e) => {
                const files = imageFiles(e.target.files)
                if (files.length && editor) void uploadAndInsert(editor.view, files)
                e.target.value = ''
              }}
            />
          </>
        )}
        <span className="spacer" />
        <button
          type="button"
          className="tb-btn"
          title="Print note"
          onMouseDown={(e) => {
            e.preventDefault()
            window.print()
          }}
        >
          <Icon name="printer" size={16} />
        </button>
        {onDelete && (
          <button
            type="button"
            className="tb-btn"
            title="Delete note"
            onMouseDown={(e) => {
              e.preventDefault()
              onDelete()
            }}
          >
            <Icon name="trash" size={16} />
          </button>
        )}
        {!isLocal && !note.locked && (
          <button
            type="button"
            className="tb-btn"
            title="Edit history"
            onMouseDown={(e) => {
              e.preventDefault()
              setHistoryOpen(true)
            }}
          >
            <Icon name="clock" size={16} />
          </button>
        )}
        {note.role === 'owner' && !isLocal && !readOnly && !note.locked && (
          <button
            type="button"
            className="tb-btn"
            title="Lock this note (encrypted)"
            onMouseDown={(e) => {
              e.preventDefault()
              void lockNote()
            }}
          >
            <Icon name="unlock" size={16} />
          </button>
        )}
        {note.locked && (
          <button
            type="button"
            className="tb-btn active"
            title="Lock now — hide the content again"
            onMouseDown={(e) => {
              e.preventDefault()
              void (async () => {
                // Flush any in-flight edit before dropping the plaintext.
                if (pendingRef.current) await save(editor)
                onLockView?.()
              })()
            }}
          >
            <Icon name="lock" size={16} />
          </button>
        )}
        {note.role === 'owner' && !isLocal && !readOnly && note.locked && (
          <button
            type="button"
            className="tb-btn"
            title="Remove lock — make this a normal note again"
            onMouseDown={(e) => {
              e.preventDefault()
              void unlockNote()
            }}
          >
            Remove Lock
          </button>
        )}
        {onShare && (
          <button
            type="button"
            className="tb-btn"
            title="Share this note"
            onMouseDown={(e) => {
              e.preventDefault()
              onShare()
            }}
          >
            <Icon name="users" size={16} />
          </button>
        )}
        {note.role === 'owner' && !isLocal && folders.length > 0 && (
          <select
            className="folder-select"
            title="Move to folder"
            value={note.folder_id}
            onChange={(e) => void moveToFolder(e.target.value)}
          >
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        )}
        <span className="editor-meta" style={{ padding: 0 }}>
          {status === 'saving' && 'Saving…'}
          {status === 'saved' && 'Saved'}
          {status === 'conflict' && 'Updated elsewhere — reloading'}
          {status === 'offline' && 'Saved on this device — will sync'}
        </span>
      </div>
      <div className="editor-scroll">
        <div className="editor-meta">{dateLine}</div>
        <EditorContent editor={editor} className="tiptap-wrapper" />
      </div>
      {tagSuggest && !readOnly && (
        <div
          className="tag-suggest"
          style={{ left: tagSuggest.left, top: tagSuggest.top }}
        >
          {tagSuggest.items.map((t) => (
            <button
              key={t}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault()
                applyTagSuggest(t)
              }}
            >
              #{t}
            </button>
          ))}
        </div>
      )}
      {historyOpen && (
        <HistoryDialog
          noteId={note.id}
          onRestore={
            readOnly
              ? undefined
              : (rev: RevisionDetail) => {
                  // Load the old content and let autosave persist it — the
                  // restore itself becomes a new history entry.
                  editor.commands.setContent((rev.body as object) ?? rev.body_text)
                }
          }
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </div>
  )
}
