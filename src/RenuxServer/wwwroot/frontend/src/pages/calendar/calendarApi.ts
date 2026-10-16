import { apiFetch, type ApiError } from '../../api/client.ts'
import { diffDays, isYmd, kindOrder, eventStart, type EventKind } from './calendarDates.ts'

/** GET /calendar/events 계약. 날짜는 모두 KST 달력 날짜다. */
export interface CalendarEvent {
  id: string
  source: 'schedule' | 'notices'
  kind: EventKind
  title: string
  startDate: string | null
  endDate: string
  url: string | null
  snippet: string | null
  evidence: string | null
  dateStatus: 'confirmed' | 'unverified'
  department: string | null
  publishedAt: string | null
}

export interface CalendarEventsResponse {
  from: string
  to: string
  generatedAt: string
  events: CalendarEvent[]
}

/** 서버가 허용하는 최대 조회 기간(양 끝 포함 일수). */
export const MAX_RANGE_DAYS = 92

export const validateRange = (from: string, to: string) => {
  if (!isYmd(from) || !isYmd(to)) return '날짜 형식이 올바르지 않습니다.'
  const days = diffDays(from, to) + 1
  if (days < 1) return '조회 시작일이 종료일보다 늦습니다.'
  if (days > MAX_RANGE_DAYS) return `조회 기간은 ${MAX_RANGE_DAYS}일을 넘을 수 없습니다.`
  return null
}

const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value : null)
const httpUrl = (value: unknown) => {
  const url = text(value)
  return url && /^https?:\/\//i.test(url) ? url : null
}

/** 계약과 다른 행은 버리고, 출처 링크는 http(s)만 남긴다. */
export const normalizeEvent = (raw: unknown): CalendarEvent | null => {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const kind = value.kind
  const source = value.source
  if (typeof value.id !== 'string' || !value.id || !text(value.title) || !isYmd(value.endDate)) return null
  if (kind !== 'academic' && kind !== 'exam' && kind !== 'notice') return null
  if (source !== 'schedule' && source !== 'notices') return null
  const startDate = isYmd(value.startDate) && value.startDate <= value.endDate ? value.startDate : null
  return {
    id: value.id,
    source,
    kind,
    title: String(value.title).trim(),
    startDate,
    endDate: value.endDate,
    url: httpUrl(value.url),
    snippet: text(value.snippet),
    evidence: text(value.evidence),
    dateStatus: value.dateStatus === 'confirmed' ? 'confirmed' : 'unverified',
    department: text(value.department),
    publishedAt: text(value.publishedAt),
  }
}

const byDate = (a: CalendarEvent, b: CalendarEvent) => eventStart(a).localeCompare(eventStart(b))
  || a.endDate.localeCompare(b.endDate) || kindOrder[a.kind] - kindOrder[b.kind] || a.title.localeCompare(b.title, 'ko')

export const fetchCalendarEvents = async (from: string, to: string, signal?: AbortSignal) => {
  const invalid = validateRange(from, to)
  if (invalid) {
    const error: ApiError = new Error(invalid)
    error.status = 400
    throw error
  }
  const params = new URLSearchParams({ from, to })
  const response = await apiFetch<Partial<CalendarEventsResponse>>(`/calendar/events?${params}`, { method: 'GET', signal })
  const events = (Array.isArray(response?.events) ? response.events : [])
    .map(normalizeEvent)
    .filter((event): event is CalendarEvent => event !== null)
    .sort(byDate)
  return { from, to, generatedAt: text(response?.generatedAt) ?? '', events }
}

/** 날짜를 확실히 읽지 못한 일정은 달력 칸에 두지 않고 따로 보여준다. */
export const splitByDateStatus = (events: CalendarEvent[]) => ({
  confirmed: events.filter((event) => event.dateStatus === 'confirmed'),
  unverified: events.filter((event) => event.dateStatus !== 'confirmed'),
})

export const calendarErrorMessage = (error: unknown) => {
  const status = (error as ApiError | null)?.status
  if (status === 400) return '조회 기간이 올바르지 않아요. 다른 달을 선택해 주세요.'
  if (status === 503) return '학사일정·공지 정보를 잠시 불러올 수 없어요.'
  if (status === 429) return '요청이 많아요. 잠시 후 다시 시도해 주세요.'
  if (typeof status === 'number') return `일정을 불러오지 못했어요. (오류 ${status})`
  return '네트워크 연결을 확인해 주세요.'
}

export const KIND_LABEL: Record<EventKind, string> = { academic: '학사', notice: '공지', exam: '시험' }

export const sourceLabel = (event: CalendarEvent) => event.source === 'schedule'
  ? '동국대 학사일정'
  : event.department ? `${event.department} 공지` : '동국대 공지사항'

/** 채팅 입력창에 미리 채울 질문. */
export const buildCalendarQuestion = (event: CalendarEvent) => `${event.title} 일정 자세히 알려줘`
