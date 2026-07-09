/** Persisted login session (token + user) in localStorage. */

export interface SessionUser {
  id: string
  username: string
  name: string
  is_admin: boolean
  totp_enabled?: boolean
}

export interface StoredSession {
  sessionToken: string
  user: SessionUser
}

const KEY = 'session'

export function getSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as StoredSession) : null
  } catch {
    return null
  }
}

export function setSession(session: StoredSession): void {
  localStorage.setItem(KEY, JSON.stringify(session))
}

export function clearSession(): void {
  localStorage.removeItem(KEY)
}
