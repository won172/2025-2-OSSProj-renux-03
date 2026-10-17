import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import { apiFetch } from '../../api/client'
import { publicMajorName } from '../../auth/publicMajor'
import { fetchHomeBriefing, fetchNotifications } from '../../chat/chatApi'
import { countUnreadGroups, groupNotifications } from '../../chat/notifications'
import AppNav from '../../components/AppNav'
import type { AuthNameResponse } from '../../types/auth'
import type { HomeBriefing } from '../../types/briefing'
import { KIND_LABEL, calendarErrorMessage, eventDdayText, fetchCalendarEvents, type CalendarEvent } from '../calendar/calendarApi'
import { fetchSavedEvents, personalToEvent } from '../calendar/savedEventsApi'
import { addDays, dDayBig, kstToday } from '../calendar/calendarDates'
import {
  UPCOMING_RANGE_DAYS,
  briefingNotices,
  eventDetailPath,
  nextSevenDays,
  restaurantsFromBriefing,
  todayLabel,
  upcomingEvents,
} from './todayData'
import './today.css'

const CHAT_INPUT_MAX_LENGTH = 2000
const UPCOMING_LIMIT = 6

type Load<T> = { data: T | null; error: unknown; loading: boolean }

const useLoad = <T,>(load: () => Promise<T>) => {
  const [state, setState] = useState<Load<T>>({ data: null, error: null, loading: true })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setState((previous) => ({ ...previous, loading: true, error: null }))
    load().then(
      (data) => { if (active) setState({ data, error: null, loading: false }) },
      (error: unknown) => { if (active) setState({ data: null, error, loading: false }) },
    )
    return () => { active = false }
    // load는 화면마다 한 번 정해지는 함수다. 다시 시도는 attempt로만 일으킨다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])
  return { ...state, retry: useCallback(() => setAttempt((value) => value + 1), []) }
}

interface Viewer {
  authenticated: boolean
  department: string | null
}

const loadViewer = async (): Promise<Viewer> => {
  try {
    const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })
    if (!data?.name) return { authenticated: false, department: null }
    return { authenticated: true, department: publicMajorName(data.majorName) || null }
  } catch {
    return { authenticated: false, department: null }
  }
}

const TodayPage = () => {
  const navigate = useNavigate()
  const today = useMemo(() => kstToday(), [])
  const [question, setQuestion] = useState('')
  const [restaurantIndex, setRestaurantIndex] = useState(0)

  const viewer = useLoad(loadViewer)
  const briefing = useLoad<HomeBriefing>(fetchHomeBriefing)
  const calendar = useLoad(() => fetchCalendarEvents(today, addDays(today, UPCOMING_RANGE_DAYS - 1)))

  const authenticated = viewer.data?.authenticated === true
  const [unread, setUnread] = useState(0)
  const [savedIds, setSavedIds] = useState<Set<string>>(() => new Set())
  const [personal, setPersonal] = useState<CalendarEvent[]>([])
  useEffect(() => {
    if (!authenticated) return
    let active = true
    fetchNotifications().then(
      (notifications) => { if (active) setUnread(countUnreadGroups(groupNotifications(Array.isArray(notifications) ? notifications : []))) },
      () => { if (active) setUnread(0) },
    )
    // 저장한 일정은 카드에 별로만 표시한다. 실패해도 홈은 그대로 보인다.
    fetchSavedEvents().then(
      (result) => {
        if (!active || !result.signedIn) return
        setSavedIds(new Set(result.events.filter((event) => event.source !== 'personal').map((event) => event.eventId)))
        setPersonal(result.events.filter((event) => event.source === 'personal').map(personalToEvent))
      },
      () => {},
    )
    return () => { active = false }
  }, [authenticated])

  // 내가 추가한 일정도 다가오는 일정·7일 표에 함께 보인다.
  const events = useMemo(() => [...personal, ...(calendar.data?.events ?? [])], [calendar.data, personal])
  const upcoming = useMemo(() => upcomingEvents(events, today, UPCOMING_LIMIT), [events, today])
  const week = useMemo(() => nextSevenDays(events, today), [events, today])
  const restaurants = useMemo(() => restaurantsFromBriefing(briefing.data), [briefing.data])
  const notices = useMemo(() => briefingNotices(briefing.data), [briefing.data])
  const restaurant = restaurants[Math.min(restaurantIndex, Math.max(0, restaurants.length - 1))]

  const ask = (event: FormEvent) => {
    event.preventDefault()
    const text = question.trim().slice(0, CHAT_INPUT_MAX_LENGTH)
    navigate('/chat', text ? { state: { prefillQuestion: text } } : undefined)
  }

  const subtitle = [todayLabel(today), viewer.data?.department].filter(Boolean).join(' · ')

  return (
    <Shell>
      <div className="today-scroll">
        <div className="today-content">
          <header className="today-header">
            <div>
              <p className="today-muted today-small">{subtitle}</p>
              <h1>오늘의 동똑</h1>
            </div>
            {authenticated && (
              <Link className="today-bell" to="/settings" state={{ screen: 'inbox' }}
                aria-label={unread > 0 ? `알림함, 읽지 않은 알림 ${unread}개` : '알림함'}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></svg>
                {unread > 0 && <span className="today-bell__badge" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
              </Link>
            )}
          </header>

          <form className="today-ask" onSubmit={ask} role="search">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z" /></svg>
            <label htmlFor="today-ask-input" className="today-sr">동똑이에게 질문하기</label>
            <input id="today-ask-input" value={question} maxLength={CHAT_INPUT_MAX_LENGTH}
              onChange={(event) => setQuestion(event.target.value)} placeholder="동똑이에게 물어보세요" />
            <button type="submit">물어보기</button>
          </form>

          <div className="today-grid">
            <Section title="다가오는 일정" action={<Link to="/calendar">캘린더</Link>} className="today-upcoming">
              {calendar.loading ? <Skeleton rows={3} /> : calendar.error ? (
                <ErrorRow message={calendarErrorMessage(calendar.error)} onRetry={calendar.retry} />
              ) : upcoming.length === 0 ? (
                <p className="today-empty">{UPCOMING_RANGE_DAYS}일 안에 다가오는 일정이 없어요.</p>
              ) : (
                <ul className="today-events">
                  {upcoming.map((event) => <UpcomingItem key={event.id} event={event} today={today} saved={savedIds.has(event.id)} />)}
                </ul>
              )}
            </Section>

            <Section title="오늘 학식" className="today-meals">
              {briefing.loading ? <Skeleton rows={3} /> : !restaurant ? (
                <p className="today-empty">{briefing.error ? '학식 정보를 불러오지 못했어요.' : '오늘은 운영하는 식당 정보가 없어요.'}</p>
              ) : (
                <>
                  {restaurants.length > 1 && (
                    <div className="today-chips" role="group" aria-label="식당 선택">
                      {restaurants.map((item, index) => (
                        <button key={item.name} type="button" className="today-chip" aria-pressed={item === restaurant}
                          onClick={() => setRestaurantIndex(index)}>{item.name}</button>
                      ))}
                    </div>
                  )}
                  <ul className="today-menu" aria-label={`${restaurant.name} 메뉴`}>
                    {restaurant.corners.map((corner) => (
                      <li key={`${corner.corner}-${corner.menu}`}><b>{corner.corner}</b><span>{corner.menu}</span></li>
                    ))}
                  </ul>
                </>
              )}
            </Section>

            <Section title={authenticated ? '새 공지 · 내 학과 포함' : '새 공지'} className="today-notices">
              {briefing.loading ? <Skeleton rows={2} /> : notices.length === 0 ? (
                <p className="today-empty">{briefing.error ? '공지를 불러오지 못했어요.' : '새 공지가 없어요.'}</p>
              ) : (
                <ul className="today-notice-list">
                  {notices.map((notice) => {
                    const meta = [notice.board, notice.publishedAt].filter(Boolean).join(' · ')
                    const body = <>{meta && <span className="today-muted today-small">{meta}</span>}<span className="today-notice-title">{notice.title}</span></>
                    return (
                      <li key={`${notice.title}-${notice.url ?? ''}`}>
                        {notice.url
                          ? <a href={notice.url} target="_blank" rel="noopener noreferrer" aria-label={`${notice.title} (원문, 새 창)`}>{body}</a>
                          : <div>{body}</div>}
                      </li>
                    )
                  })}
                </ul>
              )}
            </Section>
          </div>

          {!calendar.loading && !calendar.error && (
            <Section title="앞으로 7일" action={<Link to="/calendar">월간 보기</Link>} className="today-week">
              <div className="today-week__scroll">
                <ol className="today-week__grid">
                  {week.map((day) => (
                    <li key={day.date} className={day.isToday ? 'today-day is-today' : 'today-day'}
                      aria-label={`${day.label}${day.isToday ? ' 오늘' : ''}, 일정 ${day.events.length}개`}>
                      <span className={`today-day__label wd-${day.weekday}`} aria-hidden="true">{day.label}{day.isToday ? ' · 오늘' : ''}</span>
                      {day.events.slice(0, 3).map((event) => (
                        <span key={event.id} className={`today-bar kind-${event.kind}`}>{event.title}</span>
                      ))}
                      {day.events.length > 3 && <span className="today-muted today-small">+{day.events.length - 3}개</span>}
                    </li>
                  ))}
                </ol>
              </div>
            </Section>
          )}
        </div>
      </div>
    </Shell>
  )
}

const UpcomingItem = ({ event, today, saved }: { event: CalendarEvent; today: string; saved: boolean }) => {
  const big = dDayBig(event, today)
  return (
    <li>
      <Link className="today-event" to={eventDetailPath(event, today)} state={{ event }}
        aria-label={`${KIND_LABEL[event.kind]} ${event.title}, ${eventDdayText(event, today)}${saved ? ', 저장한 일정' : ''}`}>
        <span className="today-event__head">
          <span className={`today-tag kind-${event.kind}`}>{KIND_LABEL[event.kind]}</span>
          {saved && <svg className="today-event__star" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" /></svg>}
        </span>
        <span className="today-event__title">{event.title}</span>
        <span className="today-event__dday">{big.value}</span>
      </Link>
    </li>
  )
}

const Section = ({ title, action, className, children }: { title: string; action?: ReactNode; className: string; children: ReactNode }) => (
  <section className={`today-card ${className}`}>
    <h2 className="today-card__title"><span>{title}</span>{action}</h2>
    {children}
  </section>
)

const Skeleton = ({ rows }: { rows: number }) => (
  <ul className="today-skeleton" aria-hidden="true">
    {Array.from({ length: rows }, (_, index) => <li key={index} />)}
  </ul>
)

const ErrorRow = ({ message, onRetry }: { message: string; onRetry: () => void }) => (
  <div className="today-error" role="alert">
    <p>{message}</p>
    <button type="button" onClick={onRetry}>다시 시도</button>
  </div>
)

const Shell = ({ children }: { children: ReactNode }) => (
  <div className="ch-shell today-shell">
    <aside className="ch-sidebar today-side">
      <div className="ch-sidebar__top">
        <div className="ch-brand">
          <img src={dongddokiLogo} alt="" className="ch-brand__logo" />
          <span className="ch-brand__name">동국대학교 동똑이</span>
        </div>
        <AppNav />
      </div>
    </aside>
    <main className="ch-main today-main">
      {children}
      <AppNav tabs />
    </main>
  </div>
)

export default TodayPage
