import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { APP_NAME, APP_TAGLINE } from '../constants/branding'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  // Revealed when the account has 2FA: the server answers "totp_required".
  const [needsTotp, setNeedsTotp] = useState(false)
  const [totpCode, setTotpCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Registration exists only until the first (admin) account is created.
  const [canRegister, setCanRegister] = useState(false)

  useEffect(() => {
    api
      .meta()
      .then((m) => setCanRegister(m.allow_registration))
      .catch(() => setCanRegister(false))
  }, [])

  if (user) return <Navigate to="/" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password, needsTotp ? totpCode : undefined)
      navigate('/', { replace: true })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Login failed'
      if (message === 'totp_required') {
        setNeedsTotp(true)
      } else {
        setError(message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={onSubmit}>
        <h1>{APP_NAME}</h1>
        <p className="tagline">{APP_TAGLINE}</p>

        <label htmlFor="username">Username</label>
        <input
          id="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {needsTotp && (
          <>
            <label htmlFor="totp">Verification code</label>
            <input
              id="totp"
              autoComplete="one-time-code"
              inputMode="numeric"
              placeholder="6-digit or recovery code"
              autoFocus
              required
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
            />
          </>
        )}

        {error && <div className="auth-error">{error}</div>}

        <button className="btn-primary" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign In'}
        </button>

        {canRegister && (
          <div className="auth-switch">
            First time setup? <Link to="/register">Create the admin account</Link>
          </div>
        )}
      </form>
    </div>
  )
}
