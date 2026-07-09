import { createContext, useCallback, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { clearOfflineCache, pendingCount } from '../lib/offline'
import { setSessionPassphrase } from '../lib/noteCrypto'
import { syncPending } from '../lib/sync'
import { clearSession, getSession, setSession } from '../lib/session'
import type { SessionUser } from '../lib/session'

interface AuthContextValue {
  user: SessionUser | null
  login: (username: string, password: string, totpCode?: string) => Promise<void>
  register: (username: string, name: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(() => getSession()?.user ?? null)

  const login = useCallback(
    async (username: string, password: string, totpCode?: string) => {
      const result = await api.login(username, password, totpCode)
      // A different person may have used this browser — never serve them
      // the previous user's cached notes.
      await clearOfflineCache().catch(() => {})
      // Locked notes are encrypted with the account password; keeping it
      // in memory for the session means unlocking never re-prompts.
      setSessionPassphrase(password)
      setSession({ sessionToken: result.session_token, user: result.user })
      setUser(result.user)
    },
    [],
  )

  const register = useCallback(
    async (username: string, name: string, password: string) => {
      const result = await api.register(username, name, password)
      await clearOfflineCache().catch(() => {})
      setSession({ sessionToken: result.session_token, user: result.user })
      setUser(result.user)
    },
    [],
  )

  const logout = useCallback(async () => {
    // Try to push any offline edits first; refuse to silently drop them.
    try {
      if ((await pendingCount()) > 0) {
        await syncPending().catch(() => {})
        if ((await pendingCount()) > 0) {
          const proceed = window.confirm(
            'You have edits that have not synced to the server yet. ' +
              'Signing out will discard them from this device. Sign out anyway?',
          )
          if (!proceed) return
        }
      }
    } catch {
      // cache unavailable — proceed with a normal logout
    }
    try {
      await api.logout()
    } catch {
      // Session may already be invalid server-side; clear locally regardless.
    }
    await clearOfflineCache().catch(() => {})
    setSessionPassphrase(null)
    clearSession()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
