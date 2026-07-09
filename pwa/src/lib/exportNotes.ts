/**
 * Export everything as a zip — the anti-lock-in feature.
 *
 * Layout:
 *   <Folder Name>/<note-title>-<id8>.md   plain-text content per note
 *   Shared with me/…                      notes others shared with me
 *   media/…                               every image referenced by a note
 *   notes.json                            lossless dump (full ProseMirror
 *                                         bodies + folders) for re-import
 */
import JSZip from 'jszip'
import { api } from './api'
import type { NoteOut } from './api'

function slug(text: string): string {
  return (
    text
      .trim()
      .replace(/[^\p{L}\p{N} _-]/gu, '')
      .replace(/\s+/g, ' ')
      .slice(0, 60) || 'untitled'
  )
}

function mediaUrls(note: NoteOut): string[] {
  const body = JSON.stringify(note.body ?? '')
  return [...body.matchAll(/\/media\/attachments\/[\w.-]+/g)].map((m) => m[0])
}

export async function exportAllNotes(appName: string): Promise<void> {
  const [notes, folders] = await Promise.all([api.syncNotes(), api.listFolders()])
  const zip = new JSZip()
  const folderNames = new Map(folders.map((f) => [f.id, f.name]))

  zip.file(
    'notes.json',
    JSON.stringify(
      { exported_at: new Date().toISOString(), folders, notes },
      null,
      2,
    ),
  )

  for (const note of notes) {
    const dir =
      note.role === 'owner'
        ? folderNames.get(note.folder_id) ?? 'Notes'
        : 'Shared with me'
    const name = `${slug(note.title)}-${note.id.slice(0, 8)}.md`
    zip.file(`${dir}/${name}`, note.body_text || note.title || '')
  }

  // Include every referenced image (best effort — skip any that 404).
  const seen = new Set<string>()
  for (const note of notes) {
    for (const url of mediaUrls(note)) {
      if (seen.has(url)) continue
      seen.add(url)
      try {
        const resp = await fetch(url)
        if (resp.ok) {
          zip.file(`media/${url.split('/').pop()}`, await resp.blob())
        }
      } catch {
        // offline or missing file — the text export still stands
      }
    }
  }

  const blob = await zip.generateAsync({ type: 'blob' })
  const stamp = new Date().toISOString().slice(0, 10)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${appName.toLowerCase()}-export-${stamp}.zip`
  a.click()
  URL.revokeObjectURL(a.href)
}
