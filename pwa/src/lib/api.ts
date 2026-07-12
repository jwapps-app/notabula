/** Thin fetch wrapper around the backend API. */
import { getSession } from './session'

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Thrown when the server is unreachable (offline / server down) — as
 * opposed to an HTTP error the server actually returned. */
export class OfflineError extends Error {
  constructor() {
    super('offline')
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')

  const session = getSession()
  if (session?.sessionToken) {
    headers.set('Authorization', `Bearer ${session.sessionToken}`)
  }

  let resp: Response
  try {
    resp = await fetch(`${BASE}${path}`, { ...options, headers })
  } catch {
    // fetch rejects only on network-level failure, never on HTTP status.
    throw new OfflineError()
  }
  // Gateway errors mean the API is down even though nginx/Cloudflare
  // answered — that's "offline" as far as the app is concerned.
  if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
    throw new OfflineError()
  }

  if (resp.status === 204) return undefined as T

  const body = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const detail =
      typeof body?.detail === 'string' ? body.detail : `Request failed (${resp.status})`
    throw new ApiError(resp.status, detail)
  }
  return body as T
}

// --- Types mirroring the backend schemas -------------------------------

export interface Meta {
  app_name: string
  app_tagline: string
  support_email: string
  allow_registration: boolean
}

export interface UserOut {
  id: string
  username: string
  name: string
  is_admin: boolean
  totp_enabled: boolean
}

export interface AdminUser extends UserOut {
  created_at: string
}

export interface TotpSetup {
  secret: string
  otpauth_uri: string
  qr_png_base64: string
}

export interface SessionResult {
  session_token: string
  user: UserOut
}

export interface FolderOut {
  id: string
  name: string
  parent_id: string | null
  position: number
  is_default: boolean
  created_at: string
  note_count: number
}

export interface TagOut {
  id: string
  name: string
  note_count: number
}

export interface LinkPreviewOut {
  url: string
  title: string | null
  description: string | null
  image_url: string | null
  site_name: string | null
  ok: boolean
}

export interface AttachmentOut {
  id: string
  url: string
  content_type: string
  size_bytes: number
}

export interface UserSummary {
  username: string
  name: string
}

export interface RevisionListItem {
  id: string
  version: number
  editor_name: string
  created_at: string
  updated_at: string
}

export interface RevisionDetail extends RevisionListItem {
  title: string
  body: unknown | null
  body_text: string
  prev_body_text: string
}

export type ShareRole = 'viewer' | 'editor'

export interface NoteLink {
  token: string
  role: string
}

export interface PublicNote {
  title: string
  body: unknown | null
  body_text: string
  updated_at: string
  version: number
  role: string
  app_name: string
}

export interface ShareOut {
  username: string
  name: string
  role: string
}

export interface SharedFolder {
  id: string
  name: string
  owner_name: string
  role: string
}

export interface NoteListItem {
  id: string
  folder_id: string
  title: string
  preview: string
  /** Gallery-view thumbnail: first image URL in the body, if any. */
  thumb: string | null
  pinned: boolean
  locked: boolean
  /** Per-note reminder (ISO datetime) — pushes the owner when due. */
  remind_at: string | null
  version: number
  created_at: string
  updated_at: string
  deleted_at: string | null
  role: string
  owner_name: string | null
}

export type SmartView = 'media' | 'links' | 'tasks' | 'locked' | 'recent'

export interface NoteOut {
  id: string
  folder_id: string
  title: string
  body: unknown | null
  body_text: string
  pinned: boolean
  locked: boolean
  cipher_body: string | null
  remind_at: string | null
  version: number
  created_at: string
  updated_at: string
  deleted_at: string | null
  role: string
  owner_name: string | null
}

// --- Endpoints ----------------------------------------------------------

export const api = {
  meta: () => request<Meta>('/meta'),

  register: (username: string, name: string, password: string) =>
    request<SessionResult>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, name, password }),
    }),
  login: (username: string, password: string, totpCode?: string) =>
    request<SessionResult>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, totp_code: totpCode ?? null }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<UserOut>('/auth/me'),

  // Capture-only token for the iOS-Shortcut link (revocable, not a session).
  mintCaptureToken: () =>
    request<{ token: string }>('/auth/capture-token', { method: 'POST' }),

  verifyPassword: (password: string) =>
    request<void>('/auth/verify-password', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>('/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  totpSetup: () => request<TotpSetup>('/auth/totp/setup', { method: 'POST' }),
  totpEnable: (code: string) =>
    request<{ recovery_codes: string[] }>('/auth/totp/enable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  totpDisable: (code: string) =>
    request<void>('/auth/totp/disable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),

  // --- Admin (403 for non-admins) ---
  adminListUsers: () => request<AdminUser[]>('/admin/users'),
  adminCreateUser: (username: string, name: string, password: string) =>
    request<UserOut>('/admin/users', {
      method: 'POST',
      body: JSON.stringify({ username, name, password }),
    }),
  adminResetPassword: (userId: string, password: string) =>
    request<void>(`/admin/users/${userId}/password`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  adminDisableTotp: (userId: string) =>
    request<void>(`/admin/users/${userId}/totp/disable`, { method: 'POST' }),
  adminDeleteUser: (userId: string) =>
    request<void>(`/admin/users/${userId}`, { method: 'DELETE' }),

  // Full-server restore from the nightly backup pair. Multipart, and the
  // server may take minutes — bypasses the JSON request wrapper.
  adminRestore: async (
    dbDump: File,
    mediaArchive?: File,
  ): Promise<{ restored: boolean; media_files: number }> => {
    const form = new FormData()
    form.append('db_dump', dbDump)
    if (mediaArchive) form.append('media_archive', mediaArchive)
    const headers = new Headers()
    const session = getSession()
    if (session?.sessionToken) {
      headers.set('Authorization', `Bearer ${session.sessionToken}`)
    }
    const resp = await fetch(`${BASE}/admin/restore`, {
      method: 'POST',
      headers,
      body: form,
    })
    const body = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const detail =
        typeof body?.detail === 'string' ? body.detail : `Restore failed (${resp.status})`
      throw new ApiError(resp.status, detail)
    }
    return body as { restored: boolean; media_files: number }
  },

  listFolders: () => request<FolderOut[]>('/folders'),
  createFolder: (name: string, parent_id?: string | null) =>
    request<FolderOut>('/folders', {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parent_id ?? null }),
    }),
  renameFolder: (id: string, name: string) =>
    request<FolderOut>(`/folders/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),
  deleteFolder: (id: string) =>
    request<void>(`/folders/${id}`, { method: 'DELETE' }),

  search: (q: string) =>
    request<NoteListItem[]>(`/search?q=${encodeURIComponent(q)}`),

  // Multipart upload — bypasses the JSON request wrapper.
  uploadAttachment: async (file: File): Promise<AttachmentOut> => {
    const form = new FormData()
    form.append('file', file)
    const headers = new Headers()
    const session = getSession()
    if (session?.sessionToken) {
      headers.set('Authorization', `Bearer ${session.sessionToken}`)
    }
    const resp = await fetch(`${BASE}/attachments`, {
      method: 'POST',
      headers,
      body: form,
    })
    const body = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const detail =
        typeof body?.detail === 'string' ? body.detail : `Upload failed (${resp.status})`
      throw new ApiError(resp.status, detail)
    }
    return body as AttachmentOut
  },

  linkPreview: (url: string) =>
    request<LinkPreviewOut>(`/links/preview?url=${encodeURIComponent(url)}`),

  // --- Push notifications ---
  vapidPublicKey: () => request<{ public_key: string }>('/push/vapid-public-key'),
  subscribePush: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<void>('/push/subscriptions', {
      method: 'POST',
      body: JSON.stringify(sub),
    }),
  unsubscribePush: (endpoint: string) =>
    request<void>('/push/subscriptions/delete', {
      method: 'POST',
      body: JSON.stringify({ endpoint }),
    }),

  listTags: () => request<TagOut[]>('/tags'),
  renameTag: (name: string, newName: string) =>
    request<{ updated: number }>(`/tags/${encodeURIComponent(name)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ new_name: newName }),
    }),

  listUsers: () => request<UserSummary[]>('/auth/users'),

  // Every accessible note in full — hydrates the offline cache.
  syncNotes: () => request<NoteOut[]>('/notes/sync'),

  importNotes: (notes: unknown[]) =>
    request<{ imported: number }>('/notes/import', {
      method: 'POST',
      body: JSON.stringify({ notes }),
    }),

  listRevisions: (noteId: string) =>
    request<RevisionListItem[]>(`/notes/${noteId}/revisions`),
  getRevision: (noteId: string, revisionId: string) =>
    request<RevisionDetail>(`/notes/${noteId}/revisions/${revisionId}`),

  // --- Public links ---
  getNoteLink: (noteId: string) => request<NoteLink | null>(`/notes/${noteId}/link`),
  upsertNoteLink: (noteId: string, role: ShareRole) =>
    request<NoteLink>(`/notes/${noteId}/link`, {
      method: 'PUT',
      body: JSON.stringify({ role }),
    }),
  revokeNoteLink: (noteId: string) =>
    request<void>(`/notes/${noteId}/link`, { method: 'DELETE' }),
  publicNote: (token: string) => request<PublicNote>(`/public/notes/${token}`),
  publicUpdate: (
    token: string,
    patch: {
      base_version: number
      title?: string
      body?: unknown
      body_text?: string
      guest_name?: string | null
    },
  ) =>
    request<PublicNote>(`/public/notes/${token}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  // --- Sharing ---
  sharedFolders: () => request<SharedFolder[]>('/shared/folders'),
  listShares: (type: 'note' | 'folder', id: string) =>
    request<ShareOut[]>(`/${type}s/${id}/shares`),
  addShare: (type: 'note' | 'folder', id: string, username: string, role: ShareRole) =>
    request<ShareOut[]>(`/${type}s/${id}/shares`, {
      method: 'PUT',
      body: JSON.stringify({ username, role }),
    }),
  removeShare: (type: 'note' | 'folder', id: string, username: string) =>
    request<ShareOut[]>(`/${type}s/${id}/shares/${encodeURIComponent(username)}`, {
      method: 'DELETE',
    }),

  listNotes: (
    opts: {
      folderId?: string
      tag?: string
      deleted?: boolean
      shared?: boolean
      view?: SmartView
    } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.folderId) params.set('folder_id', opts.folderId)
    if (opts.tag) params.set('tag', opts.tag)
    if (opts.deleted) params.set('deleted', 'true')
    if (opts.shared) params.set('shared', 'true')
    if (opts.view) params.set('view', opts.view)
    const qs = params.toString()
    return request<NoteListItem[]>(`/notes${qs ? `?${qs}` : ''}`)
  },
  getNote: (id: string) => request<NoteOut>(`/notes/${id}`),
  createNote: (folder_id: string) =>
    request<NoteOut>('/notes', {
      method: 'POST',
      body: JSON.stringify({ folder_id }),
    }),
  updateNote: (
    id: string,
    patch: {
      base_version: number
      title?: string
      body?: unknown
      body_text?: string
      pinned?: boolean
      folder_id?: string
      locked?: boolean
      cipher_body?: string
      remind_at?: string | null
    },
  ) =>
    request<NoteOut>(`/notes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteNote: (id: string, permanent = false) =>
    request<void>(`/notes/${id}${permanent ? '?permanent=true' : ''}`, {
      method: 'DELETE',
    }),
  restoreNote: (id: string) =>
    request<NoteOut>(`/notes/${id}/restore`, { method: 'POST' }),
}
