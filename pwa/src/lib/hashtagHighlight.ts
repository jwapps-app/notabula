/**
 * TipTap extension: decorate #hashtags in note text with the accent color.
 *
 * Display-only — the actual tags are extracted server-side from body_text
 * on save, so this regex only needs to visually agree with that one
 * (letters/digits/underscore/hyphen, at least one letter).
 */
import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import type { Node as PMNode } from '@tiptap/pm/model'

const TAG_RE = /#[\w-]*[a-zA-Z][\w-]*/g

function buildDecorations(doc: PMNode): DecorationSet {
  const decorations: Decoration[] = []
  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return
    for (const match of node.text.matchAll(TAG_RE)) {
      decorations.push(
        Decoration.inline(pos + match.index, pos + match.index + match[0].length, {
          class: 'hashtag',
        }),
      )
    }
  })
  return DecorationSet.create(doc, decorations)
}

export const HashtagHighlight = Extension.create({
  name: 'hashtagHighlight',

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey('hashtagHighlight'),
        state: {
          init: (_config, { doc }) => buildDecorations(doc),
          apply: (tr, old) => (tr.docChanged ? buildDecorations(tr.doc) : old),
        },
        props: {
          decorations(state) {
            return this.getState(state)
          },
        },
      }),
    ]
  },
})
