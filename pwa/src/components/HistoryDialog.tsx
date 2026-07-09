import { useEffect, useState } from 'react'
import { diffWords } from 'diff'
import { api } from '../lib/api'
import type { RevisionDetail, RevisionListItem } from '../lib/api'

interface Props {
  noteId: string
  /** Offered when the current user may edit the note. */
  onRestore?: (revision: RevisionDetail) => void
  onClose: () => void
}

function sessionLabel(rev: RevisionListItem): string {
  const start = new Date(rev.created_at)
  return start.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Word-level redline: green additions, red struck-through deletions. */
function Redline({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after)
  return (
    <div className="redline">
      {parts.map((part, i) =>
        part.added ? (
          <ins key={i}>{part.value}</ins>
        ) : part.removed ? (
          <del key={i}>{part.value}</del>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </div>
  )
}

export default function HistoryDialog({ noteId, onRestore, onClose }: Props) {
  const [revisions, setRevisions] = useState<RevisionListItem[]>([])
  const [selected, setSelected] = useState<RevisionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listRevisions(noteId)
      .then(setRevisions)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
  }, [noteId])

  async function open(rev: RevisionListItem) {
    setError(null)
    try {
      setSelected(await api.getRevision(noteId, rev.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load revision')
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card history-card" onClick={(e) => e.stopPropagation()}>
        {selected === null ? (
          <>
            <h2>Edit history</h2>
            {revisions.length === 0 && <p className="muted">No history yet.</p>}
            {revisions.map((rev, i) => (
              <button className="history-row" key={rev.id} onClick={() => void open(rev)}>
                <strong>{rev.editor_name}</strong>
                <span className="muted">
                  {sessionLabel(rev)}
                  {i === 0 && ' · current'}
                </span>
              </button>
            ))}
          </>
        ) : (
          <>
            <h2>
              {selected.editor_name} · {sessionLabel(selected)}
            </h2>
            <p className="muted redline-key">
              <ins>added</ins> · <del>removed</del> (compared with the previous
              version)
            </p>
            <Redline before={selected.prev_body_text} after={selected.body_text} />
            <div className="history-actions">
              <button className="modal-done" onClick={() => setSelected(null)}>
                ‹ All versions
              </button>
              {onRestore && (
                <button
                  className="btn-primary history-restore"
                  onClick={() => {
                    onRestore(selected)
                    onClose()
                  }}
                >
                  Restore this version
                </button>
              )}
            </div>
          </>
        )}

        {error && <div className="auth-error">{error}</div>}
        {selected === null && (
          <button className="modal-done" onClick={onClose}>
            Done
          </button>
        )}
      </div>
    </div>
  )
}
