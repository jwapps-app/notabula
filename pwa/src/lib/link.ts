/**
 * A stored `link` mark for the editor schema.
 *
 * Plain URLs typed in the PWA are never stored as marks — `linkify` decorates
 * them at render time (see lib/linkify.ts). But real link marks do arrive in
 * note documents: the iOS app writes them for attached files (scanned PDFs,
 * shared PDFs) and for shared web links. Without a `link` mark in the schema
 * ProseMirror throws "There is no mark type link in this schema" and refuses
 * the *entire* document, leaving the note blank.
 *
 * This is deliberately minimal — no autolink/linkOnPaste, so it renders and
 * round-trips stored links without competing with the linkify decoration.
 */
import { Mark, mergeAttributes } from '@tiptap/core'

/** Only hrefs that can't run script. Documents arrive from imports, shares
 * and other clients, so a stored mark is untrusted input; `javascript:`
 * (and friends) must never reach an <a href>. Relative paths cover our own
 * attachments. */
export function isSafeHref(href: unknown): href is string {
  if (typeof href !== 'string' || !href) return false
  if (href.startsWith('/') && !href.startsWith('//')) return true
  try {
    const { protocol } = new URL(href)
    return protocol === 'http:' || protocol === 'https:' || protocol === 'mailto:'
  } catch {
    return false
  }
}

export const Link = Mark.create({
  name: 'link',
  priority: 1000,
  inclusive: false,
  excludes: '_',

  addAttributes() {
    return {
      href: { default: null },
      title: { default: null },
    }
  },

  parseHTML() {
    return [{ tag: 'a[href]' }]
  },

  renderHTML({ HTMLAttributes }) {
    // Same class the linkify decoration uses, so stored and decorated links
    // look identical. An unsafe href renders as plain styled text: the mark
    // survives the round-trip, but the browser never sees the URL.
    const { href, ...rest } = HTMLAttributes
    return [
      'a',
      mergeAttributes(rest, {
        ...(isSafeHref(href) ? { href } : {}),
        class: 'note-link',
        rel: 'noopener noreferrer',
      }),
      0,
    ]
  },
})
