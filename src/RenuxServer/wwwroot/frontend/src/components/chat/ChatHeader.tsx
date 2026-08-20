import { type RefObject, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import NotificationBell from './NotificationBell'
import type { UserRole } from '../../types/auth'
import type { UserNotification } from '../../types/notification'

interface ChatHeaderProps {
  title: string
  /** 대화 화면에서만 제목 변경을 허용한다(홈에는 바꿀 제목이 없다). */
  onRenameTitle?: () => void
  /** 드로어를 닫을 때 포커스를 여기로 돌려준다 — 키보드 사용자가 위치를 잃지 않도록. */
  menuButtonRef: RefObject<HTMLButtonElement | null>
  isAuthenticated: boolean
  userName: string | null
  departmentName: string | null
  role: UserRole
  notifications: UserNotification[]
  hasEnabledTopics: boolean
  lastSyncedAt: Date | null
  syncingNotifications: boolean
  onMarkNotificationRead: (notificationIds: string[]) => void
  onMarkAllNotificationsRead: () => void
  onDeleteNotifications: (notificationIds: string[]) => void
  onRefreshNotifications: () => void
  onAskFromNotification: (question: string) => void
  onOpenSidebar: () => void
  isSidebarOpen: boolean
  onLogin: () => void
  onSignup: () => void
  onLogout: () => void
}

const ROLE_LABEL: Record<UserRole, string> = {
  STUDENT: '일반학생',
  DEPARTMENT_COUNCIL: '학생회',
  UNIVERSITY_COUNCIL: '총학생회',
}

/** 역할에 따라 열 수 있는 관리자 콘솔 경로. 학생은 없다. */
const ADMIN_ROUTE: Partial<Record<UserRole, { path: string; label: string }>> = {
  UNIVERSITY_COUNCIL: { path: '/admin/dashboard', label: '관리자 콘솔' },
  DEPARTMENT_COUNCIL: { path: '/admin/department', label: '학과 콘솔' },
}

const ChatHeader = ({
  title,
  onRenameTitle,
  menuButtonRef,
  isAuthenticated,
  userName,
  departmentName,
  role,
  notifications,
  hasEnabledTopics,
  lastSyncedAt,
  syncingNotifications,
  onMarkNotificationRead,
  onMarkAllNotificationsRead,
  onDeleteNotifications,
  onRefreshNotifications,
  onAskFromNotification,
  onOpenSidebar,
  isSidebarOpen,
  onLogin,
  onSignup,
  onLogout,
}: ChatHeaderProps) => {
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const handlePointerDown = (event: MouseEvent) => {
      if (!anchorRef.current?.contains(event.target as Node)) setMenuOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

  const adminRoute = isAuthenticated ? ADMIN_ROUTE[role] : undefined
  const displayName = userName?.trim() || '관리자'
  const initial = displayName.slice(0, 1)

  return (
    <header className="ch-header">
      <button
        ref={menuButtonRef}
        type="button"
        className="ch-header__menu-btn"
        onClick={onOpenSidebar}
        aria-label="대화 목록 열기"
        aria-expanded={isSidebarOpen}
        aria-controls="chat-navigation-drawer"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>

      <div className="ch-header__title">
        <h1>{title}</h1>
        {onRenameTitle && (
          <button type="button" className="ch-header__rename" onClick={onRenameTitle} aria-label="대화 이름 변경">
            ✏️
          </button>
        )}
      </div>

      <div className="ch-header__actions">
        {isAuthenticated ? (
          <>
            <NotificationBell
              notifications={notifications}
              hasEnabledTopics={hasEnabledTopics}
              lastSyncedAt={lastSyncedAt}
              syncing={syncingNotifications}
              onMarkRead={onMarkNotificationRead}
              onMarkAllRead={onMarkAllNotificationsRead}
              onDelete={onDeleteNotifications}
              onOpen={onRefreshNotifications}
              onAsk={onAskFromNotification}
            />

            <div className="ch-menu-anchor" ref={anchorRef}>
              <button
                type="button"
                className="ch-avatar"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <span className="ch-avatar__initial" aria-hidden="true">{initial}</span>
                <span className="ch-avatar__label">{displayName}</span>
                <span className="ch-avatar__caret" aria-hidden="true">▾</span>
              </button>

              {menuOpen && (
                <div className="ch-menu ch-menu--right" role="menu">
                  <div className="ch-menu__header">
                    <span className="ch-menu__name">
                      {displayName} <span className="ch-badge ch-badge--role">{ROLE_LABEL[role]}</span>
                    </span>
                    <span className="ch-menu__sub">{departmentName?.trim() || '동국대학교'}</span>
                  </div>
                  <button
                    type="button"
                    className="ch-menu__item"
                    role="menuitem"
                    onClick={() => { setMenuOpen(false); navigate('/settings') }}
                  >
                    알림 설정
                  </button>
                  {adminRoute && (
                    <button
                      type="button"
                      className="ch-menu__item"
                      role="menuitem"
                      onClick={() => { setMenuOpen(false); navigate(adminRoute.path) }}
                    >
                      {adminRoute.label}
                    </button>
                  )}
                  <div className="ch-menu__divider" />
                  <button
                    type="button"
                    className="ch-menu__item ch-menu__item--danger"
                    role="menuitem"
                    onClick={() => { setMenuOpen(false); onLogout() }}
                  >
                    로그아웃
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            <button type="button" className="ch-btn ch-btn--sm" onClick={onLogin}>로그인</button>
            <button type="button" className="ch-btn ch-btn--sm ch-btn--primary" onClick={onSignup}>회원가입</button>
          </>
        )}
      </div>
    </header>
  )
}

export default ChatHeader
