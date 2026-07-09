/**
 * Single source of truth for the app's display name on the PWA.
 *
 * Per project convention, the brand name "Notabula" appears ONLY here (and
 * in the backend APP_NAME env var, surfaced via /meta). Components must
 * reference these constants — never inline the literal string. Renaming the
 * app touches only this file (and the backend env var).
 */
export const APP_NAME = 'Notabula'
export const APP_TAGLINE = 'Your notes, on your server'
