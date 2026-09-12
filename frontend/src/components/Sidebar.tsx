/**
 * The conversation list. Deliberately plain for this slice: new chat, switch,
 * delete. Renaming, pinning and search come later.
 *
 * A row is a `div` holding a link plus a delete button rather than a link with a
 * button inside it - interactive content cannot nest inside an anchor, and the
 * delete button has to be a real button to be reachable by keyboard.
 */

import { NavLink } from 'react-router'

import type { Conversation } from '../api/types'
import { MessageIcon, PanelIcon, PlusIcon, TrashIcon } from './icons'

export interface SidebarProps {
  conversations: Conversation[]
  activeId: string | null
  collapsed: boolean
  onToggle(): void
  onDelete(conversation: Conversation): void
}

export function Sidebar({ conversations, activeId, collapsed, onToggle, onDelete }: SidebarProps) {
  return (
    <aside className={`sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="sidebar-top">
        {!collapsed && <span className="sidebar-logo">Jarvis</span>}
        <button
          type="button"
          className="icon-button"
          title={collapsed ? '展开侧栏' : '收起侧栏'}
          onClick={onToggle}
        >
          <PanelIcon size={17} />
        </button>
      </div>

      <div className="sidebar-section">
        <NavLink to="/" className="sidebar-item" title="新对话" end>
          <PlusIcon size={17} />
          {!collapsed && <span className="label">新对话</span>}
        </NavLink>
      </div>

      {!collapsed && <div className="sidebar-label">最近</div>}

      <div className="sidebar-list">
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`sidebar-item sidebar-row${conversation.id === activeId ? ' is-active' : ''}`}
          >
            <NavLink
              to={`/c/${conversation.id}`}
              className="sidebar-link"
              title={conversation.title}
            >
              <MessageIcon size={17} />
              {!collapsed && <span className="label">{conversation.title}</span>}
            </NavLink>
            {!collapsed && (
              <button
                type="button"
                className="icon-button row-action"
                title="删除"
                onClick={() => onDelete(conversation)}
              >
                <TrashIcon size={15} />
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="sidebar-bottom">
        <NavLink to="/setup" className="sidebar-item" title="设置">
          <PanelIcon size={17} />
          {!collapsed && <span className="label">设置</span>}
        </NavLink>
      </div>
    </aside>
  )
}
