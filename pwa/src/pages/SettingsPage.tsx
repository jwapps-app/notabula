import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { AdminUser, TotpSetup, UserOut } from '../lib/api'
import { exportAllNotes } from '../lib/exportNotes'
import { importFromZip } from '../lib/importNotes'
import { APP_NAME } from '../constants/branding'
import { useAuth } from '../context/AuthContext'

function ExportSection() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <section>
      <h2>Export</h2>
      <p className="muted">
        Download every note (yours and ones shared with you) as a zip:
        plain-text files organized by folder, all images, and a lossless
        JSON copy. Your notes are never locked in.
      </p>
      <button
        className="btn-primary"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          setError(null)
          exportAllNotes(APP_NAME)
            .catch((err) =>
              setError(err instanceof Error ? err.message : 'Export failed'),
            )
            .finally(() => setBusy(false))
        }}
      >
        {busy ? 'Preparing…' : 'Export All Notes'}
      </button>
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

function ChangePasswordSection() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await api.changePassword(current, next)
      // Locked notes are encrypted with the account password — carry them
      // over to the new one so they stay unlockable.
      const { decryptBody, encryptBody, setSessionPassphrase } = await import(
        '../lib/noteCrypto'
      )
      setSessionPassphrase(next)
      let reEncrypted = 0
      let failed = 0
      const all = await api.syncNotes()
      for (const note of all.filter((n) => n.locked && n.cipher_body)) {
        try {
          const body = await decryptBody(note.cipher_body!, current)
          const cipher = await encryptBody(body, next)
          await api.updateNote(note.id, {
            base_version: note.version,
            cipher_body: cipher,
          })
          reEncrypted++
        } catch {
          failed++
        }
      }
      let msg = 'Password changed. Your other devices were signed out.'
      if (reEncrypted) msg += ` ${reEncrypted} locked note(s) re-encrypted.`
      if (failed)
        msg += ` ${failed} locked note(s) could not be re-encrypted — they still need your old password.`
      setNotice(msg)
      setCurrent('')
      setNext('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Change Password</h2>
      <form onSubmit={submit}>
        <label htmlFor="pw-current">Current password</label>
        <input
          id="pw-current"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <label htmlFor="pw-new">New password</label>
        <input
          id="pw-new"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <button className="btn-primary" type="submit" disabled={busy}>
          Change Password
        </button>
      </form>
      {notice && <p className="admin-notice">{notice}</p>}
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

function ImportSection() {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(file: File | undefined) {
    if (!file) return
    if (
      !window.confirm(
        `Import “${file.name}”?\n\nEvery note in the file is added as a new ` +
          'note (importing the same file twice creates duplicates).',
      )
    )
      return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await importFromZip(file)
      setNotice(
        `Imported ${result.imported} note(s)` +
          (result.mediaUploaded ? ` and ${result.mediaUploaded} image(s).` : '.'),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Import</h2>
      <p className="muted">
        Restore notes from an export zip — folders, dates, images, tags, and
        locked notes come back. Works with both the button above and the
        server's nightly export files.
      </p>
      <label className="btn-primary import-btn">
        {busy ? 'Importing…' : 'Import from Export Zip'}
        <input
          type="file"
          accept=".zip,application/zip"
          hidden
          disabled={busy}
          onChange={(e) => {
            void handleFile(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </label>
      {notice && <p className="admin-notice">{notice}</p>}
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

function NotificationsSection() {
  const [enabled, setEnabled] = useState<boolean | null>(null) // null = checking
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      const { currentSubscription, pushSupported } = await import('../lib/push')
      if (!pushSupported()) {
        setEnabled(false)
        return
      }
      setEnabled((await currentSubscription()) !== null)
    })()
  }, [])

  async function toggle() {
    setBusy(true)
    setError(null)
    try {
      const { disablePush, enablePush } = await import('../lib/push')
      if (enabled) {
        await disablePush()
        setEnabled(false)
      } else {
        await enablePush()
        setEnabled(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update notifications')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Notifications</h2>
      <p className="muted">
        Get a push when someone shares with you, when a shared note changes,
        when a guest edits via your link, and when a note's reminder comes
        due. On iPhone/iPad this needs the app added to the Home Screen
        (iOS 16.4+); enable it on each device you want notified.
      </p>
      <button
        className="btn-primary"
        disabled={busy || enabled === null}
        onClick={() => void toggle()}
      >
        {enabled === null
          ? 'Checking…'
          : busy
            ? 'Working…'
            : enabled
              ? 'Disable Notifications on This Device'
              : 'Enable Notifications on This Device'}
      </button>
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

function ShareShortcutSection() {
  const [copied, setCopied] = useState(false)

  const [error, setError] = useState<string | null>(null)

  async function copyLink() {
    setError(null)
    try {
      // Mint a fresh capture-ONLY token (not the session token) so the link
      // can't be used to sign in or reach anything but the capture endpoint.
      // Minting replaces any previous one, revoking older copied links.
      const { token } = await api.mintCaptureToken()
      await navigator.clipboard.writeText(
        `${window.location.origin}/api/v1/notes/capture?token=${token}`,
      )
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the link')
    }
  }

  return (
    <section>
      <h2>Share to {APP_NAME} (iPhone / iPad)</h2>
      <p className="muted">
        iOS doesn't let web apps into the share sheet, so a tiny Shortcut does
        it instead: share text or a link from any app and it lands here as a
        new note. Two taps to set up:
      </p>
      <ol className="muted shortcut-steps">
        <li>
          Tap <strong>Copy Capture Link</strong> below.
        </li>
        <li>
          Tap <strong>Get the Shortcut</strong>, add it, and paste the link
          when asked.
        </li>
      </ol>
      <div className="shortcut-actions">
        <button className="btn-primary" onClick={() => void copyLink()}>
          {copied ? 'Copied ✓' : '1. Copy Capture Link'}
        </button>
        <a className="btn-primary" href="/share-to-notabula.shortcut" download>
          2. Get the Shortcut
        </a>
      </div>
      {error && <div className="auth-error">{error}</div>}
      <p className="muted">
        Then "Share to {APP_NAME}" appears in every share sheet. The link
        carries a capture-only key — it can add notes but can't sign in or
        read anything, so it's far safer than a login. Copying a new link
        replaces the old one. To turn capture off entirely, change your
        password (or ask an admin) — a fresh link is always one tap away.
      </p>
    </section>
  )
}

function RestoreSection() {
  const [dbFile, setDbFile] = useState<File | null>(null)
  const [mediaFile, setMediaFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    if (!dbFile) return
    if (
      !window.confirm(
        `Restore this server from “${dbFile.name}”` +
          (mediaFile ? ` and “${mediaFile.name}”` : '') +
          '?\n\nThis REPLACES every account, note, and image on this server ' +
          'with the backup. Anything created since that backup is lost.\n\n' +
          'Everyone (including you) will be signed out and must log in with ' +
          'the passwords from the backup.',
      )
    )
      return
    setBusy(true)
    setError(null)
    try {
      await api.adminRestore(dbFile, mediaFile ?? undefined)
      window.alert(
        'Restore complete. Sign in again with your password from the backup.',
      )
      // The restored sessions table doesn't know this login — clean up
      // locally and start fresh. (api.logout would just 401.)
      const { clearOfflineCache } = await import('../lib/offline')
      const { clearSession } = await import('../lib/session')
      await clearOfflineCache().catch(() => {})
      clearSession()
      window.location.href = '/login'
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Restore failed')
      setBusy(false)
    }
  }

  const fileLabel = (f: File | null, placeholder: string) =>
    f ? f.name : placeholder

  return (
    <section>
      <h2>Restore from Backup</h2>
      <p className="muted">
        Rebuild this server from a nightly backup: pick the database dump
        (db-…dump) and the matching media archive (media-…tar.gz) from your
        backup folder, and everything — accounts, notes, images, history —
        returns to that moment. Replaces all current data.
      </p>
      <div className="restore-files">
        <label className="btn-primary import-btn">
          {fileLabel(dbFile, '1. Choose database dump')}
          <input
            type="file"
            accept=".dump"
            hidden
            disabled={busy}
            onChange={(e) => setDbFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <label className="btn-primary import-btn">
          {fileLabel(mediaFile, '2. Choose media archive (optional)')}
          <input
            type="file"
            accept=".gz,.tar.gz,application/gzip"
            hidden
            disabled={busy}
            onChange={(e) => setMediaFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <button
          className="btn-danger"
          disabled={busy || !dbFile}
          onClick={() => void run()}
        >
          {busy ? 'Restoring… (can take a few minutes)' : 'Restore Server'}
        </button>
      </div>
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

function AdminUsersSection({ me }: { me: UserOut }) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [username, setUsername] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = async () => setUsers(await api.adminListUsers())

  useEffect(() => {
    void refresh()
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }

  function addUser(e: FormEvent) {
    e.preventDefault()
    void run(async () => {
      await api.adminCreateUser(username, name, password)
      setNotice(`Account "${username.toLowerCase()}" created — share the username and password with them; they can change the password later.`)
      setUsername('')
      setName('')
      setPassword('')
    })
  }

  function resetPassword(user: AdminUser) {
    const pw = window.prompt(
      `New password for ${user.username} (min 8 chars).\n\n` +
        'Note: if they have LOCKED notes, those stay encrypted with their ' +
        'old password — a reset cannot recover them.',
    )
    if (!pw) return
    void run(async () => {
      await api.adminResetPassword(user.id, pw)
      setNotice(`Password reset for ${user.username}. Their other devices were signed out.`)
    })
  }

  function clearTotp(user: AdminUser) {
    if (!window.confirm(`Remove two-factor from ${user.username}'s account? Use this only if they lost their authenticator and recovery codes.`)) return
    void run(() => api.adminDisableTotp(user.id))
  }

  function removeUser(user: AdminUser) {
    if (!window.confirm(`Delete ${user.username} and ALL their notes? This cannot be undone.`)) return
    void run(() => api.adminDeleteUser(user.id))
  }

  return (
    <section>
      <h2>Users</h2>
      <div className="user-list">
        {users.map((u) => (
          <div className="user-row" key={u.id}>
            <div className="user-info">
              <strong>{u.name}</strong>
              <span className="muted">
                @{u.username}
                {u.is_admin && ' · admin'}
                {u.totp_enabled && ' · 2FA'}
              </span>
            </div>
            {u.id !== me.id && (
              <div className="user-actions">
                <button disabled={busy} onClick={() => resetPassword(u)}>
                  Reset password
                </button>
                {u.totp_enabled && (
                  <button disabled={busy} onClick={() => clearTotp(u)}>
                    Clear 2FA
                  </button>
                )}
                <button
                  className="destructive"
                  disabled={busy}
                  onClick={() => removeUser(u)}
                >
                  Delete
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={addUser} className="add-user-form">
        <h3>Add a user</h3>
        <label htmlFor="new-username">Username</label>
        <input
          id="new-username"
          autoCapitalize="none"
          autoCorrect="off"
          required
          minLength={3}
          maxLength={32}
          pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="new-name">Name</label>
        <input
          id="new-name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor="new-password">Temporary password</label>
        <input
          id="new-password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="btn-primary" type="submit" disabled={busy}>
          Create Account
        </button>
      </form>

      {notice && <p className="admin-notice">{notice}</p>}
      {error && <div className="auth-error">{error}</div>}
    </section>
  )
}

type TotpStage =
  | { step: 'off' }
  | { step: 'setup'; setup: TotpSetup; code: string }
  | { step: 'recovery'; codes: string[] }
  | { step: 'on'; code: string }

export default function SettingsPage() {
  const { logout } = useAuth()
  const [me, setMe] = useState<UserOut | null>(null)
  const [stage, setStage] = useState<TotpStage>({ step: 'off' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void (async () => {
      const user = await api.me()
      setMe(user)
      setStage(user.totp_enabled ? { step: 'on', code: '' } : { step: 'off' })
    })()
  }, [])

  async function startSetup() {
    setBusy(true)
    setError(null)
    try {
      const setup = await api.totpSetup()
      setStage({ step: 'setup', setup, code: '' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setBusy(false)
    }
  }

  async function confirmEnable(e: FormEvent) {
    e.preventDefault()
    if (stage.step !== 'setup') return
    setBusy(true)
    setError(null)
    try {
      const result = await api.totpEnable(stage.code)
      setStage({ step: 'recovery', codes: result.recovery_codes })
      setMe(await api.me())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not enable')
    } finally {
      setBusy(false)
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault()
    if (stage.step !== 'on') return
    setBusy(true)
    setError(null)
    try {
      await api.totpDisable(stage.code)
      setStage({ step: 'off' })
      setMe(await api.me())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not disable')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="settings-page">
      <div className="settings-card">
        <div className="settings-header">
          <Link to="/" className="back-link">
            ‹ Notes
          </Link>
          <h1>Settings</h1>
        </div>

        {me && (
          <section>
            <h2>Account</h2>
            <p>
              <strong>{me.name}</strong> · @{me.username}
              {me.is_admin && ' · admin'}
            </p>
          </section>
        )}

        <section>
          <h2>Two-Factor Authentication</h2>

          {stage.step === 'off' && (
            <>
              <p className="muted">
                Require a code from an authenticator app (or a saved recovery
                code) in addition to your password when signing in.
              </p>
              <button className="btn-primary" disabled={busy} onClick={startSetup}>
                Enable Two-Factor
              </button>
            </>
          )}

          {stage.step === 'setup' && (
            <form onSubmit={confirmEnable}>
              <p className="muted">
                Scan with your authenticator app (or enter the secret
                manually), then type the 6-digit code it shows.
              </p>
              <img
                className="totp-qr"
                alt="TOTP QR code"
                src={`data:image/png;base64,${stage.setup.qr_png_base64}`}
              />
              <p className="totp-secret">
                <code>{stage.setup.secret}</code>
              </p>
              <label htmlFor="enable-code">Verification code</label>
              <input
                id="enable-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={stage.code}
                onChange={(e) => setStage({ ...stage, code: e.target.value })}
              />
              <button className="btn-primary" type="submit" disabled={busy}>
                Verify &amp; Enable
              </button>
            </form>
          )}

          {stage.step === 'recovery' && (
            <>
              <p>
                <strong>Two-factor is on.</strong> Save these recovery codes
                somewhere safe — each works once if you lose your
                authenticator. They will not be shown again.
              </p>
              <div className="recovery-codes">
                {stage.codes.map((c) => (
                  <code key={c}>{c}</code>
                ))}
              </div>
              <button
                className="btn-primary"
                onClick={() => setStage({ step: 'on', code: '' })}
              >
                I saved them
              </button>
            </>
          )}

          {stage.step === 'on' && (
            <form onSubmit={disable}>
              <p>
                <strong>Two-factor is on.</strong> Enter a current code (or a
                recovery code) to turn it off.
              </p>
              <label htmlFor="disable-code">Verification code</label>
              <input
                id="disable-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={stage.code}
                onChange={(e) => setStage({ ...stage, code: e.target.value })}
              />
              <button className="btn-danger" type="submit" disabled={busy}>
                Disable Two-Factor
              </button>
            </form>
          )}

          {error && <div className="auth-error">{error}</div>}
        </section>

        <NotificationsSection />

        <ChangePasswordSection />

        <ExportSection />

        <ImportSection />

        <ShareShortcutSection />

        {me?.is_admin && <RestoreSection />}

        {me?.is_admin && <AdminUsersSection me={me} />}

        <section>
          <button className="btn-danger" onClick={() => void logout()}>
            Sign Out
          </button>
        </section>
      </div>
    </div>
  )
}
