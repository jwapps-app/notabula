import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { EditorContent, useEditor } from '@tiptap/react'
import type { Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import TaskItem from '@tiptap/extension-task-item'
import TaskList from '@tiptap/extension-task-list'
import Underline from '@tiptap/extension-underline'
import Highlight from '@tiptap/extension-highlight'
import { HashtagHighlight } from '../lib/hashtagHighlight'
import { Linkify } from '../lib/linkify'
import { Link } from '../lib/link'
import { PdfEmbed } from '../lib/pdfEmbed'
import { ApiError, api } from '../lib/api'
import type { PublicNote } from '../lib/api'

const SAVE_DEBOUNCE_MS = 700
// Remembered across links so a returning guest isn't asked every time.
const GUEST_NAME_KEY = 'guestName'

/** First non-empty line = title, mirroring the app's own editor. */
function deriveTitle(editor: Editor): string {
  let title = ''
  editor.state.doc.forEach((node) => {
    if (!title) title = node.textContent.trim()
  })
  return title.slice(0, 400)
}

export default function PublicNotePage() {
  const { token = '' } = useParams()
  const [note, setNote] = useState<PublicNote | null>(null)
  const [gone, setGone] = useState(false)
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'conflict'>('idle')
  const [guestName, setGuestName] = useState<string>(
    () => localStorage.getItem(GUEST_NAME_KEY) ?? '',
  )
  const [showNameBar, setShowNameBar] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const versionRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef(false)
  // The current name goes into every save without re-subscribing the editor.
  const guestNameRef = useRef(guestName)
  guestNameRef.current = guestName

  const editor = useEditor({
    extensions: [
      StarterKit,
      Image,
      TaskList,
      TaskItem.configure({ nested: true }),
      Underline,
      Highlight,
      HashtagHighlight,
      Linkify,
      Link,
      PdfEmbed,
    ],
    editable: false,
    onUpdate: ({ editor }) => {
      pendingRef.current = true
      setStatus('saving')
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => void save(editor), SAVE_DEBOUNCE_MS)
    },
  })

  async function save(ed: Editor) {
    if (!pendingRef.current) return
    pendingRef.current = false
    try {
      const updated = await api.publicUpdate(token, {
        base_version: versionRef.current,
        title: deriveTitle(ed),
        body: ed.getJSON(),
        body_text: ed.getText({ blockSeparator: '\n' }),
        guest_name: guestNameRef.current || null,
      })
      versionRef.current = updated.version
      setStatus('saved')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setStatus('conflict')
        const fresh = await api.publicNote(token)
        versionRef.current = fresh.version
        ed.commands.setContent((fresh.body as object) ?? '', false)
        setStatus('saved')
      } else {
        setStatus('idle')
      }
    }
  }

  useEffect(() => {
    if (!editor) return
    api
      .publicNote(token)
      .then((data) => {
        setNote(data)
        versionRef.current = data.version
        editor.commands.setContent((data.body as object) ?? '', false)
        // A guest may only edit once they've named themselves — the note is
        // read-only until then. emitUpdate=false: no phantom save.
        const mayEdit = data.role === 'editor' && !!guestNameRef.current
        editor.setEditable(mayEdit, false)
        document.title = data.title || data.app_name
        if (data.role === 'editor' && !guestNameRef.current) {
          setShowNameBar(true)
        }
      })
      .catch(() => setGone(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, editor])

  function saveName(name: string) {
    const clean = name.trim().slice(0, 80)
    if (!clean) return // a name is required — can't save empty
    setGuestName(clean)
    localStorage.setItem(GUEST_NAME_KEY, clean)
    setShowNameBar(false)
    // Unlock editing now that they're identified.
    if (note?.role === 'editor') editor?.setEditable(true, false)
  }

  if (gone) {
    return (
      <div className="public-page">
        <div className="public-note public-gone">
          <h1>This link is no longer available</h1>
          <p className="muted">It may have been turned off by the note's owner.</p>
        </div>
      </div>
    )
  }

  const editable = note?.role === 'editor'

  return (
    <div className="public-page">
      <div className="public-note">
        <div className="public-topbar">
          <span className="public-brand">{note?.app_name ?? ''}</span>
          <span className="public-status">
            {editable && (
              <>
                <button
                  className="guest-name-btn"
                  title="Change how you appear in this note's history"
                  onClick={() => {
                    setNameDraft(guestName)
                    setShowNameBar(true)
                  }}
                >
                  {guestName ? `You: ${guestName}` : 'You: Guest'}
                </button>
                <span className="dot">·</span>
              </>
            )}
            {editable
              ? !guestName
                ? 'Enter your name to edit'
                : status === 'saving'
                  ? 'Saving…'
                  : status === 'saved'
                    ? 'Saved'
                    : status === 'conflict'
                      ? 'Updated elsewhere — reloading'
                      : 'Anyone with this link can edit'
              : 'View only'}
          </span>
        </div>

        {showNameBar && (
          <div className="guest-name-bar">
            <span>
              {guestName
                ? 'Change your name:'
                : 'Enter your name to edit — it shows the owner who made each change:'}
            </span>
            <input
              autoFocus
              placeholder="Your name"
              value={nameDraft}
              maxLength={80}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && saveName(nameDraft)}
            />
            <button
              className="btn-primary guest-name-save"
              disabled={!nameDraft.trim()}
              onClick={() => saveName(nameDraft)}
            >
              {guestName ? 'Save' : 'Start editing'}
            </button>
            {guestName && (
              <button className="guest-name-skip" onClick={() => setShowNameBar(false)}>
                Cancel
              </button>
            )}
          </div>
        )}

        <div className="editor-scroll public-scroll">
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  )
}
