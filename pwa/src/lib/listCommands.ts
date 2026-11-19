/** Toolbar commands that work from INSIDE a list.
 *
 * TipTap's stock toggles silently do nothing in several list situations,
 * because a list item must start with a paragraph and they try to lift only
 * that paragraph:
 *   - Heading / Subheading on an item that has nested items under it
 *   - Checklist on such a bullet (and Bullets / Numbers on such a checkbox)
 *   - Block quote on any list item
 * These wrappers handle those cases and defer to the stock command for
 * everything it already gets right.
 */

import type { ChainedCommands, Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import { canJoin } from '@tiptap/pm/transform'

const ITEM_TYPES = ['listItem', 'taskItem']

export type ListName = 'bulletList' | 'orderedList' | 'taskList'

/**
 * Take the caret's line out of every list it is nested in, then run `then`
 * on it — all in one undo step. Used for block types a list item can't be
 * (heading, quote), the same either/or the iOS app has: making a bullet a
 * heading turns it into a heading line, and whatever was nested under it
 * stays behind as a list of its own.
 */
export function outOfListsThen(
  editor: Editor,
  then: (chain: ChainedCommands) => ChainedCommands,
): boolean {
  const { $from } = editor.state.selection
  let levels = 0
  for (let d = $from.depth; d > 0; d--) {
    if (ITEM_TYPES.includes($from.node(d).type.name)) levels++
  }
  let chain = editor.chain().focus()
  for (let i = 0; i < levels; i++) {
    // One level per step; each step looks at the document as the previous
    // step left it.
    chain = chain.command(({ state, commands }) => {
      const $pos = state.selection.$from
      for (let d = $pos.depth; d > 0; d--) {
        const name = $pos.node(d).type.name
        if (ITEM_TYPES.includes(name)) return commands.liftListItem(name)
      }
      return true
    })
  }
  return then(chain).run()
}

function stockToggle(editor: Editor, target: ListName): boolean {
  const chain = editor.chain().focus()
  if (target === 'bulletList') return chain.toggleBulletList().run()
  if (target === 'orderedList') return chain.toggleOrderedList().run()
  return chain.toggleTaskList().run()
}

/**
 * Bullets / Numbers / Checklist button. Switching between a checkbox and a
 * bullet or number changes the item's own node type, which the stock toggle
 * can only do by lifting the line out of the list first — that fails when
 * the item has nested items, and drags a nested item up to the top level.
 * Here the one item is converted where it stands: its list is split around
 * it, its nested items stay under it, and it merges with a neighbouring
 * list of the new kind.
 */
export function toggleListKind(editor: Editor, target: ListName): boolean {
  const { state } = editor
  const { $from, $to } = state.selection
  let d = $from.depth
  while (d > 0 && !ITEM_TYPES.includes($from.node(d).type.name)) d--
  if (d < 2) return stockToggle(editor, target)

  const item = $from.node(d)
  const list = $from.node(d - 1)
  const targetItemName = target === 'taskList' ? 'taskItem' : 'listItem'
  // Toggling off, bullet <-> number, or a selection spanning several items:
  // the stock command already handles those.
  if (
    list.type.name === target ||
    item.type.name === targetItemName ||
    $to.pos > $from.end(d)
  ) {
    return stockToggle(editor, target)
  }

  const { schema } = state
  const listType = schema.nodes[target]
  const itemType = schema.nodes[targetItemName]
  const index = $from.index(d - 1)
  const listPos = $from.before(d - 1)

  const before: (typeof item)[] = []
  const after: (typeof item)[] = []
  list.forEach((child, _offset, i) => {
    if (i < index) before.push(child)
    else if (i > index) after.push(child)
  })
  const converted = listType.create(null, [
    itemType.create(targetItemName === 'taskItem' ? { checked: false } : null, item.content),
  ])
  const parts = [
    ...(before.length ? [list.type.create(list.attrs, before)] : []),
    converted,
    ...(after.length ? [list.type.create(list.attrs, after)] : []),
  ]

  const tr = state.tr.replaceWith(listPos, listPos + list.nodeSize, parts)
  const newListPos = listPos + (before.length ? parts[0].nodeSize : 0)
  // Caret back where it was in the text (+2: into the list, into the item).
  const offsetInItem = $from.pos - $from.start(d)
  tr.setSelection(TextSelection.create(tr.doc, newListPos + 2 + offsetInItem))

  // Become part of a neighbouring list of the same kind rather than a
  // one-item list beside it. End first, so the start position still holds.
  const joinIfSameKind = (pos: number) => {
    const $pos = tr.doc.resolve(pos)
    if (
      $pos.nodeBefore?.type === listType &&
      $pos.nodeAfter?.type === listType &&
      canJoin(tr.doc, pos)
    ) {
      tr.join(pos)
    }
  }
  joinIfSameKind(newListPos + converted.nodeSize)
  joinIfSameKind(newListPos)

  editor.view.dispatch(tr.scrollIntoView())
  editor.view.focus()
  return true
}
