import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { APP_NAME } from '../constants/branding'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'

export default function RegisterPage() {
  const { user, register } = useAuth()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<boolean | null>(null)

  useEffect(() => {
    api
      .meta()
      .then((m) => setOpen(m.allow_registration))
      .catch(() => setOpen(false))
  }, [])

  if (user) return <Navigate to="/" replace />

  // Registration only bootstraps the first (admin) account.
  if (open === false) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <h1>{APP_NAME}</h1>
          <p className="tagline">
            Registration is closed on this server. Ask your admin for an
            account.
          </p>
          <div className="auth-switch">
            <Link to="/login">Back to sign in</Link>
          </div>
        </div>
      </div>
    )
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await register(username, name, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={onSubmit}>
        <h1>{APP_NAME}</h1>
        <p className="tagline">Create the admin account for this server</p>

        <label htmlFor="name">Name</label>
        <input
          id="name"
          autoComplete="name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        <label htmlFor="username">Username</label>
        <input
          id="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          required
          minLength={3}
          maxLength={32}
          pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
          title="Letters, numbers, and . _ - (3–32 characters)"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="auth-error">{error}</div>}

        <button className="btn-primary" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create Account'}
        </button>

        <div className="auth-switch">
          Have an account? <Link to="/login">Sign in</Link>
        </div>
      </form>
    </div>
  )
}
