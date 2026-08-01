import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  buildNotificationQuestion,
  countUnreadGroups,
  groupNotifications,
  type NotificationGroup,
  type NotificationUrgency,
} from '../../chat/notifications'
import type { UserNotification } from '../../types/notification'

interface NotificationBellProps {
  notifications: UserNotification[]
  /** 관심 주제를 하나도 켜지 않았으면 '알림이 없는 이유'가 다르다. */
  hasEnabledTopics: boolean
  lastSyncedAt: Date | null
  syncing: boolean
  onMarkRead: (notificationIds: string[]) => void
  onMarkAllRead: () => void
  onDelete: (notificationIds: string[]) => void
  /** 벨을 열 때 오래된 목록을 갱신한다. */
  onOpen: () => void
  onAsk: (question: string) => void
}

const URGENCY_BADGE: Record<NotificationUrgency, string> = {
  overdue: 'ch-badge--neutral',
  today: 'ch-badge--danger',
  urgent: 'ch-badge--danger',
  soon: 'ch-badge--warn',
  later: 'ch-badge--info',
}

const MAX_VISIBLE = 8

const NotificationBell = ({
  notifications,
  hasEnabledTopics,
  lastSyncedAt,
  syncing,
  onMarkRead,
  onMarkAllRead,
  onDelete,
  onOpen,
  onAsk,
}: NotificationBellProps) => {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const groups = useMemo(() => groupNotifications(notifications), [notifications])
  const unreadCount = useMemo(() => countUnreadGroups(groups), [groups])
  const visible = groups.slice(0, MAX_VISIBLE)

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: MouseEvent) => {
      if (!anchorRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); return }
      if (!listRef.current) return

      // 방향키로 항목 사이를 이동한다 — 마우스 없이도 알림을 훑을 수 있게.
      const items = Array.from(listRef.current.querySelectorAll<HTMLElement>('[data-notif-item]'))
      if (items.length === 0) return
      const current = items.indexOf(document.activeElement as HTMLElement)

      if (event.key === 'ArrowDown') {
        event.preventDefault()
        items[current < 0 ? 0 : Math.min(current + 1, items.length - 1)].focus()
      } else if (event.key === 'ArrowUp') {
        event.preventDefault()
        items[current <= 0 ? 0 : current - 1].focus()
      } else if (event.key === 'Home') {
        event.preventDefault()
        items[0].focus()
      } else if (event.key === 'End') {
        event.preventDefault()
        items[items.length - 1].focus()
      }
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const toggleOpen = () => {
    const next = !open
    setOpen(next)
    if (next) onOpen()
  }

  /** 알림을 열면 읽음으로 처리하고, 원문이 있으면 원문·없으면 챗봇 질문으로 잇는다. */
  const openGroup = (group: NotificationGroup) => {
    if (group.hasUnread) onMarkRead(group.ids)
    setOpen(false)

    const url = group.primary.url?.trim()
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer')
      return
    }
    onAsk(buildNotificationQuestion(group.primary))
  }

  return (
    <div className="ch-menu-anchor" ref={anchorRef}>
      <button
        type="button"
        className="ch-bell"
        aria-label={unreadCount > 0 ? `알림 ${unreadCount}건 읽지 않음` : '알림'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggleOpen}
      >
        <span aria-hidden="true">🔔</span>
        {unreadCount > 0 && <span className="ch-bell__count">{unreadCount > 99 ? '99+' : unreadCount}</span>}
      </button>

      {/* 배지 숫자 변화를 스크린리더에도 알린다. */}
      <span className="ch-visually-hidden" role="status" aria-live="polite">
        {unreadCount > 0 ? `읽지 않은 마감 알림 ${unreadCount}건` : '읽지 않은 알림 없음'}
      </span>

      {open && (
        <div className="ch-menu ch-menu--right ch-menu--notifications" role="menu">
          <div className="ch-menu__header ch-notif__head">
            <div>
              <span className="ch-menu__name">알림</span>
              <span className="ch-menu__sub">
                {syncing
                  ? '새 알림을 확인하는 중…'
                  : unreadCount > 0
                    ? `읽지 않은 마감 ${unreadCount}건`
                    : '읽지 않은 알림이 없습니다'}
              </span>
            </div>
            {unreadCount > 0 && (
              <button type="button" className="ch-btn ch-btn--sm ch-btn--ghost" onClick={onMarkAllRead}>
                모두 읽음
              </button>
            )}
          </div>

          <div ref={listRef}>
            {visible.length === 0 ? (
              <div className="ch-notif__empty">
                {!hasEnabledTopics ? (
                  <>
                    <p>관심 주제가 모두 꺼져 있어요.</p>
                    <p className="ch-notif__empty-sub">주제를 켜면 마감 전에 미리 알려드려요.</p>
                    <button
                      type="button"
                      className="ch-btn ch-btn--sm ch-btn--primary"
                      onClick={() => { setOpen(false); navigate('/settings') }}
                    >
                      관심 주제 켜기
                    </button>
                  </>
                ) : (
                  <>
                    <p>다가오는 마감이 없어요.</p>
                    <p className="ch-notif__empty-sub">새 마감이 생기면 여기에 알려드릴게요.</p>
                  </>
                )}
              </div>
            ) : (
              visible.map((group) => (
                <div
                  key={group.primary.id}
                  className={`ch-notif ${group.hasUnread ? 'ch-notif--unread' : ''}`}
                >
                  <button
                    type="button"
                    className="ch-notif__open"
                    data-notif-item
                    role="menuitem"
                    onClick={() => openGroup(group)}
                    aria-label={`${group.dDayLabel} ${group.primary.title}${group.primary.url ? ' — 원문 열기' : ' — 챗봇에 물어보기'}`}
                  >
                    <span className="ch-notif__meta">
                      <span className={`ch-badge ${URGENCY_BADGE[group.urgency]}`}>{group.dDayLabel}</span>
                      <span>{group.primary.topicLabel}</span>
                      {group.ids.length > 1 && <span>알림 {group.ids.length}회</span>}
                    </span>
                    <span className="ch-notif__title">{group.primary.title}</span>
                    <span className="ch-notif__cta">
                      {group.primary.url ? '원문 보기 →' : '동똑이에게 물어보기 →'}
                    </span>
                  </button>

                  <button
                    type="button"
                    className="ch-notif__remove"
                    aria-label={`${group.primary.title} 알림 삭제`}
                    onClick={(event) => { event.stopPropagation(); onDelete(group.ids) }}
                  >
                    ×
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="ch-menu__divider" />
          <button
            type="button"
            className="ch-menu__item"
            role="menuitem"
            onClick={() => { setOpen(false); navigate('/settings') }}
          >
            알림 설정
            {lastSyncedAt && (
              <span className="ch-notif__synced">
                {lastSyncedAt.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })} 확인
              </span>
            )}
          </button>
        </div>
      )}
    </div>
  )
}

export default NotificationBell
