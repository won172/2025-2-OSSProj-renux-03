import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiFetch, type ApiError } from '../../api/client'
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
  const [preferences, setPreferences] = useState<NotificationPreference[]>([])
  const [deadlines, setDeadlines] = useState<DeadlineItem[]>([])
  const [notifications, setNotifications] = useState<UserNotification[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [deadlinesLoading, setDeadlinesLoading] = useState(true)
  const [deadlinesError, setDeadlinesError] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [needsLogin, setNeedsLogin] = useState(false)

  const preferenceByTopic = useMemo(() => {
    const map = new Map<NotificationTopic, NotificationPreference>()
    preferences.forEach((preference) => map.set(preference.topic, preference))
    return map
  }, [preferences])

  const unreadCount = useMemo(() => notifications.filter((item) => !item.isRead).length, [notifications])

  // 관심 주제·알림함은 DB만 조회하므로 빠르다. 화면은 이 코어 로드만 기다린다.
  const loadCore = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    setNeedsLogin(false)
    try {
      const [preferenceData, notificationData] = await Promise.all([
        apiFetch<NotificationPreferenceResponse>('/notifications/preferences'),
        apiFetch<UserNotification[]>('/notifications'),
      ])
      setPreferences(preferenceData.preferences ?? [])
      setNotifications(Array.isArray(notificationData) ? notificationData : [])
    } catch (loadError) {
      if (isApiError(loadError) && loadError.status === 401) {
        setNeedsLogin(true)
        setError('로그인 후 알림 설정을 사용할 수 있습니다.')
      } else {
        setError('알림 정보를 불러오지 못했습니다.')
      }
    } finally {
      setIsLoading(false)
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
      // 마감일 실패는 전체 화면을 막지 않는다. 로그인 필요 여부는 코어 로더가 처리한다.
      setDeadlines([])
      if (!(isApiError(loadError) && loadError.status === 401)) {
        setDeadlinesError(true)
      }
    } finally {
      setDeadlinesLoading(false)
    }
  }, [])

  const loadData = useCallback(async () => {
    void loadDeadlines()
    await loadCore()
  }, [loadCore, loadDeadlines])

  useEffect(() => {
    loadData()
  }, [loadData])

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
    setError(null)
    setNotice(null)
    try {
      const result = await apiFetch<{ deleted: number }>('/notifications/read', { method: 'DELETE' })
      setNotifications((current) => current.filter((notification) => !notification.isRead))
      setNotice(result.deleted > 0 ? `읽은 알림 ${result.deleted}개를 정리했습니다.` : '정리할 읽은 알림이 없습니다.')
    } catch {
      setError('읽은 알림을 정리하지 못했습니다.')
    }
  }

  const handleMarkAllRead = async () => {
    const previous = notifications
    setNotifications((current) => current.map((notification) => ({ ...notification, isRead: true })))
    try {
      await apiFetch<{ updated: number }>('/notifications/read-all', { method: 'POST' })
    } catch {
      setNotifications(previous)
      setError('알림을 모두 읽음으로 표시하지 못했습니다.')
    }
  }

  const handleSave = async () => {
    setIsSaving(true)
    setError(null)
    setNotice(null)
    try {
      const data = await apiFetch<NotificationPreferenceResponse>('/notifications/preferences', {
        method: 'PUT',
        json: { preferences },
      })
      setPreferences(data.preferences ?? preferences)
      setNotice('알림 설정을 저장했습니다.')
      await loadData()
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
      setNotice(`새 알림 ${result.created}개를 확인했습니다. 관심 마감 ${result.upcomingMatched}개가 매칭되었습니다.`)
      await loadData()
    } catch {
      setError('마감 알림 동기화에 실패했습니다.')
    } finally {
      setIsSyncing(false)
    }
  }

  const handleMarkRead = async (notificationId: string) => {
    try {
      const updated = await apiFetch<UserNotification>(`/notifications/${notificationId}/read`, { method: 'POST' })
      setNotifications((current) => current.map((item) => (item.id === notificationId ? updated : item)))
    } catch {
      setError('알림 읽음 처리에 실패했습니다.')
    }
  }

  return (
    <div className="settings-page">
      <header className="settings-page__header">
        <div>
          <p className="settings-page__eyebrow">D-day 비서</p>
          <h1>알림 설정</h1>
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

      {error && <div className="settings-alert settings-alert--danger">{error}</div>}
      {notice && <div className="settings-alert settings-alert--success">{notice}</div>}

      {needsLogin ? (
        <section className="settings-panel">
          <h2>로그인이 필요합니다</h2>
          <p>관심 주제와 알림 읽음 상태는 계정에 저장됩니다.</p>
          <button type="button" className="auth-submit settings-page__login" onClick={() => navigate('/auth/in')}>
            로그인하기
          </button>
        </section>
      ) : isLoading ? (
        <section className="settings-panel">
          <p className="settings-empty">알림 정보를 불러오는 중입니다...</p>
        </section>
      ) : (
        <>
          <section className="settings-grid">
            <article className="settings-panel">
              <div className="settings-panel__header">
                <div>
                  <h2>관심 주제</h2>
                  <p>켜둔 주제만 내 마감일과 알림에 반영됩니다.</p>
                </div>
                <div className="settings-topic-bulk">
                  <button
                    type="button"
                    className="settings-page__back"
                    onClick={() => handleToggleAllTopics(true)}
                    disabled={isSaving || preferences.every((preference) => preference.enabled)}
                  >
                    전체 켜기
                  </button>
                  <button
                    type="button"
                    className="settings-page__back"
                    onClick={() => handleToggleAllTopics(false)}
                    disabled={isSaving || preferences.every((preference) => !preference.enabled)}
                  >
                    전체 끄기
                  </button>
                  <button type="button" className="auth-submit settings-save-btn" onClick={handleSave} disabled={isSaving}>
                    {isSaving ? '저장 중...' : '저장'}
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
                              disabled={!preference?.enabled}
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

            <article className="settings-panel settings-panel--summary">
              <h2>알림함</h2>
              <p className="settings-summary-number">{unreadCount}</p>
              <p>읽지 않은 내부 알림</p>
              <button type="button" className="settings-sync-btn" onClick={handleSync} disabled={isSyncing}>
                {isSyncing ? '확인 중...' : '마감 알림 확인'}
              </button>
            </article>
          </section>

          <section className="settings-panel">
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
                <p className="settings-empty">마감 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>
              ) : deadlines.length === 0 ? (
                <p className="settings-empty">켜둔 관심 주제에 해당하는 마감이 아직 없습니다.</p>
              ) : (
                deadlines.map((deadline) => (
                  <article className="deadline-item" key={deadline.id}>
                    <div className="deadline-item__d-day">{formatDday(deadline.dDay)}</div>
                    <div className="deadline-item__body">
                      <div className="deadline-item__meta">
                        <span>{deadline.topicLabel}</span>
                        <span>{deadline.sourceLabel}</span>
                        <span>{formatDate(deadline.targetDate)}</span>
                      </div>
                      <h3>{deadline.title}</h3>
                      {deadline.snippet && <p>{deadline.snippet}</p>}
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
          </section>

          <section className="settings-panel">
            <div className="settings-panel__header">
              <div>
                <h2>최근 알림</h2>
                <p>동기화 시점에 해당하는 7일 전, 1일 전, 당일 알림입니다.</p>
              </div>
              <div className="settings-topic-bulk">
                <span className="settings-count">{notifications.length}개</span>
                {unreadCount > 0 && (
                  <button type="button" className="settings-page__back" onClick={handleMarkAllRead}>
                    모두 읽음
                  </button>
                )}
                {notifications.some((notification) => notification.isRead) && (
                  <button type="button" className="settings-page__back" onClick={handleClearRead}>
                    읽은 알림 정리
                  </button>
                )}
              </div>
            </div>
            <div className="notification-list">
              {notifications.length === 0 ? (
                <p className="settings-empty">아직 생성된 알림이 없습니다.</p>
              ) : (
                notifications.map((notification) => (
                  <article className={`notification-item ${notification.isRead ? 'notification-item--read' : ''}`} key={notification.id}>
                    <div>
                      <div className="deadline-item__meta">
                        <span>{notification.topicLabel}</span>
                        <span>{formatDateTime(notification.createdTime)}</span>
                      </div>
                      <h3>{notification.title}</h3>
                      <p>{notification.body}</p>
                    </div>
                    <div className="notification-item__actions">
                      {notification.url && (
                        <a href={notification.url} target="_blank" rel="noreferrer">
                          출처
                        </a>
                      )}
                      {!notification.isRead && (
                        <button type="button" onClick={() => handleMarkRead(notification.id)}>
                          읽음
                        </button>
                      )}
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>
        </>
      )}

      <footer className="settings-page__footer">
        <Link to="/privacy">개인정보처리방침</Link>
      </footer>
    </div>
  )
}

export default SettingsPage
