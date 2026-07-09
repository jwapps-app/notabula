import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'

/** Web Share Target: other apps share text/links here → a new note in the
 * default folder. (Android/Chrome; iOS Safari doesn't support this.) */
export default function SharePage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return
    ran.current = true
    void (async () => {
      try {
        const title = (params.get('title') ?? '').trim()
        const text = (params.get('text') ?? '').trim()
        const url = (params.get('url') ?? '').trim()
        const lines = [title, text, url].filter(Boolean)
        if (lines.length === 0) {
          navigate('/', { replace: true })
          return
        }
        const folders = await api.listFolders()
        const target = folders.find((f) => f.is_default) ?? folders[0]
        const note = await api.createNote(target.id)
        await api.updateNote(note.id, {
          base_version: note.version,
          title: lines[0].slice(0, 400),
          body: {
            type: 'doc',
            content: lines.map((line) => ({
              type: 'paragraph',
              content: [{ type: 'text', text: line }],
            })),
          },
          body_text: lines.join('\n'),
        })
        navigate('/', { replace: true })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not save the share')
      }
    })()
  }, [params, navigate])

  return (
    <div className="auth-page">
      <div className="auth-card">
        {error ? (
          <>
            <h1>Couldn’t save</h1>
            <p className="tagline">{error}</p>
          </>
        ) : (
          <p className="tagline">Saving to your notes…</p>
        )}
      </div>
    </div>
  )
}
