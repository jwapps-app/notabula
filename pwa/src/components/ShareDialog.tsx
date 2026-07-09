import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../lib/api'
import type { NoteLink, ShareOut, ShareRole, UserSummary } from '../lib/api'

/** "Anyone with the link" — create, copy, change role, revoke. Notes only. */
function PublicLinkSection({ noteId }: { noteId: string }) {
  const [link, setLink] = useState<NoteLink | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getNoteLink(noteId)
      .then(setLink)
      .catch(() => setLink(null))
      .finally(() => setLoaded(true))
  }, [noteId])

  const url = link ? `${window.location.origin}/s/${link.token}` : ''

  async function run(action: () => Promise<void>) {
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed')
    }
  }

  if (!loaded) return null

  return (
    <div className="link-section">
      <h3>Public link</h3>
      {link === null ? (
        <>
          <p className="muted share-hint">
            Anyone with the link can open this note — no account needed.
          </p>
          <div className="link-actions">
            <button
              className="btn-primary link-btn"
              onClick={() =>
                void run(async () => setLink(await api.upsertNoteLink(noteId, 'editor')))
              }
            >
              Create link
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="link-url" title={url}>
            {url}
          </div>
          <div className="link-actions">
            <button
              className="btn-primary link-btn"
              onClick={() =>
                void run(async () => {
                  await navigator.clipboard.writeText(url)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1500)
                })
              }
            >
              {copied ? 'Copied ✓' : 'Copy link'}
            </button>
            <select
              value={link.role}
              onChange={(e) =>
                void run(async () =>
                  setLink(await api.upsertNoteLink(noteId, e.target.value as ShareRole)),
                )
              }
            >
              <option value="editor">Can edit</option>
              <option value="viewer">Can view</option>
            </select>
            <button
              className="share-remove"
              onClick={() =>
                void run(async () => {
                  await api.revokeNoteLink(noteId)
                  setLink(null)
                })
              }
            >
              Revoke
            </button>
          </div>
          {link.role === 'editor' && (
            <p className="muted share-hint">
              Link editors can change the note; every edit lands in its history,
              so nothing is ever lost.
            </p>
          )}
        </>
      )}
      {error && <div className="auth-error">{error}</div>}
    </div>
  )
}

export interface ShareTarget {
  type: 'note' | 'folder'
  id: string
  name: string
}

interface Props {
  target: ShareTarget
  onClose: () => void
}

export default function ShareDialog({ target, onClose }: Props) {
  const [shares, setShares] = useState<ShareOut[]>([])
  const [users, setUsers] = useState<UserSummary[]>([])
  const [username, setUsername] = useState('')
  const [role, setRole] = useState<ShareRole>('editor')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    Promise.all([api.listShares(target.type, target.id), api.listUsers()])
      .then(([shareList, userList]) => {
        setShares(shareList)
        setUsers(userList)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
  }, [target])

  // Everyone on the server who doesn't already have access.
  const candidates = useMemo(() => {
    const taken = new Set(shares.map((s) => s.username))
    return users.filter((u) => !taken.has(u.username))
  }, [users, shares])

  useEffect(() => {
    // Keep the picker pointing at a valid candidate.
    if (!candidates.some((c) => c.username === username)) {
      setUsername(candidates[0]?.username ?? '')
    }
  }, [candidates, username])

  async function add(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setShares(await api.addShare(target.type, target.id, username, role))
      setUsername('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not share')
    } finally {
      setBusy(false)
    }
  }

  async function remove(share: ShareOut) {
    setBusy(true)
    setError(null)
    try {
      setShares(await api.removeShare(target.type, target.id, share.username))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h2>
          Share {target.type === 'folder' ? 'folder' : 'note'} “{target.name}”
        </h2>

        {shares.length === 0 && (
          <p className="muted">Not shared with anyone yet.</p>
        )}
        {shares.map((s) => (
          <div className="share-row" key={s.username}>
            <div className="user-info">
              <strong>{s.name}</strong>
              <span className="muted">
                @{s.username} · can {s.role === 'editor' ? 'edit' : 'view'}
              </span>
            </div>
            <button
              className="share-remove"
              disabled={busy}
              onClick={() => void remove(s)}
            >
              Remove
            </button>
          </div>
        ))}

        {candidates.length > 0 ? (
          <form onSubmit={add} className="share-add">
            <select
              className="share-user-select"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            >
              {candidates.map((u) => (
                <option key={u.username} value={u.username}>
                  {u.name} (@{u.username})
                </option>
              ))}
            </select>
            <select value={role} onChange={(e) => setRole(e.target.value as ShareRole)}>
              <option value="editor">Can edit</option>
              <option value="viewer">Can view</option>
            </select>
            <button className="btn-primary" type="submit" disabled={busy || !username}>
              Share
            </button>
          </form>
        ) : (
          shares.length > 0 && (
            <p className="muted share-hint">Everyone on this server already has access.</p>
          )
        )}

        {target.type === 'folder' && (
          <p className="muted share-hint">
            Covers every note in this folder, including ones added later.
          </p>
        )}
        {error && <div className="auth-error">{error}</div>}

        {target.type === 'note' && <PublicLinkSection noteId={target.id} />}

        <button className="modal-done" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  )
}
