/**
 * TipTap extension: make URLs in note text clickable AND render a rich
 * preview card under each link (title, description, image) — for links
 * typed into a note or imported from another app, without ever mutating
 * the document.
 *
 * Everything here is a ProseMirror Decoration: inline decorations turn
 * plain-text URLs into styled <a data-href>, and widget decorations add
 * a preview card after the block. Decorations don't change the doc, so
 * cards never touch body_text/JSON, never trigger autosave, and never
 * sync — they're pure presentation, and they show in read-only views too.
 */
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import type { EditorView } from '@tiptap/pm/view'
import type { Node as PMNode } from '@tiptap/pm/model'
import { getPreview, loadPreview } from './linkPreview'
import type { LinkPreviewOut } from './api'

const URL_RE = /https?:\/\/[^\s<>"')\]]+/g
const key = new PluginKey('linkify')

function cleanUrl(raw: string): string {
  return raw.replace(/[.,;:!?]+$/, '')
}

/** Build the preview-card DOM for a widget decoration. */
function renderCard(url: string, p: LinkPreviewOut): HTMLElement {
  const card = document.createElement('a')
  card.className = 'link-card'
  card.setAttribute('data-href', url)
  card.contentEditable = 'false'
  card.draggable = false

  if (p.image_url) {
    const img = document.createElement('img')
    img.className = 'link-card-img'
    img.src = p.image_url
    img.loading = 'lazy'
    img.alt = ''
    img.onerror = () => img.remove()
    card.appendChild(img)
  }

  const body = document.createElement('div')
  body.className = 'link-card-body'

  const title = document.createElement('div')
  title.className = 'link-card-title'
  title.textContent = p.title || url
  body.appendChild(title)

  if (p.description) {
    const desc = document.createElement('div')
    desc.className = 'link-card-desc'
    desc.textContent = p.description
    body.appendChild(desc)
  }

  const site = document.createElement('div')
  site.className = 'link-card-site'
  try {
    site.textContent = p.site_name || new URL(url).hostname
  } catch {
    site.textContent = p.site_name || url
  }
  body.appendChild(site)

  card.appendChild(body)
  return card
}

function buildDecorations(doc: PMNode, getView: () => EditorView | null): DecorationSet {
  const decorations: Decoration[] = []
  doc.descendants((node, pos) => {
    // Inline: make each URL clickable.
    if (node.isText && node.text) {
      for (const match of node.text.matchAll(URL_RE)) {
        const url = cleanUrl(match[0])
        decorations.push(
          Decoration.inline(pos + match.index!, pos + match.index! + url.length, {
            nodeName: 'a',
            class: 'note-link',
            'data-href': url,
          }),
        )
      }
    }
    // Block: one preview card per distinct URL, placed after the block.
    if (node.isTextblock && node.textContent) {
      const seen = new Set<string>()
      for (const match of node.textContent.matchAll(URL_RE)) {
        const url = cleanUrl(match[0])
        if (seen.has(url)) continue
        seen.add(url)
        const preview = getPreview(url)
        if (preview === undefined) {
          loadPreview(url, () => {
            const view = getView()
            if (view) view.dispatch(view.state.tr.setMeta(key, true))
          })
          continue
        }
        if (!preview) continue // fetched, nothing worth showing
        decorations.push(
          Decoration.widget(pos + node.nodeSize, () => renderCard(url, preview), {
            side: 1,
            key: `card:${url}`,
          }),
        )
      }
    }
  })
  return DecorationSet.create(doc, decorations)
}

export const Linkify = Extension.create({
  name: 'linkify',

  addProseMirrorPlugins() {
    let view: EditorView | null = null
    const getView = () => view

    const plugin: Plugin = new Plugin({
      key,
      state: {
        init: (_config, { doc }) => buildDecorations(doc, getView),
        apply: (tr, old) =>
          tr.docChanged || tr.getMeta(key) ? buildDecorations(tr.doc, getView) : old,
      },
      props: {
        decorations(state) {
          return plugin.getState(state)
        },
        handleDOMEvents: {
          click(_editorView, event) {
            const anchor = (event.target as HTMLElement).closest?.('a[data-href]')
            const href = anchor?.getAttribute('data-href')
            if (href) {
              event.preventDefault()
              window.open(href, '_blank', 'noopener')
              return true
            }
            return false
          },
        },
      },
      view(editorView) {
        view = editorView
        // Kick a rebuild now that we have a view, so uncached previews
        // begin loading on first render.
        queueMicrotask(() => {
          if (view) view.dispatch(view.state.tr.setMeta(key, true))
        })
        return {
          destroy() {
            view = null
          },
        }
      },
    })

    return [plugin]
  },
})
