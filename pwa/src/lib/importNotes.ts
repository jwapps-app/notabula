/**
 * Import an export zip back into the app — the other half of the escape
 * hatch. Handles both zip flavors (the Settings export and the nightly
 * server-side export): reads notes.json, re-uploads any bundled media and
 * remaps image URLs in note bodies, recreates folders by name, keeps
 * original timestamps, and passes locked notes' ciphertext through.
 */
import JSZip from 'jszip'
import { api } from './api'

interface RawNote {
  folder?: string
  folder_id?: string
  title?: string
  body?: unknown
  body_text?: string
  pinned?: boolean
  locked?: boolean
  cipher_body?: string | null
  created_at: string
  updated_at: string
}

export interface ImportResult {
  imported: number
  mediaUploaded: number
}

const BATCH = 500

export async function importFromZip(file: File): Promise<ImportResult> {
  const zip = await JSZip.loadAsync(file)
  const manifest = zip.file('notes.json')
  if (!manifest) {
    throw new Error('Not a recognized export: the zip has no notes.json')
  }
  const parsed = JSON.parse(await manifest.async('string'))
  const rawNotes: RawNote[] = Array.isArray(parsed) ? parsed : parsed.notes
  if (!Array.isArray(rawNotes)) {
    throw new Error('Not a recognized export: notes.json has no notes list')
  }
  // The Settings export carries a folders list; map ids → names.
  const folderNames = new Map<string, string>(
    (parsed.folders ?? []).map((f: { id: string; name: string }) => [f.id, f.name]),
  )

  // Re-upload bundled media, remembering old filename → new URL.
  const urlMap = new Map<string, string>()
  let mediaUploaded = 0
  for (const entry of Object.values(zip.files)) {
    if (entry.dir || !entry.name.startsWith('media/')) continue
    const filename = entry.name.slice('media/'.length)
    const blob = await entry.async('blob')
    const ext = filename.split('.').pop()?.toLowerCase() ?? 'png'
    const mime =
      { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
        webp: 'image/webp', heic: 'image/heic', svg: 'image/svg+xml' }[ext] ?? 'image/png'
    try {
      const up = await api.uploadAttachment(new File([blob], filename, { type: mime }))
      urlMap.set(`/media/attachments/${filename}`, up.url)
      mediaUploaded++
    } catch {
      // media volume unwritable or bad file — the text import still stands
    }
  }

  const remapBody = (body: unknown): unknown => {
    if (body == null || urlMap.size === 0) return body
    let text = JSON.stringify(body)
    for (const [oldUrl, newUrl] of urlMap) {
      text = text.split(oldUrl).join(newUrl)
    }
    return JSON.parse(text)
  }

  const notes = rawNotes.map((n) => ({
    folder:
      n.folder ??
      (n.folder_id ? folderNames.get(n.folder_id) : undefined) ??
      'Notes',
    title: n.title ?? '',
    body: n.locked ? null : remapBody(n.body),
    body_text: n.locked ? '' : (n.body_text ?? ''),
    pinned: n.pinned ?? false,
    locked: n.locked ?? false,
    cipher_body: n.locked ? (n.cipher_body ?? null) : null,
    created_at: n.created_at,
    updated_at: n.updated_at,
  }))

  let imported = 0
  for (let i = 0; i < notes.length; i += BATCH) {
    const result = await api.importNotes(notes.slice(i, i + BATCH))
    imported += result.imported
  }
  return { imported, mediaUploaded }
}
