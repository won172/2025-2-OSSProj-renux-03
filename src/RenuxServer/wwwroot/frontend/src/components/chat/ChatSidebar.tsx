import { type RefObject, useEffect, useMemo, useState } from 'react'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import AppNav from '../AppNav'
import { filterChatEntries, formatChatTime, groupChatEntries, type ChatListEntry } from '../../chat/chatList'

interface ChatSidebarProps {
  sidebarRef: RefObject<HTMLElement | null>
  entries: ChatListEntry[]
  selectedChatId: string | null
  isMobileLayout: boolean
  isOpen: boolean
  /** 스트리밍 중에는 대화 전환·삭제를 막아 진행 중인 답변을 잃지 않게 한다. */
  busy: boolean
  isAuthenticated: boolean
  onNewChat: () => void
  onSelectChat: (chatId: string) => void
  onRenameChat: (entry: ChatListEntry) => void
  onDeleteChat: (entry: ChatListEntry) => void
  onLogin: () => void
  onSignup: () => void
}

const ChatSidebar = ({
  sidebarRef,
  entries,
  selectedChatId,
  isMobileLayout,
  isOpen,
  busy,
  isAuthenticated,
  onNewChat,
  onSelectChat,
  onRenameChat,
  onDeleteChat,
  onLogin,
  onSignup,
}: ChatSidebarProps) => {
  const [query, setQuery] = useState('')
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const groups = useMemo(
    () => groupChatEntries(filterChatEntries(entries, query)),
    [entries, query],
  )
  const totalVisible = groups.reduce((sum, group) => sum + group.entries.length, 0)

  // 행 메뉴는 바깥을 누르면 닫는다 — 열린 메뉴가 다른 대화를 가리지 않도록.
  useEffect(() => {
    if (!openMenuId) return
    const close = () => setOpenMenuId(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [openMenuId])

  return (
    <aside
      ref={sidebarRef}
      id="chat-navigation-drawer"
      className="ch-sidebar"
      role={isMobileLayout ? 'dialog' : undefined}
      aria-label="대화 목록 및 계정"
      aria-modal={isMobileLayout && isOpen ? true : undefined}
      aria-hidden={isMobileLayout && !isOpen ? true : undefined}
      inert={isMobileLayout && !isOpen}
      tabIndex={isMobileLayout ? -1 : undefined}
    >
      <div className="ch-sidebar__top">
        <div className="ch-brand">
          <img src={dongddokiLogo} alt="" className="ch-brand__logo" />
          <span className="ch-brand__name">동국대학교 동똑이</span>
        </div>

        <AppNav />

        <button type="button" className="ch-newchat" onClick={onNewChat} disabled={busy}>
          <span aria-hidden="true">+</span> 새 대화
        </button>

        {entries.length > 0 && (
          <div className="ch-search">
            <span className="ch-search__icon" aria-hidden="true">🔍</span>
            <input
              type="search"
              className="ch-search__input"
              placeholder="대화 검색"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="대화 제목·내용 검색"
            />
          </div>
        )}
      </div>

      <nav className="ch-chatlist" aria-label="최근 대화">
        {entries.length === 0 ? (
          <p className="ch-chatlist__empty">아직 대화가 없습니다.<br />무엇이든 물어보며 시작해보세요.</p>
        ) : totalVisible === 0 ? (
          <p className="ch-chatlist__empty">“{query.trim()}”와 일치하는 대화가 없습니다.</p>
        ) : (
          groups.map((group) => (
            <div key={group.bucket}>
              <p className="ch-chatlist__group">{group.label}</p>
              <ul className="ch-chatlist__items">
                {group.entries.map((entry) => (
                  <li
                    key={entry.id}
                    className={`ch-chatrow ${selectedChatId === entry.id ? 'ch-chatrow--active' : ''}`}
                  >
                    <button
                      type="button"
                      className="ch-chatrow__open"
                      onClick={() => onSelectChat(entry.id)}
                      disabled={busy}
                      aria-current={selectedChatId === entry.id ? 'page' : undefined}
                    >
                      <span className="ch-chatrow__title">{entry.title}</span>
                      <span className="ch-chatrow__preview">
                        {entry.preview || '메시지가 없습니다'}
                        {entry.updatedAt && (
                          <>
                            {' · '}
                            <span className="ch-chatrow__time">{formatChatTime(entry.updatedAt)}</span>
                          </>
                        )}
                      </span>
                    </button>

                    <div className="ch-menu-anchor" onClick={(event) => event.stopPropagation()}>
                      <button
                        type="button"
                        className="ch-chatrow__menu-btn"
                        aria-label={`${entry.title} 대화 관리`}
                        aria-haspopup="menu"
                        aria-expanded={openMenuId === entry.id}
                        onClick={() => setOpenMenuId(openMenuId === entry.id ? null : entry.id)}
                      >
                        ⋯
                      </button>
                      {openMenuId === entry.id && (
                        <div className="ch-menu ch-menu--row" role="menu">
                          <button
                            type="button"
                            className="ch-menu__item"
                            role="menuitem"
                            onClick={() => { setOpenMenuId(null); onRenameChat(entry) }}
                          >
                            이름 변경
                          </button>
                          <button
                            type="button"
                            className="ch-menu__item ch-menu__item--danger"
                            role="menuitem"
                            disabled={busy}
                            onClick={() => { setOpenMenuId(null); onDeleteChat(entry) }}
                          >
                            삭제
                          </button>
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </nav>

      {!isAuthenticated && (
        <div className="ch-sidebar__bottom">
          <div className="ch-guestnote">
            <b>게스트로 이용 중</b>이에요. 대화는 이 기기에만 저장되고 브라우저 데이터를 지우면 사라집니다.
            <div className="ch-guestnote__actions">
              <button type="button" className="ch-btn ch-btn--sm" onClick={onLogin}>로그인</button>
              <button type="button" className="ch-btn ch-btn--sm ch-btn--primary" onClick={onSignup}>회원가입</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}

export default ChatSidebar
