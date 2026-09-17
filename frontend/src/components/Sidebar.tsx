/**
 * The conversation list. New chat, switch, delete, and a search that narrows
 * the list in place to conversations whose title or any message mentions the
 * keyword.
 *
 * A row is a `div` holding a link plus a delete button rather than a link with a
 * button inside it - interactive content cannot nest inside an anchor, and the
 * delete button has to be a real button to be reachable by keyboard.
 */

import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router'

import type { Conversation } from '../api/types'
import {
  BookIcon,
  BookmarkIcon,
  MessageIcon,
  PanelIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
} from './icons'

export interface SidebarProps {
  conversations: Conversation[]
  activeId: string | null
  collapsed: boolean
  onToggle(): void
  onDelete(conversation: Conversation): void
  /** The keyword the list above is filtered by; '' is no filter. */
  search: string
  onSearchChange(search: string): void
}

export function Sidebar({
  conversations,
  activeId,
  collapsed,
  onToggle,
  onDelete,
  search,
  onSearchChange,
}: SidebarProps) {
  const [searchOpen, setSearchOpen] = useState(false)
  const searchInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // The row only exists while open, so this focuses it on open - and the
    // expand-then-focus path when the button was pressed while collapsed.
    if (searchOpen) {
      searchInput.current?.focus()
    }
  }, [searchOpen, collapsed])

  function closeSearch() {
    setSearchOpen(false)
    onSearchChange('')
  }

  function toggleSearch() {
    if (!searchOpen) {
      // From the collapsed rail, the search needs a wide sidebar to live in;
      // expanding is part of opening it.
      if (collapsed) {
        onToggle()
      }
      setSearchOpen(true)
      return
    }
    if (search === '') {
      setSearchOpen(false)
    } else {
      searchInput.current?.focus()
    }
  }

  return (
    <aside className={`sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="sidebar-top">
        {!collapsed && <span className="sidebar-logo">Jarvis</span>}
        <button
          type="button"
          className={`icon-button${searchOpen ? ' is-on' : ''}`}
          title="搜索对话"
          aria-expanded={searchOpen}
          onClick={toggleSearch}
        >
          <SearchIcon size={17} />
        </button>
        <button
          type="button"
          className="icon-button"
          title={collapsed ? '展开侧栏' : '收起侧栏'}
          onClick={onToggle}
        >
          <PanelIcon size={17} />
        </button>
      </div>

      {searchOpen && !collapsed && (
        <div className="sidebar-search">
          <input
            ref={searchInput}
            type="text"
            value={search}
            placeholder="搜索对话内容…"
            onChange={(event) => onSearchChange(event.target.value)}
            onKeyDown={(event) => {
              // Escape lets go of the search: first the keyword, then the row.
              if (event.key === 'Escape') {
                if (search !== '') {
                  onSearchChange('')
                } else {
                  closeSearch()
                }
              }
            }}
          />
        </div>
      )}

      <div className="sidebar-section">
        <NavLink to="/" className="sidebar-item" title="新对话" end>
          <PlusIcon size={17} />
          {!collapsed && <span className="label">新对话</span>}
        </NavLink>
      </div>

      {!collapsed && <div className="sidebar-label">{search !== '' ? '搜索结果' : '最近'}</div>}

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
        {!collapsed && search !== '' && conversations.length === 0 && (
          <div className="sidebar-empty">没有找到包含「{search}」的对话</div>
        )}
      </div>

      <div className="sidebar-bottom">
        <NavLink to="/knowledge" className="sidebar-item" title="知识库">
          <BookIcon size={17} />
          {!collapsed && <span className="label">知识库</span>}
        </NavLink>
        <NavLink to="/memories" className="sidebar-item" title="记忆">
          <BookmarkIcon size={17} />
          {!collapsed && <span className="label">记忆</span>}
        </NavLink>
        <NavLink to="/setup" className="sidebar-item" title="设置">
          <PanelIcon size={17} />
          {!collapsed && <span className="label">设置</span>}
        </NavLink>
      </div>
    </aside>
  )
}
