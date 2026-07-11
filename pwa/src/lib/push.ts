/** Web Push subscription management for the installed PWA.

iOS requires the app to be added to the Home Screen (16.4+) before the
Push API exists; desktop Chrome/Firefox work in the tab. The server's
VAPID public key identifies our server to the browser's push service. */
import { api } from './api'

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export function pushSupported(): boolean {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  // getRegistration resolves immediately (undefined when no worker) —
  // .ready would hang forever if registration failed or never happened.
  const reg = await navigator.serviceWorker.getRegistration()
  return reg ? reg.pushManager.getSubscription() : null
}

/** Ask permission, subscribe, and register with the server.
 * Throws Error with a human message on any refusal/failure. */
export async function enablePush(): Promise<void> {
  if (!pushSupported()) {
    throw new Error(
      'Notifications need the installed app (Add to Home Screen on iOS).',
    )
  }
  const reg = await navigator.serviceWorker.getRegistration()
  if (!reg) {
    throw new Error(
      'The app is not fully installed yet — reload once and try again.',
    )
  }
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(
      'Notifications are blocked. Allow them for this app in system settings.',
    )
  }
  const { public_key } = await api.vapidPublicKey()
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    }))
  const json = sub.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error('The browser returned an incomplete subscription.')
  }
  await api.subscribePush({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
  })
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (!sub) return
  await api.unsubscribePush(sub.endpoint).catch(() => {})
  await sub.unsubscribe()
}
