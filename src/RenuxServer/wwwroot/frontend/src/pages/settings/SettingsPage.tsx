import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useBlocker, useNavigate } from 'react-router-dom'
import { apiFetch, type ApiError } from '../../api/client'
import AppNav from '../../components/AppNav'
import Pagination from '../../components/Pagination'
import { cleanDeadlineSnippet, pageSlice, preferenceKey } from '../../chat/settingsState'
import { countUnreadGroups, formatDDay, getDDay, groupNotifications } from '../../chat/notifications'
import ConfirmDialog from '../../components/chat/ConfirmDialog'
import type {
  DeadlineItem,
  NotificationPreference,
  NotificationPreferenceResponse,
  NotificationTopic,
  UserNotification,
} from '../../types/notification'

const TOPICS: Array<{ topic: NotificationTopic; label: string; description: string }> = [
  { topic: 'scholarship', label: '장학', description: '장학금 신청, 선발, 서류 제출 마감' },
  { topic: 'course_registration', label: '수강신청', description: '수강신청, 정정, 취소 기간' },
  { topic: 'tuition_payment', label: '등록/납부', description: '등록금 납부와 추가 등록 안내' },
  { topic: 'document_submission', label: '서류 제출', description: '신청서, 증빙, 비교과 접수 마감' },
  { topic: 'academic_schedule', label: '학사일정', description: '개강, 종강, 시험, 주요 학사 기간' },
]

const REMIND_DAYS = [7, 1, 0]
// NotificationApis caps GET /notifications at 100, ordered by target date ascending.
const INBOX_LIMIT = 100
const INBOX_URL = `/notifications?includePast=true&limit=${INBOX_LIMIT}`
const SCREENS = [{ id: 'preferences', label: '관심 주제' }, { id: 'deadlines', label: '내 마감' }, { id: 'inbox', label: '알림함' }, { id: 'account', label: '계정' }] as const
type Screen = typeof SCREENS[number]['id']

const formatDate = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric' }).format(date)
}

const formatDateTime = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
}

const formatDday = (dDay: number) => {
  if (dDay === 0) return 'D-day'
  if (dDay > 0) return `D-${dDay}`
  return `D+${Math.abs(dDay)}`
}

const isApiError = (error: unknown): error is ApiError => error instanceof Error && 'status' in error

const SettingsPage = () => {
  const navigate = useNavigate()
  const [screen, setScreen] = useState<Screen>('preferences')
  const [savedPreferences, setSavedPreferences] = useState<NotificationPreference[]>([])
  const [notificationBusy, setNotificationBusy] = useState(false)
  const [deadlinePage, setDeadlinePage] = useState(0)
  const [notificationPage, setNotificationPage] = useState(0)
  const [preferences, setPreferences] = useState<NotificationPreference[]>([])
  const [deadlines, setDeadlines] = useState<DeadlineItem[]>([])
  const [notifications, setNotifications] = useState<UserNotification[]>([])
  const [preferencesLoading, setPreferencesLoading] = useState(true)
  const [preferencesError, setPreferencesError] = useState<string | null>(null)
  const [inboxLoading, setInboxLoading] = useState(true)
  const [inboxError, setInboxError] = useState<string | null>(null)
  const [deadlinesLoading, setDeadlinesLoading] = useState(true)
  const [deadlinesError, setDeadlinesError] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [needsLogin, setNeedsLogin] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [isDeletingAccount, setIsDeletingAccount] = useState(false)

  const dirty = preferenceKey(preferences) !== preferenceKey(savedPreferences)
  const blocker = useBlocker(dirty && !isDeletingAccount)
  useEffect(() => {
    if (!dirty || isDeletingAccount) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, isDeletingAccount])
  const deadlineSlice = pageSlice(deadlines, deadlinePage)
  const notificationSlice = pageSlice(notifications, notificationPage)
  const unreadDeadlines = countUnreadGroups(groupNotifications(notifications))

  const preferenceByTopic = useMemo(() => {
    const map = new Map<NotificationTopic, NotificationPreference>()
    preferences.forEach((preference) => map.set(preference.topic, preference))
    return map
  }, [preferences])

  const unreadCount = useMemo(() => notifications.filter((item) => !item.isRead).length, [notifications])

  // Each service owns its loading/error state; account access never depends on it.
  const loadPreferences = useCallback(async () => {
    setPreferencesLoading(true)
    setPreferencesError(null)
    try {
      const data = await apiFetch<NotificationPreferenceResponse>('/notifications/preferences')
      setPreferences(data.preferences ?? [])
      setSavedPreferences(data.preferences ?? [])
    } catch (loadError) {
      if (isApiError(loadError) && loadError.status === 401) setNeedsLogin(true)
      else setPreferencesError('관심 주제를 불러오지 못했습니다. 다시 불러온 후 설정을 저장할 수 있습니다.')
    } finally {
      setPreferencesLoading(false)
    }
  }, [])

  const loadInbox = useCallback(async () => {
    setInboxLoading(true)
    setInboxError(null)
    try {
      const data = await apiFetch<UserNotification[]>(INBOX_URL)
      setNotifications(Array.isArray(data) ? data : [])
    } catch (loadError) {
      if (isApiError(loadError) && loadError.status === 401) setNeedsLogin(true)
      else setInboxError('알림함을 불러오지 못했습니다.')
    } finally {
      setInboxLoading(false)
    }
  }, [])

  // 마감일은 RAG 후보 스캔이라 느리므로 별도로 불러와 해당 섹션만 로딩 표시한다.
  const loadDeadlines = useCallback(async () => {
    setDeadlinesLoading(true)
    setDeadlinesError(false)
    try {
      const deadlineData = await apiFetch<DeadlineItem[]>('/notifications/deadlines')
      setDeadlines(Array.isArray(deadlineData) ? deadlineData : [])
    } catch (loadError) {
      // Deadline failures stay within their screen; authorization still guards the page.
      setDeadlines([])
      if (isApiError(loadError) && loadError.status === 401) setNeedsLogin(true)
      else setDeadlinesError(true)
    } finally {
      setDeadlinesLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPreferences()
    void loadInbox()
    void loadDeadlines()
  }, [loadPreferences, loadInbox, loadDeadlines])

  const updatePreference = (topic: NotificationTopic, updater: (current: NotificationPreference) => NotificationPreference) => {
    setPreferences((current) =>
      current.map((preference) => (preference.topic === topic ? updater(preference) : preference)),
    )
  }

  const handleToggleTopic = (topic: NotificationTopic) => {
    updatePreference(topic, (current) => ({ ...current, enabled: !current.enabled }))
  }

  const handleToggleRemindDay = (topic: NotificationTopic, day: number) => {
    updatePreference(topic, (current) => {
      const set = new Set(current.remindDaysBefore)
      if (set.has(day)) set.delete(day)
      else set.add(day)
      return { ...current, remindDaysBefore: Array.from(set).sort((a, b) => b - a) }
    })
  }

  /** 주제를 하나씩 누르지 않아도 되게 — 처음 설정할 때와 잠시 끄고 싶을 때 모두 쓰인다. */
  const handleToggleAllTopics = (enabled: boolean) => {
    setPreferences((current) => current.map((preference) => ({ ...preference, enabled })))
  }

  /** 주제별로 지금 몇 건이 걸려 있는지 — 켜고 나서 무엇이 오는지 미리 보인다. */
  const deadlineCountByTopic = useMemo(() => {
    const counts = new Map<NotificationTopic, number>()
    deadlines.forEach((deadline) => {
      counts.set(deadline.topic, (counts.get(deadline.topic) ?? 0) + 1)
    })
    return counts
  }, [deadlines])

  const handleClearRead = async () => {
    if (notificationBusy) return
    setNotificationBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await apiFetch<{ deleted: number }>('/notifications/read', { method: 'DELETE' })
      setNotifications((current) => current.filter((notification) => !notification.isRead))
      setNotice(result.deleted > 0 ? `읽은 알림 ${result.deleted}개를 정리했습니다.` : '정리할 읽은 알림이 없습니다.')
    } catch {
      setError('읽은 알림을 정리하지 못했습니다.')
    } finally { setNotificationBusy(false) }
  }

  const handleMarkAllRead = async () => {
    if (notificationBusy) return
    setNotificationBusy(true)
    const previous = notifications
    setNotifications((current) => current.map((notification) => ({ ...notification, isRead: true })))
    try {
      await apiFetch<{ updated: number }>('/notifications/read-all', { method: 'POST' })
    } catch {
      setNotifications(previous)
      setError('알림을 모두 읽음으로 표시하지 못했습니다.')
    } finally { setNotificationBusy(false) }
  }

  const handleSave = async () => {
    if (preferencesLoading || preferencesError || isSaving) return
    setIsSaving(true)
    setError(null)
    setNotice(null)
    try {
      const data = await apiFetch<NotificationPreferenceResponse>('/notifications/preferences', {
        method: 'PUT',
        json: { preferences },
      })
      setPreferences(data.preferences ?? preferences)
      setSavedPreferences(data.preferences ?? preferences)
      setNotice('알림 설정을 저장했습니다.')
      void loadDeadlines()
    } catch {
      setError('알림 설정 저장에 실패했습니다.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleSync = async () => {
    setIsSyncing(true)
    setError(null)
    setNotice(null)
    try {
      const result = await apiFetch<{ created: number; upcomingMatched: number }>('/notifications/sync', { method: 'POST' })
      setNotice(`새 리마인드 ${result.created}회를 확인했습니다. 관심 마감 ${result.upcomingMatched}건이 매칭되었습니다.`)
      await loadInbox()
      void loadDeadlines()
    } catch {
      setError('마감 알림 동기화에 실패했습니다.')
    } finally {
      setIsSyncing(false)
    }
  }

  const handleMarkRead = async (notificationId: string) => {
    if (notificationBusy) return
    setNotificationBusy(true)
    try {
      const updated = await apiFetch<UserNotification>(`/notifications/${notificationId}/read`, { method: 'POST' })
      setNotifications((current) => current.map((item) => (item.id === notificationId ? updated : item)))
    } catch {
      setError('알림 읽음 처리에 실패했습니다.')
    } finally { setNotificationBusy(false) }
  }

  const closeDeleteDialog = () => {
    if (isDeletingAccount) return
    setDeleteDialogOpen(false)
    setDeletePassword('')
    setDeleteConfirmation('')
    setDeleteError(null)
  }

  const handleDeleteAccount = async () => {
    if (!deletePassword || deleteConfirmation.trim() !== '회원 탈퇴' || isDeletingAccount) return
    setIsDeletingAccount(true)
    setDeleteError(null)
    try {
      await apiFetch<{ message: string }>('/auth/account', {
        method: 'DELETE',
        json: { password: deletePassword, confirmation: deleteConfirmation },
      })
      try {
        window.localStorage.removeItem('renux-user-role')
      } catch {
        // 저장소 접근이 막혀 있어도 서버 계정 삭제는 완료됐다.
      }
      setDeleteDialogOpen(false)
      navigate('/', { replace: true })
      window.location.reload()
    } catch (deleteError) {
      const message = isApiError(deleteError)
        && typeof deleteError.details === 'object'
        && deleteError.details !== null
        && 'message' in deleteError.details
        ? String(deleteError.details.message)
        : '회원 탈퇴를 완료하지 못했습니다.'
      setDeleteError(message)
    } finally {
      setIsDeletingAccount(false)
    }
  }

  return (
    <div className="settings-page">
      <header className="settings-page__header">
        <div>
          <p className="settings-page__eyebrow">D-day 비서</p>
          <h1>설정</h1>
        </div>
        <div className="settings-page__actions">
          <button type="button" className="settings-page__back" onClick={() => navigate('/')}>
            홈으로
          </button>
          <button type="button" className="settings-page__back" onClick={() => navigate(-1)}>
            돌아가기
          </button>
        </div>
      </header>

      {error && <div role="alert" className="settings-alert settings-alert--danger">{error}</div>}
      {notice && <div role="status" className="settings-alert settings-alert--success">{notice}</div>}

      {needsLogin ? (
        <section className="settings-panel">
          <h2>로그인이 필요합니다</h2>
          <p>관심 주제와 알림 읽음 상태는 계정에 저장됩니다.</p>
          <button type="button" className="auth-submit settings-page__login" onClick={() => navigate('/auth/in')}>
            로그인하기
          </button>
        </section>
      ) : (
        <>
          <nav className="settings-nav" aria-label="설정 화면">
            {SCREENS.map((item) => <button key={item.id} type="button" aria-current={screen === item.id ? 'page' : undefined}
              aria-controls={`settings-${item.id}`} onClick={() => setScreen(item.id)}>{item.label}</button>)}
          </nav>
          {dirty && <p className="settings-dirty" role="status">저장하지 않은 관심 주제 변경이 있습니다. 다른 설정 화면에서도 유지됩니다.
            {screen !== 'preferences' && <button type="button" className="settings-page__back" onClick={() => setScreen('preferences')}>설정 저장하러 가기</button>}
          </p>}
          <section id="settings-preferences" hidden={screen !== 'preferences'}>

            <article className="settings-panel">
              {preferencesLoading && <p role="status">관심 주제를 불러오는 중입니다...</p>}
              {preferencesError && <div role="alert"><p>{preferencesError}</p><button type="button" className="settings-page__back" onClick={() => void loadPreferences()}>관심 주제 다시 시도</button></div>}
              <div className="settings-panel__header">
                <div>
                  <h2>관심 주제</h2>
                  <p>저장한 관심 주제와 리마인드 시점을 내 마감과 알림에 반영합니다. 변경 후 ‘설정 저장’을 눌러주세요.</p>
                </div>
                <div className="settings-topic-bulk">
                  <button
                    type="button"
                    className="settings-page__back"
                    onClick={() => handleToggleAllTopics(true)}
                    disabled={isSaving || preferencesLoading || !!preferencesError || preferences.every((preference) => preference.enabled)}
                  >
                    전체 켜기
                  </button>
                  <button
                    type="button"
                    className="settings-page__back"
                    onClick={() => handleToggleAllTopics(false)}
                    disabled={isSaving || preferencesLoading || !!preferencesError || preferences.every((preference) => !preference.enabled)}
                  >
                    전체 끄기
                  </button>
                  <button type="button" className="auth-submit settings-save-btn" onClick={handleSave} disabled={isSaving || preferencesLoading || !!preferencesError || !dirty}>
                    {isSaving ? '저장 중...' : preferencesLoading || preferencesError ? '설정 저장' : dirty ? '설정 저장' : '저장됨'}
                  </button>
                </div>
              </div>

              <div className="settings-topic-list">
                {TOPICS.map((topic) => {
                  const preference = preferenceByTopic.get(topic.topic)
                  const remindDays = new Set(preference?.remindDaysBefore ?? [])
                  return (
                    <div className="settings-topic" key={topic.topic}>
                      <label className="settings-topic__main">
                        <input
                          type="checkbox"
                          disabled={isSaving || preferencesLoading || !!preferencesError || !preference}
                          checked={preference?.enabled ?? false}
                          onChange={() => handleToggleTopic(topic.topic)}
                        />
                        <span>
                          <strong>
                            {topic.label}
                            {(deadlineCountByTopic.get(topic.topic) ?? 0) > 0 && (
                              <em className="settings-topic__count">
                                다가오는 마감 {deadlineCountByTopic.get(topic.topic)}건
                              </em>
                            )}
                          </strong>
                          <small>{topic.description}</small>
                        </span>
                      </label>
                      <div className="settings-topic__reminders" aria-label={`${topic.label} 리마인드 시점`}>
                        {REMIND_DAYS.map((day) => (
                          <label key={day}>
                            <input
                              type="checkbox"
                              checked={remindDays.has(day)}
                              onChange={() => handleToggleRemindDay(topic.topic, day)}
                              disabled={isSaving || preferencesLoading || !!preferencesError || !preference?.enabled}
                            />
                            <span>{day === 0 ? '당일' : `${day}일 전`}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </article>

          </section>

          <section id="settings-deadlines" className="settings-panel" hidden={screen !== 'deadlines'}>
            <div className="settings-panel__header">
              <div>
                <h2>내 마감일</h2>
                <p>관심 주제 기준으로 앞으로 다가오는 학교 공지와 학사일정을 모았습니다.</p>
              </div>
              <span className="settings-count">{deadlinesLoading ? '불러오는 중' : `${deadlines.length}개`}</span>
            </div>
            <div className="deadline-list">
              {deadlinesLoading ? (
                <p className="settings-empty">마감 정보를 불러오는 중...</p>
              ) : deadlinesError ? (
                <p className="settings-empty">마감 정보를 불러오지 못했습니다. <button type="button" className="settings-page__back" onClick={() => void loadDeadlines()}>다시 시도</button></p>
              ) : deadlines.length === 0 ? (
                <p className="settings-empty">켜둔 관심 주제에 해당하는 마감이 아직 없습니다.</p>
              ) : (
                deadlineSlice.items.map((deadline) => (
                  <article className="deadline-item" key={deadline.id}>
                    <div className="deadline-item__d-day">{formatDday(deadline.dDay)}</div>
                    <div className="deadline-item__body">
                      <div className="deadline-item__meta">
                        <span>{deadline.topicLabel}</span>
                        <span>{deadline.sourceLabel}</span>
                        <span>{formatDate(deadline.targetDate)}</span>
                      </div>
                      <h3>{deadline.title}</h3>
                      {cleanDeadlineSnippet(deadline.snippet) && <details className="settings-description"><summary>상세 설명</summary><p>{cleanDeadlineSnippet(deadline.snippet)}</p></details>}
                    </div>
                    {deadline.url && (
                      <a className="deadline-item__link" href={deadline.url} target="_blank" rel="noreferrer">
                        출처
                      </a>
                    )}
                  </article>
                ))
              )}
            </div>
            <Pagination {...deadlineSlice} total={deadlines.length} onChange={setDeadlinePage} />
          </section>

          <section id="settings-inbox" className="settings-panel" hidden={screen !== 'inbox'}>
            <div className="settings-panel__header">
              <div>
                <h2>알림함</h2>
                {!inboxLoading && !inboxError && <p>불러온 {notifications.length}회 중 읽지 않은 다가오는 마감 {unreadDeadlines}건 · 읽지 않은 리마인드 {unreadCount}회.</p>}
                <p>지난 마감을 포함하여 마감일이 빠른 순으로 최대 {INBOX_LIMIT}회만 조회합니다. 건수는 불러온 범위 기준이며 전체 이력이 아닙니다.</p>
                <p>벨은 다가오는 마감의 여러 리마인드를 1건으로 묶습니다. 별도 조회 범위 때문에 숫자가 다를 수 있습니다.</p>
                <p>‘모두 읽음’과 ‘읽은 알림 정리’는 현재 페이지 밖의 계정 알림에도 적용됩니다.</p>
                <button type="button" className="settings-sync-btn" onClick={handleSync} disabled={isSyncing || notificationBusy}>
                  {isSyncing ? '확인 중...' : '마감 알림 확인'}
                </button>
              </div>
              <div className="settings-topic-bulk">
                <span className="settings-count">{inboxLoading ? '불러오는 중' : inboxError ? '조회 실패' : `조회 ${notifications.length}회`}</span>
                {unreadCount > 0 && (
                  <button type="button" className="settings-page__back" onClick={handleMarkAllRead} disabled={notificationBusy || isSyncing}>
                    모두 읽음
                  </button>
                )}
                {notifications.some((notification) => notification.isRead) && (
                  <button type="button" className="settings-page__back" onClick={handleClearRead} disabled={notificationBusy || isSyncing}>
                    읽은 알림 정리
                  </button>
                )}
              </div>
            </div>
            {inboxError && <div role="alert"><p>{inboxError}</p><button type="button" className="settings-page__back" onClick={() => void loadInbox()}>알림함 다시 시도</button></div>}
            <div className="notification-list">
              {inboxLoading ? <p role="status">알림함을 불러오는 중입니다...</p> : inboxError ? null : notifications.length === 0 ? (
                <p className="settings-empty">아직 생성된 알림이 없습니다.</p>
              ) : (
                notificationSlice.items.map((notification) => (
                  <article className={`notification-item ${notification.isRead ? 'notification-item--read' : ''}`} key={notification.id}>
                    <div>
                      <div className="deadline-item__meta">
                        <span>{notification.topicLabel}</span>
                        <span>마감 {formatDate(notification.targetDate)} · {getDDay(notification.targetDate) === null ? '날짜 확인 필요' : formatDDay(getDDay(notification.targetDate)!)}</span>
                        <span>{formatDateTime(notification.createdTime)}</span>
                      </div>
                      <h3>{notification.title}</h3>
                      <details className="settings-description"><summary>알림 상세 · {notification.reminderDaysBefore === 0 ? '당일' : `${notification.reminderDaysBefore}일 전`} 리마인드</summary><p>{cleanDeadlineSnippet(notification.body) || '원문에서 자세한 내용을 확인하세요.'}</p></details>
                    </div>
                    <div className="notification-item__actions">
                      {notification.url && (
                        <a href={notification.url} target="_blank" rel="noreferrer">
                          출처
                        </a>
                      )}
                      {!notification.isRead && (
                        <button type="button" disabled={notificationBusy || isSyncing} onClick={() => handleMarkRead(notification.id)}>
                          읽음
                        </button>
                      )}
                    </div>
                  </article>
                ))
              )}
            </div>
            {!inboxLoading && !inboxError && <Pagination {...notificationSlice} total={notifications.length} onChange={setNotificationPage} />}
          </section>

          <section id="settings-account" className="settings-panel settings-panel--danger-zone" hidden={screen !== 'account'}>
            <div className="settings-panel__header">
              <div>
                <h2>계정 관리</h2>
                <p>회원 탈퇴 시 계정, 대화 기록, 알림 설정과 연결된 개인정보가 영구 삭제됩니다.</p>
              </div>
              <button
                type="button"
                className="settings-delete-account"
                onClick={() => setDeleteDialogOpen(true)}
              >
                회원 탈퇴
              </button>
            </div>
          </section>
        </>
      )}

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="저장하지 않은 변경"
        description="관심 주제 변경을 저장하지 않고 이동할까요? 머무르기를 선택하면 설정을 저장할 수 있습니다."
        confirmLabel="저장하지 않고 이동"
        cancelLabel="머무르기"
        onCancel={() => { if (blocker.state === 'blocked') blocker.reset() }}
        onConfirm={() => { if (blocker.state === 'blocked') blocker.proceed() }}
      />
      <ConfirmDialog
        open={deleteDialogOpen}
        title="회원 탈퇴"
        description="삭제 후에는 계정과 대화 기록을 복구할 수 없습니다. 현재 비밀번호와 확인 문구를 입력해주세요."
        confirmLabel="영구 삭제"
        tone="danger"
        busy={isDeletingAccount}
        confirmDisabled={!deletePassword || deleteConfirmation.trim() !== '회원 탈퇴'}
        onCancel={closeDeleteDialog}
        onConfirm={handleDeleteAccount}
      >
        {deleteError && <p role="alert" className="settings-alert settings-alert--danger">{deleteError}</p>}
        <label className="settings-delete-field">
          <span>현재 비밀번호</span>
          <input
            type="password"
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label className="settings-delete-field">
          <span>확인 문구: 회원 탈퇴</span>
          <input
            type="text"
            value={deleteConfirmation}
            onChange={(event) => setDeleteConfirmation(event.target.value)}
            autoComplete="off"
          />
        </label>
      </ConfirmDialog>

      <footer className="settings-page__footer">
        <Link to="/privacy">개인정보처리방침</Link>
      </footer>
      <AppNav tabs />
    </div>
  )
}

export default SettingsPage
