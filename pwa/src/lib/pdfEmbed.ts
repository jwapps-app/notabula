/**
 * TipTap extension: show attached PDFs inline, so opening a note that has a
 * scanned/shared PDF displays the document itself instead of just a link.
 *
 * Like lib/linkify, this is purely a ProseMirror widget Decoration — it never
 * mutates the document, so it doesn't touch body_text/JSON, doesn't trigger
 * autosave, doesn't sync, and works in read-only views too.
 *
 * Picks up both shapes a PDF can arrive in: a real `link` mark (what the iOS
 * app writes for attached files) and a bare .pdf URL in the text.
 */
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import type { Node as PMNode } from '@tiptap/pm/model'

const URL_RE = /https?:\/\/[^\s<>"')\]]+/g
const key = new PluginKey('pdfEmbed')

function isPdf(href: string): boolean {
  return /\.pdf(\?|#|$)/i.test(href)
}

function fileName(href: string): string {
  const last = href.split(/[?#]/)[0].split('/').pop() || 'PDF'
  try {
    return decodeURIComponent(last)
  } catch {
    return last
  }
}

/** The inline viewer for one PDF. */
function renderEmbed(href: string): HTMLElement {
  const wrap = document.createElement('div')
  wrap.className = 'pdf-embed'
  wrap.contentEditable = 'false'
  wrap.draggable = false

  const bar = document.createElement('div')
  bar.className = 'pdf-embed-bar'

  const name = document.createElement('span')
  name.className = 'pdf-embed-name'
  name.textContent = fileName(href)

  const open = document.createElement('a')
  open.className = 'pdf-embed-open'
  open.textContent = 'Open'
  open.href = href
  open.target = '_blank'
  open.rel = 'noopener noreferrer'

  bar.append(name, open)

  const frame = document.createElement('iframe')
  frame.className = 'pdf-embed-frame'
  frame.src = href
  frame.loading = 'lazy'
  frame.title = fileName(href)

  wrap.append(bar, frame)
  return wrap
}

function buildDecorations(doc: PMNode): DecorationSet {
  const decorations: Decoration[] = []
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return
    // One viewer per distinct PDF, placed after the block that references it.
    const hrefs = new Set<string>()
    node.descendants((child) => {
      if (!child.isText) return
      for (const mark of child.marks) {
        const href = mark.type.name === 'link' ? (mark.attrs.href as string | null) : null
        if (href && isPdf(href)) hrefs.add(href)
      }
      for (const match of (child.text ?? '').matchAll(URL_RE)) {
        const url = match[0].replace(/[.,;:!?]+$/, '')
        if (isPdf(url)) hrefs.add(url)
      }
    })
    for (const href of hrefs) {
      decorations.push(
        Decoration.widget(pos + node.nodeSize, () => renderEmbed(href), {
          side: 1,
          key: `pdf:${href}`,
        }),
      )
    }
  })
  return DecorationSet.create(doc, decorations)
}

export const PdfEmbed = Extension.create({
  name: 'pdfEmbed',

  addProseMirrorPlugins() {
    const plugin: Plugin = new Plugin({
      key,
      state: {
        init: (_config, { doc }) => buildDecorations(doc),
        apply: (tr, old) => (tr.docChanged ? buildDecorations(tr.doc) : old),
      },
      props: {
        decorations(state) {
          return plugin.getState(state)
        },
      },
    })
    return [plugin]
  },
})
