import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react'
import { Link, useLocation, useMatch, useNavigate, useSearchParams } from 'react-router-dom'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import AppNav from '../../components/AppNav'
import {
  KIND_LABEL,
  buildCalendarQuestion,
  eventDdayBig,
  eventDdayText,
  calendarErrorMessage,
  fetchCalendarEvents,
  normalizeEvent,
  sourceLabel,
  splitByDateStatus,
  type CalendarEvent,
} from './calendarApi'
import {
  WEEKDAYS,
  addDays,
  eventStart,
  firstOfMonth,
  formatPeriod,
  groupUpcoming,
  isYmd,
  kstToday,
  layoutWeek,
  monthGrid,
  monthOf,
  monthTitle,
  occursOn,
  shiftMonth,
  weekdayLabel,
  weekdayOf,
  type EventKind,
  type MonthRef,
} from './calendarDates'
import PersonalEventForm from './PersonalEventForm'
import ReminderCard from './ReminderCard'
import { isPersonalId, personalToEvent, type PersonalEventInput, type SavedEvent, type SavedReminder } from './savedEventsApi'
import { useSavedEvents, type SavedStatus } from './useSavedEvents'
import './calendar.css'

const KINDS: EventKind[] = ['academic', 'notice', 'exam']
/** 로그인하면 '내 일정' 칩이 앞에 붙는다. */
const ALL_KINDS: EventKind[] = ['personal', ...KINDS]
const DESKTOP_QUERY = '(min-width: 901px)'
/** 넓은 화면 달력 칸에 쌓는 막대 줄 수. 넘치면 "+n"으로 줄인다. */
const MAX_LANES = 3
const LIST_RANGE_DAYS = 91 // 오늘 포함 92일

const subscribeDesktop = (onChange: () => void) => {
  const query = window.matchMedia(DESKTOP_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
const useIsDesktop = () => useSyncExternalStore(subscribeDesktop, () => window.matchMedia(DESKTOP_QUERY).matches, () => false)

type LoadState = { key: string; events?: CalendarEvent[]; error?: string }

/** 보이는 범위만 조회한다. 같은 범위는 화면에 머무는 동안 다시 받지 않는다. */
const useCalendarEvents = (from: string, to: string) => {
  const key = `${from}|${to}`
  const cache = useRef(new Map<string, CalendarEvent[]>())
  const [state, setState] = useState<LoadState | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const cached = cache.current.get(key)
    if (cached) {
      setState({ key, events: cached })
      return
    }
    const controller = new AbortController()
    fetchCalendarEvents(from, to, controller.signal)
      .then((response) => {
        cache.current.set(key, response.events)
        setState({ key, events: response.events })
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setState({ key, error: calendarErrorMessage(error) })
      })
    return () => controller.abort()
  }, [from, to, key, attempt])

  const retry = useCallback(() => { setState(null); setAttempt((value) => value + 1) }, [])
  const current = state?.key === key ? state : null
  return { events: current?.events ?? null, error: current?.error ?? null, loading: !current, retry }
}

const dayLabel = (day: string) => `${Number(day.slice(5, 7))}월 ${Number(day.slice(8, 10))}일`
const eventPath = (event: CalendarEvent) => `/calendar/event/${encodeURIComponent(event.id)}?d=${eventStart(event)}`

const Tag = ({ kind }: { kind: EventKind }) => <span className={`cal-tag cal-tag--${kind}`}>{KIND_LABEL[kind]}</span>

const STAR_PATH = 'M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z'

/** 저장 상태와 동작. 화면 곳곳의 별·상세 버튼이 같은 값을 쓴다. */
interface SaveControls {
  status: SavedStatus
  byId: Map<string, SavedEvent>
  pending: Set<string>
  toggle: (event: CalendarEvent) => void
  setReminder: (eventId: string, reminder: SavedReminder) => void
  /** 로그인하지 않은 사용자가 저장을 누르면 안내를 띄운다. */
  askLogin: () => void
  /** 개인 일정 수정 화면으로 간다. */
  edit: (event: CalendarEvent) => void
  remove: (event: CalendarEvent) => void
}

/** 목록·카드 옆 별 버튼. 터치 영역 44px. 불러오는 중·조회 실패면 숨긴다. */
const SaveStar = ({ event, controls }: { event: CalendarEvent; controls: SaveControls }) => {
  if (controls.status === 'loading' || controls.status === 'error' || event.dateStatus !== 'confirmed' || event.source === 'personal') return null
  const saved = controls.byId.has(event.id)
  const busy = controls.pending.has(event.id)
  return (
    <button
      type="button"
      className={saved ? 'cal-star is-saved' : 'cal-star'}
      aria-pressed={saved}
      aria-busy={busy}
      disabled={busy}
      aria-label={saved ? `${event.title} 저장 해제` : `${event.title} 저장`}
      onClick={() => (controls.status === 'guest' ? controls.askLogin() : controls.toggle(event))}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d={STAR_PATH} /></svg>
    </button>
  )
}

const EventCard = ({ event, today, replace, controls }: { event: CalendarEvent; today: string; replace?: boolean; controls: SaveControls }) => (
  <li className="cal-item">
    <Link className="cal-card" to={eventPath(event)} state={{ event }} replace={replace}>
      <span className="cal-card__top"><Tag kind={event.kind} /><span className="cal-dday">{eventDdayText(event, today)}</span></span>
      <span className="cal-card__title">{event.title}</span>
      <span className="cal-card__meta">{formatPeriod(event, today)} · {sourceLabel(event)}</span>
    </Link>
    <SaveStar event={event} controls={controls} />
  </li>
)

/** 상세 하단 버튼 줄. 좁은 화면에서는 탭 위에 붙어 엄지로 바로 누를 수 있다. */
const DetailActions = ({ event, controls, onAsk }: { event: CalendarEvent; controls: SaveControls; onAsk: () => void }) => {
  const saved = controls.byId.has(event.id)
  const busy = controls.pending.has(event.id)
  const [confirmDelete, setConfirmDelete] = useState(false)
  if (event.source === 'personal') {
    return (
      <div className="cal-detail__actions">
        <button type="button" className="cal-btn cal-btn--primary cal-save" onClick={() => controls.edit(event)} disabled={busy}>수정</button>
        <button
          type="button"
          className={confirmDelete ? 'cal-btn cal-delete is-confirming' : 'cal-btn cal-delete'}
          disabled={busy}
          onClick={() => (confirmDelete ? controls.remove(event) : setConfirmDelete(true))}
          onBlur={() => setConfirmDelete(false)}
        >{busy ? '삭제하는 중...' : confirmDelete ? '한 번 더 누르면 삭제' : '삭제'}</button>
      </div>
    )
  }
  const canSave = event.dateStatus === 'confirmed' && controls.status !== 'loading' && controls.status !== 'error'
  return (
    <div className="cal-detail__actions">
      {canSave && (
        <button
          type="button"
          className={saved ? 'cal-btn cal-save is-saved' : 'cal-btn cal-btn--primary cal-save'}
          aria-pressed={saved}
          aria-busy={busy}
          disabled={busy}
          onClick={() => (controls.status === 'guest' ? controls.askLogin() : controls.toggle(event))}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={STAR_PATH} /></svg>
          {busy ? (saved ? '해제하는 중...' : '저장하는 중...') : saved ? '저장됨' : '일정 저장'}
        </button>
      )}
      <button type="button" className={canSave ? 'cal-btn cal-detail__ask' : 'cal-btn cal-btn--primary cal-detail__ask'} onClick={onAsk} aria-label="이 일정 질문하기">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z" /></svg>
        질문
      </button>
    </div>
  )
}

const UnverifiedList = ({ events }: { events: CalendarEvent[] }) => events.length === 0 ? null : (
  <section className="cal-section" aria-labelledby="cal-unverified-title">
    <h2 id="cal-unverified-title" className="cal-section__title">날짜 확인 필요 <span>{events.length}</span></h2>
    <ul className="cal-cards">
      {events.map((event) => (
        <li key={event.id} className="cal-unverified">
          <p className="cal-unverified__head"><Tag kind={event.kind} /> <b>{event.title}</b></p>
          <p>날짜 확인 필요 — 공지에서 날짜를 확실히 읽지 못했어요. 캘린더에는 넣지 않았어요.</p>
          {event.url && <a href={event.url} target="_blank" rel="noopener noreferrer">원문에서 확인 <span aria-hidden="true">↗</span></a>}
        </li>
      ))}
    </ul>
  </section>
)

const EventDetail = ({ event, today, onAsk, controls }: { event: CalendarEvent; today: string; onAsk: () => void; controls: SaveControls }) => {
  const big = eventDdayBig(event, today)
  const quote = event.evidence ?? event.snippet
  const saved = controls.byId.get(event.id)
  return (
    <article className="cal-detail" aria-labelledby="cal-detail-title">
      <Tag kind={event.kind} />
      <h2 id="cal-detail-title" className="cal-detail__title">{event.title}</h2>
      <div className="cal-detail__date">
        <div>
          <span className="cal-detail__label">기간</span>
          <strong>{formatPeriod(event, today)}</strong>
          {event.department && <span className="cal-detail__org">주관/부서 · {event.department}</span>}
        </div>
        <div className="cal-detail__dday"><strong>{big.value}</strong>{big.caption && <span>{big.caption}</span>}</div>
      </div>
      {event.source !== 'personal' && <section className="cal-detail__evidence" aria-label="근거">
        <h3>근거</h3>
        {quote
          ? event.evidence ? <blockquote>{quote}</blockquote> : <p>{quote}</p>
          : <p className="cal-muted">원문에서 내용을 확인해 주세요.</p>}
        <p className="cal-detail__source">
          <span>{sourceLabel(event)}</span>
          {event.url && <a href={event.url} target="_blank" rel="noopener noreferrer">원문 보기 <span aria-hidden="true">↗</span></a>}
        </p>
      </section>}
      {saved && (
        <ReminderCard reminder={saved.reminder} busy={controls.pending.has(event.id)} onChange={(reminder) => controls.setReminder(event.id, reminder)} />
      )}
      <DetailActions event={event} controls={controls} onAsk={onAsk} />
    </article>
  )
}

const CalendarPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const detailMatch = useMatch('/calendar/event/:eventId')
  const editMatch = useMatch('/calendar/event/:eventId/edit')
  const newMatch = useMatch('/calendar/new')
  const isDesktop = useIsDesktop()
  const today = useMemo(() => kstToday(), [])

  const hinted = searchParams.get('d')
  const initialDay = isYmd(hinted) ? hinted : today
  const [view, setView] = useState<'month' | 'list'>('month')
  const [month, setMonth] = useState<MonthRef>(() => monthOf(initialDay))
  const [selectedDay, setSelectedDay] = useState(initialDay)
  const [kinds, setKinds] = useState<Set<EventKind>>(() => new Set(ALL_KINDS))
  const [formBusy, setFormBusy] = useState(false)
  const [savedOnly, setSavedOnly] = useState(false)
  const [loginPrompt, setLoginPrompt] = useState(false)

  const grid = useMemo(() => monthGrid(month), [month])
  const range = view === 'list' && !detailMatch ? { from: today, to: addDays(today, LIST_RANGE_DAYS) } : grid
  const { events, error, loading, retry } = useCalendarEvents(range.from, range.to)
  const saved = useSavedEvents()
  const controls: SaveControls = {
    status: saved.status,
    byId: saved.byId,
    pending: saved.pending,
    toggle: (event) => { setLoginPrompt(false); void saved.toggle(event) },
    setReminder: (eventId, reminder) => { void saved.setReminder(eventId, reminder) },
    askLogin: () => setLoginPrompt(true),
    edit: (event) => navigate(`/calendar/event/${encodeURIComponent(event.id)}/edit`),
    remove: (event) => {
      void saved.remove(event.id)
      navigate('/calendar', { replace: true })
    },
  }
  const filterSaved = savedOnly && saved.status === 'ready'

  // 개인 일정은 저장 목록에서 오므로 보이는 범위와 겹치는 것만 공식 일정과 합친다.
  const personalEvents = useMemo(() => [...saved.byId.values()]
    .filter((item) => item.source === 'personal')
    .map(personalToEvent)
    .filter((event) => (event.startDate ?? event.endDate) <= range.to && event.endDate >= range.from), [saved.byId, range.from, range.to])

  const { confirmed, unverified } = useMemo(() => {
    const split = splitByDateStatus(events ?? [])
    const official = split.confirmed.filter((event) => kinds.has(event.kind) && (!filterSaved || saved.byId.has(event.id)))
    const own = kinds.has('personal') ? personalEvents : []
    return {
      confirmed: [...own, ...official],
      // 날짜 확인 필요 일정은 저장할 수 없으므로 '저장됨'만 볼 때는 숨긴다.
      unverified: filterSaved ? [] : split.unverified.filter((event) => kinds.has(event.kind)),
    }
  }, [events, kinds, filterSaved, saved.byId, personalEvents])

  const editId = editMatch?.params.eventId ? decodeURIComponent(editMatch.params.eventId) : null
  const detailId = editId ?? (detailMatch?.params.eventId ? decodeURIComponent(detailMatch.params.eventId) : null)
  const stateEvent = normalizeEvent((location.state as { event?: unknown } | null)?.event)
  const personal = detailId && isPersonalId(detailId) ? saved.byId.get(detailId) : undefined
  const detailEvent = detailId
    ? personal ? personalToEvent(personal)
      : stateEvent?.id === detailId ? stateEvent : events?.find((event) => event.id === detailId) ?? null
    : null
  const formOpen = Boolean(newMatch || (editId && personal))

  const submitPersonal = async (input: PersonalEventInput) => {
    setFormBusy(true)
    const result = await saved.savePersonal(input, editId ?? undefined)
    setFormBusy(false)
    if (!result) return
    setSelectedDay(result.startDate ?? result.endDate)
    setMonth(monthOf(result.startDate ?? result.endDate))
    navigate(`/calendar/event/${encodeURIComponent(result.eventId)}?d=${result.startDate ?? result.endDate}`, { replace: true })
  }
  const addPersonal = () => (saved.status === 'guest' ? setLoginPrompt(true) : navigate(`/calendar/new?d=${selectedDay}`))

  const goMonth = (next: MonthRef) => {
    setMonth(next)
    const todayRef = monthOf(today)
    setSelectedDay(next.year === todayRef.year && next.month === todayRef.month ? today : firstOfMonth(next))
  }
  const toggleKind = (kind: EventKind) => setKinds((previous) => {
    const next = new Set(previous)
    if (next.has(kind)) next.delete(kind)
    else next.add(kind)
    return next
  })
  const ask = (event: CalendarEvent) => navigate('/chat', { state: { prefillQuestion: buildCalendarQuestion(event) } })
  const back = () => {
    if (location.key !== 'default') navigate(-1)
    else navigate('/calendar', { replace: true })
  }

  const dayEvents = confirmed.filter((event) => occursOn(event, selectedDay))
  const errorBox = error && (
    <div className="cal-error" role="alert">
      <span>{error}</span>
      <button type="button" className="cal-btn" onClick={retry}>다시 시도</button>
    </div>
  )

  // 저장 실패·로그인 안내. 화면 위쪽 고정 위치에 두고 스크린 리더에 바로 알린다.
  const notice = (saved.message || loginPrompt) && (
    <div className="cal-notice" role="status">
      {saved.message ?? <>로그인하면 일정을 저장하고 앱 안 알림을 받을 수 있어요. <Link to="/auth/in">로그인</Link></>}
      <button type="button" className="cal-notice__close" aria-label="안내 닫기" onClick={() => { saved.clearMessage(); setLoginPrompt(false) }}>×</button>
    </div>
  )

  const form = formOpen && (
    <PersonalEventForm
      key={editId ?? 'new'}
      initial={personal}
      defaultDay={isYmd(hinted) ? hinted : selectedDay}
      busy={formBusy}
      onSubmit={(input) => { void submitPersonal(input) }}
      onCancel={back}
    />
  )

  const detailBody = form || (
    detailEvent ? <EventDetail event={detailEvent} today={today} onAsk={() => ask(detailEvent)} controls={controls} />
      : loading ? <p className="cal-status" role="status">일정을 불러오는 중...</p>
        : errorBox || <p className="cal-empty">일정을 찾을 수 없어요. 캘린더에서 다시 선택해 주세요.</p>
  )

  // 좁은 화면의 상세는 전체 화면으로 보인다.
  if ((detailId || newMatch) && !isDesktop) {
    return (
      <Shell>
        <header className="cal-header cal-header--detail">
          <button type="button" className="cal-icon-btn" onClick={back} aria-label="캘린더로 돌아가기">‹</button>
          <h1>{newMatch ? '내 일정 추가' : editId ? '내 일정 수정' : '일정 상세'}</h1>
        </header>
        {notice}
        <div className="cal-scroll"><div className="cal-pad cal-pad--detail">{form || detailBody}</div></div>
      </Shell>
    )
  }

  const header = (
    <header className="cal-header">
      <div className="cal-header__title">
        {view === 'month' ? (
          <>
            <button type="button" className="cal-icon-btn" onClick={() => goMonth(shiftMonth(month, -1))} aria-label="이전 달">‹</button>
            <h1 aria-live="polite">{monthTitle(month)}</h1>
            <button type="button" className="cal-icon-btn" onClick={() => goMonth(shiftMonth(month, 1))} aria-label="다음 달">›</button>
          </>
        ) : <h1>다가오는 일정</h1>}
      </div>
      <div className="cal-header__actions">
        {/* 좁은 화면에서는 탭 위 오른쪽 아래에 떠 있는 버튼으로 보인다(CSS). */}
        {saved.status !== 'loading' && saved.status !== 'error' && (
          <button type="button" className="cal-add" onClick={addPersonal} aria-label="내 일정 추가">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
            <span className="cal-add__label">일정 추가</span>
          </button>
        )}
        <div className="cal-segment" role="group" aria-label="보기 방식">
          <button type="button" aria-pressed={view === 'month'} onClick={() => setView('month')}>월</button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>목록</button>
        </div>
      </div>
    </header>
  )

  const chips = (
    <div className="cal-chips">
      <div className="cal-chips__kinds" role="group" aria-label="일정 종류">
        {(saved.status === 'ready' ? ALL_KINDS : KINDS).map((kind) => (
          <button key={kind} type="button" className={`cal-chip cal-chip--${kind}`} aria-pressed={kinds.has(kind)} onClick={() => toggleKind(kind)}>
            <span className="cal-dot" aria-hidden="true" />{KIND_LABEL[kind]}
          </button>
        ))}
        {saved.status === 'ready' && (
          <button type="button" className="cal-chip cal-chip--saved" aria-pressed={savedOnly} onClick={() => setSavedOnly((value) => !value)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d={STAR_PATH} /></svg>저장됨
          </button>
        )}
      </div>
      {view === 'month' && <button type="button" className="cal-btn cal-today" onClick={() => goMonth(monthOf(today))}>오늘</button>}
    </div>
  )

  const replace = Boolean(detailId)
  const dayList = (
    <section className="cal-section" aria-labelledby="cal-day-title" aria-busy={loading}>
      <h2 id="cal-day-title" className="cal-section__title">{dayLabel(selectedDay)} ({weekdayLabel(selectedDay)})</h2>
      {loading ? <Skeleton rows={2} /> : error ? null : dayEvents.length === 0
        ? <p className="cal-empty">{filterSaved ? '이 날은 저장한 일정이 없어요' : '이 날은 일정이 없어요'}</p>
        : <ul className="cal-cards">{dayEvents.map((event) => <EventCard key={event.id} event={event} today={today} replace={replace} controls={controls} />)}</ul>}
    </section>
  )

  const listView = (
    <div className="cal-list" aria-busy={loading}>
      {loading ? <Skeleton rows={4} /> : error ? null : (() => {
        const groups = groupUpcoming(confirmed, today)
        if (groups.length === 0) return <p className="cal-empty">{filterSaved ? '저장한 다가오는 일정이 없어요' : '다가오는 일정이 없어요'}</p>
        return groups.map((group) => (
          <section key={group.id} className="cal-section" aria-label={group.label}>
            <h2 className="cal-section__title">{group.label}</h2>
            <ul className="cal-rows">
              {group.items.map((event) => {
                const anchor = eventStart(event) > today ? eventStart(event) : event.endDate
                return (
                  <li key={event.id} className="cal-item">
                    <Link className="cal-row" to={eventPath(event)} state={{ event }} replace={replace}>
                      <span className={`cal-row__date cal-wd-${weekdayOf(anchor)}`}><b>{Number(anchor.slice(8, 10))}</b>{weekdayLabel(anchor)}</span>
                      <span className="cal-row__body">
                        <span className="cal-card__top"><Tag kind={event.kind} /><span className="cal-dday">{eventDdayText(event, today)}</span></span>
                        <span className="cal-card__title">{event.title}</span>
                        <span className="cal-card__meta">{formatPeriod(event, today)}</span>
                      </span>
                    </Link>
                    <SaveStar event={event} controls={controls} />
                  </li>
                )
              })}
            </ul>
          </section>
        ))
      })()}
    </div>
  )

  const weekdayHead = (
    <div className="cal-weekdays" aria-hidden="true">
      {WEEKDAYS.map((label, index) => <span key={label} className={`cal-wd-${index}`}>{label}</span>)}
    </div>
  )

  const dayButton = (day: string, count: number, children: ReactNode, style?: CSSProperties) => (
    <button
      key={day}
      type="button"
      className={`cal-cell cal-wd-${weekdayOf(day)}${day.slice(0, 7) !== firstOfMonth(month).slice(0, 7) ? ' cal-cell--outside' : ''}`}
      style={style}
      aria-pressed={day === selectedDay}
      aria-current={day === today ? 'date' : undefined}
      aria-label={`${dayLabel(day)}${day === today ? ' 오늘' : ''}, 일정 ${loading ? '불러오는 중' : `${count}개`}`}
      onClick={() => setSelectedDay(day)}
    >
      {children}
    </button>
  )

  const mobileGrid = (
    <div className="cal-grid" aria-busy={loading}>
      {weekdayHead}
      {grid.weeks.map((week) => (
        <div key={week[0]} className="cal-week">
          {week.map((day) => {
            const present = ALL_KINDS.filter((kind) => confirmed.some((event) => event.kind === kind && occursOn(event, day)))
            const count = confirmed.filter((event) => occursOn(event, day)).length
            return dayButton(day, count, (
              <>
                <span className="cal-num">{Number(day.slice(8, 10))}</span>
                <span className="cal-dots" aria-hidden="true">
                  {present.map((kind) => <span key={kind} className={`cal-dot cal-dot--${kind}`} />)}
                </span>
              </>
            ))
          })}
        </div>
      ))}
    </div>
  )

  const desktopGrid = (
    <div className="cal-grid-box">
      <div className="cal-grid cal-grid--wide" aria-busy={loading}>
        {weekdayHead}
        {grid.weeks.map((week) => {
          const segments = layoutWeek(confirmed, week)
          return (
            <div key={week[0]} className="cal-week">
              {week.map((day, index) => {
                const count = confirmed.filter((event) => occursOn(event, day)).length
                const hidden = segments.filter((segment) => segment.lane >= MAX_LANES
                  && index >= segment.startCol && index < segment.startCol + segment.span).length
                return dayButton(day, count, (
                  <>
                    <span className="cal-num">{Number(day.slice(8, 10))}</span>
                    {hidden > 0 && <span className="cal-more">+{hidden}개</span>}
                  </>
                ), { gridColumn: index + 1 })
              })}
              {segments.filter((segment) => segment.lane < MAX_LANES).map(({ item, startCol, span, lane, continues }) => (
                <Link
                  key={item.id}
                  to={eventPath(item)}
                  state={{ event: item }}
                  replace={replace}
                  className={`cal-bar cal-bar--${item.kind}${continues ? ' cal-bar--cont' : ''}${item.endDate > week[6] ? ' cal-bar--more' : ''}${detailId === item.id ? ' cal-bar--active' : ''}`}
                  style={{ gridColumn: `${startCol + 1} / span ${span}`, gridRow: lane + 2 }}
                  onClick={() => setSelectedDay(week[startCol])}
                  title={`${KIND_LABEL[item.kind]} · ${item.title}`}
                >
                  <b>{KIND_LABEL[item.kind]}</b> {item.title}
                </Link>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )

  return (
    <Shell>
      {header}
      {notice}
      <div className="cal-scroll">
        <div className={isDesktop ? 'cal-body cal-body--wide' : 'cal-body'}>
          <div className="cal-pane">
            {chips}
            {errorBox}
            {view === 'list' ? listView : isDesktop ? desktopGrid : mobileGrid}
            {!isDesktop && view === 'month' && dayList}
            {!isDesktop && <UnverifiedList events={unverified} />}
          </div>
          {isDesktop && (
            <aside className="cal-panel" aria-label="선택한 날짜와 일정">
              {view === 'month' && dayList}
              {(detailId || newMatch) && <div className="cal-panel__detail">{detailBody}</div>}
              <UnverifiedList events={unverified} />
            </aside>
          )}
        </div>
      </div>
    </Shell>
  )
}

const Skeleton = ({ rows }: { rows: number }) => (
  <ul className="cal-cards" aria-hidden="true">
    {Array.from({ length: rows }, (_, index) => <li key={index} className="cal-skeleton" />)}
  </ul>
)

const Shell = ({ children }: { children: ReactNode }) => (
  <div className="ch-shell cal-shell">
    <aside className="ch-sidebar cal-side">
      <div className="ch-sidebar__top">
        <div className="ch-brand">
          <img src={dongddokiLogo} alt="" className="ch-brand__logo" />
          <span className="ch-brand__name">동국대학교 동똑이</span>
        </div>
        <AppNav />
      </div>
    </aside>
    <main className="ch-main cal-main">
      {children}
      <AppNav tabs />
    </main>
  </div>
)

export default CalendarPage
