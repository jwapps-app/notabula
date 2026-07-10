import { useEffect, useRef, useState } from 'react'
import type { FolderOut, NoteListItem } from '../lib/api'
import { formatNoteDate } from '../lib/dates'
import Icon from './Icon'

/** How far the card slides open to reveal the two swipe actions. */
const SWIPE_OPEN = -132

type SortBy = 'updated' | 'created' | 'title'
type ViewMode = 'list' | 'gallery'

interface Props {
  title: string
  notes: NoteListItem[]
  selectedId: string | null
  isDeletedView: boolean
  /** New notes can only be created in a real folder view (not tag/deleted). */
  canCreate: boolean
  /** Bulk select is for views of my own notes. */
  canBulk: boolean
  folders: FolderOut[]
  sortBy: SortBy
  onSortChange: (s: SortBy) => void
  viewMode: ViewMode
  onViewModeChange: (v: ViewMode) => void
  onBulkMove: (ids: string[], folderId: string) => void
  onBulkTag: (ids: string[]) => void
  onBulkDelete: (ids: string[]) => void
  /** Search query — server-side, across all folders. Owned by the parent. */
  query: string
  onQueryChange: (q: string) => void
  /** When set, the list title is tappable (rename the selected tag). */
  onRenameTitle?: () => void
  onSelect: (id: string) => void
  onNewNote: () => void
  onTogglePin: (note: NoteListItem) => void
  onDelete: (note: NoteListItem) => void
  onRestore: (note: NoteListItem) => void
  onBack: () => void
}

/** Pin/delete (or restore/purge in the trash) — shared by both views. */
function CardActions({
  note,
  isDeletedView,
  onTogglePin,
  onDelete,
  onRestore,
}: {
  note: NoteListItem
  isDeletedView: boolean
  onTogglePin: (n: NoteListItem) => void
  onDelete: (n: NoteListItem) => void
  onRestore: (n: NoteListItem) => void
}) {
  return (
    <div className="note-actions" onClick={(e) => e.stopPropagation()}>
      {isDeletedView ? (
        <>
          <button title="Restore" onClick={() => onRestore(note)}>
            <Icon name="restore" size={15} />
          </button>
          <button title="Delete permanently" onClick={() => onDelete(note)}>
            <Icon name="x" size={15} />
          </button>
        </>
      ) : (
        <>
          <button
            title={note.pinned ? 'Unpin' : 'Pin'}
            onClick={() => onTogglePin(note)}
          >
            <Icon name="pin" size={15} filled={note.pinned} />
          </button>
          <button title="Delete" onClick={() => onDelete(note)}>
            <Icon name="trash" size={15} />
          </button>
        </>
      )}
    </div>
  )
}

/** Gallery view: a visual card — image thumbnail when the note has one,
 * a mini text preview otherwise, like iOS Notes' gallery. */
function GalleryCard({
  note,
  selected,
  isDeletedView,
  onSelect,
  onTogglePin,
  onDelete,
  onRestore,
}: {
  note: NoteListItem
  selected: boolean
  isDeletedView: boolean
  onSelect: (id: string) => void
  onTogglePin: (n: NoteListItem) => void
  onDelete: (n: NoteListItem) => void
  onRestore: (n: NoteListItem) => void
}) {
  return (
    <div
      className={`gallery-card${selected ? ' selected' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(note.id)}
      onKeyDown={(e) => e.key === 'Enter' && onSelect(note.id)}
    >
      <div className="gallery-thumb">
        {note.locked ? (
          <Icon name="lock" size={26} strokeWidth={1.5} />
        ) : note.thumb ? (
          <img src={note.thumb} alt="" loading="lazy" draggable={false} />
        ) : (
          <div className="gallery-text">{note.preview || ' '}</div>
        )}
      </div>
      <div className="gallery-meta">
        <p className="gallery-title">
          {note.pinned && !isDeletedView && <Icon name="pin" size={11} filled />}
          {note.title || 'New Note'}
        </p>
        <p className="gallery-date">{formatNoteDate(note.updated_at)}</p>
      </div>
      {note.role === 'owner' && (
        <CardActions
          note={note}
          isDeletedView={isDeletedView}
          onTogglePin={onTogglePin}
          onDelete={onDelete}
          onRestore={onRestore}
        />
      )}
    </div>
  )
}

function NoteCard({
  note,
  selected,
  isDeletedView,
  swiped,
  onSwipe,
  onSelect,
  onTogglePin,
  onDelete,
  onRestore,
}: {
  note: NoteListItem
  selected: boolean
  isDeletedView: boolean
  /** True while this row is the list's one open swipe. */
  swiped: boolean
  /** Claim (id) or release (null) the list-wide open-swipe slot. */
  onSwipe: (id: string | null) => void
  onSelect: (id: string) => void
  onTogglePin: (n: NoteListItem) => void
  onDelete: (n: NoteListItem) => void
  onRestore: (n: NoteListItem) => void
}) {
  // Touch swipe (iOS-style): slide left to reveal pin/delete actions.
  // Only one row may be open at a time — the parent owns that slot; when
  // it's taken away (another row swiped, a note opened, view changed),
  // this row snaps shut.
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const touchRef = useRef<{
    x: number
    y: number
    base: number
    horizontal: boolean | null
    last: number
  } | null>(null)
  const canSwipe = note.role === 'owner'

  useEffect(() => {
    if (!swiped) setOffset(0)
  }, [swiped])

  const onTouchStart = (e: React.TouchEvent) => {
    // Touch events only fire on touch screens, so this never runs on a
    // desktop — but it does on an iPad showing the three-pane layout.
    if (!canSwipe) return
    const t = e.touches[0]
    touchRef.current = {
      x: t.clientX,
      y: t.clientY,
      base: offset,
      horizontal: null,
      last: offset,
    }
    setDragging(true)
  }
  const onTouchMove = (e: React.TouchEvent) => {
    const s = touchRef.current
    if (!s) return
    const t = e.touches[0]
    const dx = t.clientX - s.x
    const dy = t.clientY - s.y
    // Decide once whether this gesture is a horizontal swipe or a scroll.
    if (s.horizontal === null) {
      if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return
      s.horizontal = Math.abs(dx) > Math.abs(dy)
      // Dragging this row closes whichever other row was open.
      if (s.horizontal) onSwipe(note.id)
    }
    if (!s.horizontal) return
    s.last = Math.min(0, Math.max(SWIPE_OPEN - 30, s.base + dx))
    setOffset(s.last)
  }
  const onTouchEnd = () => {
    const s = touchRef.current
    touchRef.current = null
    setDragging(false)
    if (!s || s.horizontal !== true) return
    const open = s.last < SWIPE_OPEN / 2
    setOffset(open ? SWIPE_OPEN : 0)
    onSwipe(open ? note.id : null)
  }

  const handleClick = () => {
    // A tap on a swiped-open card closes it instead of opening the note.
    if (offset !== 0 || swiped) {
      onSwipe(null)
      setOffset(0)
      return
    }
    onSelect(note.id)
  }
  const runAction = (fn: (n: NoteListItem) => void) => {
    onSwipe(null)
    setOffset(0)
    fn(note)
  }

  // The action buttons exist in the DOM only mid-swipe: card backgrounds
  // like the selected-row tint are translucent, so buttons parked behind
  // every row would show through them.
  const showActions = canSwipe && (dragging || offset !== 0 || swiped)

  return (
    <div className="note-card-wrap">
      {showActions && (
        <div className="swipe-actions">
          {isDeletedView ? (
            <>
              <button
                className="swipe-pin"
                title="Restore"
                tabIndex={-1}
                onClick={() => runAction(onRestore)}
              >
                <Icon name="restore" size={20} />
              </button>
              <button
                className="swipe-delete"
                title="Delete permanently"
                tabIndex={-1}
                onClick={() => runAction(onDelete)}
              >
                <Icon name="x" size={20} />
              </button>
            </>
          ) : (
            <>
              <button
                className="swipe-pin"
                title={note.pinned ? 'Unpin' : 'Pin'}
                tabIndex={-1}
                onClick={() => runAction(onTogglePin)}
              >
                <Icon name="pin" size={20} filled={note.pinned} />
              </button>
              <button
                className="swipe-delete"
                title="Delete"
                tabIndex={-1}
                onClick={() => runAction(onDelete)}
              >
                <Icon name="trash" size={20} />
              </button>
            </>
          )}
        </div>
      )}
      <div
        className={`note-card${note.pinned && !isDeletedView ? ' pinned' : ''}${
          selected ? ' selected' : ''
        }`}
        role="button"
        tabIndex={0}
        style={{
          transform: offset ? `translateX(${offset}px)` : undefined,
          transition: dragging ? 'none' : 'transform 0.2s ease',
        }}
        onClick={handleClick}
        onKeyDown={(e) => e.key === 'Enter' && onSelect(note.id)}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
      >
        <p className="note-title">
          {note.locked && (
            <span className="note-lock">
              <Icon name="lock" size={12} />{' '}
            </span>
          )}
          {note.title || 'New Note'}
        </p>
        <div className="note-sub">
          <span>{formatNoteDate(note.updated_at)}</span>
          {note.owner_name && (
            <span className="shared-by">
              <Icon name="users" size={12} /> {note.owner_name}
            </span>
          )}
          <span className="preview">{note.preview || 'No additional text'}</span>
        </div>
        {note.role === 'owner' && (
          <CardActions
            note={note}
            isDeletedView={isDeletedView}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
            onRestore={onRestore}
          />
        )}
      </div>
    </div>
  )
}

export default function NoteList({
  title,
  notes,
  selectedId,
  isDeletedView,
  canCreate,
  canBulk,
  folders,
  sortBy,
  onSortChange,
  viewMode,
  onViewModeChange,
  onBulkMove,
  onBulkTag,
  onBulkDelete,
  query,
  onQueryChange,
  onRenameTitle,
  onSelect,
  onNewNote,
  onTogglePin,
  onDelete,
  onRestore,
  onBack,
}: Props) {
  const searching = query.trim().length > 0
  const [selectMode, setSelectMode] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  // The one row allowed to sit swiped-open, if any.
  const [swipedId, setSwipedId] = useState<string | null>(null)

  // Leaving the view exits select mode.
  useEffect(() => {
    setSelectMode(false)
    setPicked(new Set())
  }, [title])

  // Opening a note or changing views closes any open swipe — no row may
  // come back from the editor still showing its actions.
  useEffect(() => {
    setSwipedId(null)
  }, [title, selectedId, selectMode])

  const togglePick = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const exitSelect = () => {
    setSelectMode(false)
    setPicked(new Set())
  }

  // Pinned grouping only applies to a normal folder view — search results
  // and Recently Deleted render as a flat list.
  const grouped = !searching && !isDeletedView
  const pinned = grouped ? notes.filter((n) => n.pinned) : []
  const others = grouped ? notes.filter((n) => !n.pinned) : notes

  const handleSelect = (id: string) => {
    setSwipedId(null)
    if (selectMode) togglePick(id)
    else onSelect(id)
  }

  // One section of notes, in whichever view is active. Gallery cards skip
  // the swipe machinery — tap opens, hover/bulk-select handle actions.
  const renderNotes = (items: NoteListItem[]) =>
    viewMode === 'gallery' ? (
      <div className="note-grid">
        {items.map((n) => (
          <GalleryCard
            key={n.id}
            note={n}
            selected={selectMode ? picked.has(n.id) : n.id === selectedId}
            isDeletedView={isDeletedView}
            onSelect={handleSelect}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
            onRestore={onRestore}
          />
        ))}
      </div>
    ) : (
      items.map((n) => (
        <NoteCard
          key={n.id}
          note={n}
          selected={selectMode ? picked.has(n.id) : n.id === selectedId}
          isDeletedView={isDeletedView}
          swiped={swipedId === n.id}
          onSwipe={setSwipedId}
          onSelect={handleSelect}
          onTogglePin={onTogglePin}
          onDelete={onDelete}
          onRestore={onRestore}
        />
      ))
    )

  return (
    <div className="pane pane-list">
      <div className="pane-header">
        <button className="back-btn" onClick={onBack}>
          ‹ Folders
        </button>
        {!searching && onRenameTitle ? (
          <h2>
            <button
              className="title-rename"
              title="Rename this tag everywhere"
              onClick={onRenameTitle}
            >
              {title} <Icon name="edit" size={13} />
            </button>
          </h2>
        ) : (
          <h2>{searching ? 'Search' : title}</h2>
        )}
        {canBulk && notes.length > 0 && (
          <button
            className="icon-btn select-toggle"
            title={selectMode ? 'Done selecting' : 'Select notes'}
            onClick={() => (selectMode ? exitSelect() : setSelectMode(true))}
          >
            {selectMode ? <Icon name="x" size={17} /> : <Icon name="check-square" size={17} />}
          </button>
        )}
        {canCreate && !selectMode && (
          <button className="icon-btn" title="New note" onClick={onNewNote}>
            <Icon name="compose" size={17} />
          </button>
        )}
      </div>
      <div className="search-box">
        <input
          placeholder="Search all notes"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
      </div>
      <div className="list-sort">
        <label>
          Sort:{' '}
          <select value={sortBy} onChange={(e) => onSortChange(e.target.value as SortBy)}>
            <option value="updated">Last edited</option>
            <option value="created">Date created</option>
            <option value="title">Title</option>
          </select>
        </label>
        <span className="view-toggle">
          <button
            className={viewMode === 'list' ? 'active' : ''}
            title="List view"
            onClick={() => onViewModeChange('list')}
          >
            <Icon name="rows" size={15} />
          </button>
          <button
            className={viewMode === 'gallery' ? 'active' : ''}
            title="Gallery view"
            onClick={() => onViewModeChange('gallery')}
          >
            <Icon name="grid" size={15} />
          </button>
        </span>
      </div>
      <div className={`pane-scroll${selectMode ? ' selecting' : ''}`}>
        {notes.length === 0 && (
          <div className="empty-state">{searching ? 'No Results' : 'No Notes'}</div>
        )}
        {!isDeletedView && pinned.length > 0 && (
          <>
            <div className="note-group-label">
              <Icon name="pin" size={13} filled /> Pinned
            </div>
            {renderNotes(pinned)}
            {others.length > 0 && <div className="note-group-label">Notes</div>}
          </>
        )}
        {renderNotes(others)}
      </div>
      {selectMode && (
        <div className="bulk-bar">
          <span>{picked.size} selected</span>
          <select
            value=""
            disabled={picked.size === 0}
            onChange={(e) => {
              if (!e.target.value) return
              onBulkMove([...picked], e.target.value)
              exitSelect()
            }}
          >
            <option value="">Move to…</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
          <button
            className="bulk-tag"
            disabled={picked.size === 0}
            onClick={() => {
              onBulkTag([...picked])
              exitSelect()
            }}
          >
            # Tag
          </button>
          <button
            className="bulk-delete"
            disabled={picked.size === 0}
            onClick={() => {
              onBulkDelete([...picked])
              exitSelect()
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}
