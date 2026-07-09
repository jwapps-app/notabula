import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { FolderOut, SharedFolder, SmartView, TagOut } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { APP_NAME } from '../constants/branding'
import Icon from './Icon'
import type { IconName } from './Icon'

export type FolderSelection =
  | { kind: 'all' }
  | { kind: 'view'; view: SmartView }
  | { kind: 'folder'; id: string }
  | { kind: 'tag'; name: string }
  | { kind: 'shared' }
  | { kind: 'sharedFolder'; id: string }
  | { kind: 'deleted' }

/** Flatten the folder forest into render order with depths (parents first,
 * children indented beneath). Orphaned parents fall back to top level. */
function folderTree(folders: FolderOut[]): { folder: FolderOut; depth: number }[] {
  const ids = new Set(folders.map((f) => f.id))
  const children = new Map<string | null, FolderOut[]>()
  for (const f of folders) {
    const parent = f.parent_id && ids.has(f.parent_id) ? f.parent_id : null
    children.set(parent, [...(children.get(parent) ?? []), f])
  }
  const out: { folder: FolderOut; depth: number }[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const f of children.get(parent) ?? []) {
      out.push({ folder: f, depth })
      walk(f.id, depth + 1)
    }
  }
  walk(null, 0)
  return out
}

export const SMART_VIEWS: { view: SmartView; label: string; icon: IconName }[] = [
  { view: 'media', label: 'Media', icon: 'image' },
  { view: 'links', label: 'Links', icon: 'link' },
  { view: 'tasks', label: 'Open Tasks', icon: 'check-square' },
  { view: 'locked', label: 'Locked', icon: 'lock' },
  { view: 'recent', label: 'Last 7 Days', icon: 'clock' },
]

interface Props {
  folders: FolderOut[]
  tags: TagOut[]
  sharedFolders: SharedFolder[]
  hasSharedNotes: boolean
  selection: FolderSelection
  onSelect: (sel: FolderSelection) => void
  onNewFolder: () => void
  onNewSubfolder: (parent: FolderOut) => void
  onRenameFolder: (folder: FolderOut) => void
  onDeleteFolder: (folder: FolderOut) => void
  onShareFolder: (folder: FolderOut) => void
}

export default function Sidebar({
  folders,
  tags,
  sharedFolders,
  hasSharedNotes,
  selection,
  onSelect,
  onNewFolder,
  onNewSubfolder,
  onRenameFolder,
  onDeleteFolder,
  onShareFolder,
}: Props) {
  const { user, logout } = useAuth()
  const [tagsOpen, setTagsOpen] = useState(
    () => localStorage.getItem('tagsCollapsed') !== '1',
  )
  const toggleTags = () => {
    setTagsOpen((open) => {
      localStorage.setItem('tagsCollapsed', open ? '1' : '0')
      return !open
    })
  }

  return (
    <div className="pane pane-sidebar">
      <div className="pane-header">
        <h2>Folders</h2>
        <button className="icon-btn" title="New folder" onClick={onNewFolder}>
          <Icon name="plus" size={18} />
        </button>
      </div>
      <div className="pane-scroll">
        <div className="sidebar-section">{APP_NAME}</div>
        <button
          className={`folder-row${selection.kind === 'all' ? ' selected' : ''}`}
          onClick={() => onSelect({ kind: 'all' })}
        >
          <span className="glyph">
            <Icon name="archive" size={16} />
          </span>
          <span className="name">All Notes</span>
          <span className="count">
            {folders.reduce((sum, f) => sum + f.note_count, 0)}
          </span>
        </button>
        {folderTree(folders).map(({ folder: f, depth }) => (
          <button
            key={f.id}
            className={`folder-row${
              selection.kind === 'folder' && selection.id === f.id ? ' selected' : ''
            }`}
            style={depth ? { paddingLeft: 12 + depth * 18 } : undefined}
            onClick={() => onSelect({ kind: 'folder', id: f.id })}
          >
            <span className="glyph">
              <Icon name="folder" size={16} />
            </span>
            <span className="name">{f.name}</span>
            <span
              className="row-action"
              title="New subfolder"
              onClick={(e) => {
                e.stopPropagation()
                onNewSubfolder(f)
              }}
            >
              <Icon name="plus" size={14} />
            </span>
            <span
              className="row-action"
              title="Share folder"
              onClick={(e) => {
                e.stopPropagation()
                onShareFolder(f)
              }}
            >
              <Icon name="users" size={14} />
            </span>
            {!f.is_default && (
              <>
                <span
                  className="row-action"
                  title="Rename"
                  onClick={(e) => {
                    e.stopPropagation()
                    onRenameFolder(f)
                  }}
                >
                  <Icon name="edit" size={14} />
                </span>
                <span
                  className="row-action"
                  title="Delete folder"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDeleteFolder(f)
                  }}
                >
                  <Icon name="trash" size={14} />
                </span>
              </>
            )}
            <span className="count">{f.note_count}</span>
          </button>
        ))}

        <div className="sidebar-section">Smart Views</div>
        {SMART_VIEWS.map((sv) => (
          <button
            key={sv.view}
            className={`folder-row${
              selection.kind === 'view' && selection.view === sv.view ? ' selected' : ''
            }`}
            onClick={() => onSelect({ kind: 'view', view: sv.view })}
          >
            <span className="glyph">
              <Icon name={sv.icon} size={16} />
            </span>
            <span className="name">{sv.label}</span>
          </button>
        ))}

        {tags.length > 0 && (
          <>
            <button className="sidebar-section section-toggle" onClick={toggleTags}>
              <span className="caret">{tagsOpen ? '▾' : '▸'}</span> Tags
              {!tagsOpen && <span className="muted-count"> ({tags.length})</span>}
            </button>
            {tagsOpen &&
              tags.map((t) => (
                <button
                  key={t.id}
                  className={`folder-row${
                    selection.kind === 'tag' && selection.name === t.name
                      ? ' selected'
                      : ''
                  }`}
                  onClick={() => onSelect({ kind: 'tag', name: t.name })}
                >
                  <span className="glyph">
                    <Icon name="hash" size={15} />
                  </span>
                  <span className="name">{t.name}</span>
                  <span className="count">{t.note_count}</span>
                </button>
              ))}
          </>
        )}

        {(sharedFolders.length > 0 || hasSharedNotes) && (
          <>
            <div className="sidebar-section">Shared with Me</div>
            {sharedFolders.map((f) => (
              <button
                key={f.id}
                className={`folder-row${
                  selection.kind === 'sharedFolder' && selection.id === f.id
                    ? ' selected'
                    : ''
                }`}
                onClick={() => onSelect({ kind: 'sharedFolder', id: f.id })}
              >
                <span className="glyph">
                  <Icon name="users" size={16} />
                </span>
                <span className="name">{f.name}</span>
                <span className="count">{f.owner_name}</span>
              </button>
            ))}
            {hasSharedNotes && (
              <button
                className={`folder-row${
                  selection.kind === 'shared' ? ' selected' : ''
                }`}
                onClick={() => onSelect({ kind: 'shared' })}
              >
                <span className="glyph">
                  <Icon name="users" size={16} />
                </span>
                <span className="name">Shared Notes</span>
              </button>
            )}
          </>
        )}

        <div className="sidebar-section">More</div>
        <button
          className={`folder-row${selection.kind === 'deleted' ? ' selected' : ''}`}
          onClick={() => onSelect({ kind: 'deleted' })}
        >
          <span className="glyph">
            <Icon name="trash" size={16} />
          </span>
          <span className="name">Recently Deleted</span>
        </button>
      </div>
      <div className="sidebar-footer">
        <span>{user?.name}</span>
        <span className="footer-actions">
          <Link to="/settings" title="Settings" className="footer-link">
            <Icon name="settings" size={22} />
          </Link>
          <button title="Sign out" onClick={() => void logout()}>
            <Icon name="power" size={20} />
          </button>
        </span>
      </div>
    </div>
  )
}
