/**
 * Client-side encryption for locked notes.
 *
 * PBKDF2-SHA256 (600k iterations) derives an AES-256-GCM key from the
 * user's passphrase; salt and IV are fresh per encryption and travel
 * inside the blob. The passphrase never leaves the browser — the server
 * only ever stores the JSON blob produced here. A wrong passphrase fails
 * GCM authentication, so decryption doubles as verification.
 *
 * Primitives chosen to be equally native in WebCrypto and Apple CryptoKit,
 * so a future native app can decrypt the same notes.
 */

const ITERATIONS = 600_000

const enc = new TextEncoder()
const dec = new TextDecoder()

function toB64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
}

function fromB64(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
}

async function deriveKey(passphrase: string, salt: Uint8Array): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    'raw',
    enc.encode(passphrase),
    'PBKDF2',
    false,
    ['deriveKey'],
  )
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: salt as BufferSource, iterations: ITERATIONS, hash: 'SHA-256' },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

/** Encrypt a ProseMirror document → self-contained blob string. */
export async function encryptBody(body: unknown, passphrase: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const key = await deriveKey(passphrase, salt)
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: iv as BufferSource },
    key,
    enc.encode(JSON.stringify(body ?? null)),
  )
  return JSON.stringify({
    v: 1,
    kdf: 'PBKDF2-SHA256',
    iter: ITERATIONS,
    salt: toB64(salt),
    iv: toB64(iv),
    ct: toB64(new Uint8Array(ct)),
  })
}

/** Decrypt a blob; throws if the passphrase is wrong or the blob is bad. */
export async function decryptBody(blob: string, passphrase: string): Promise<unknown> {
  const parsed = JSON.parse(blob)
  const key = await deriveKey(passphrase, fromB64(parsed.salt))
  const pt = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: fromB64(parsed.iv) as BufferSource },
    key,
    fromB64(parsed.ct) as BufferSource,
  )
  return JSON.parse(dec.decode(pt))
}

// --- Session passphrase cache (memory only, gone on reload) ----------------

let sessionPassphrase: string | null = null

export function getSessionPassphrase(): string | null {
  return sessionPassphrase
}

export function setSessionPassphrase(passphrase: string | null): void {
  sessionPassphrase = passphrase
}
