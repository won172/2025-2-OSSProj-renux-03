import { apiFetch, type ApiError } from '../../api/client.ts'
import type { CalendarEvent } from './calendarApi.ts'
import { diffDays, isYmd, type EventKind } from './calendarDates.ts'

/** 알림을 고를 수 있는 시점(일 전). 서버와 같은 값만 허용한다. */
export const REMINDER_STEPS = [7, 1, 0] as const
export type ReminderStep = typeof REMINDER_STEPS[number]
export const REMINDER_LABEL: Record<ReminderStep, string> = { 7: '7일 전', 1: '1일 전', 0: '당일' }
export const DEFAULT_REMINDER: SavedReminder = { enabled: true, daysBefore: [7, 1] }

export interface SavedReminder {
  enabled: boolean
  daysBefore: ReminderStep[]
}

/** GET/PUT /calendar/saved 계약. 제목·기간은 서버가 공식 일정에서 가져온 값이다. */
export interface SavedEvent {
  eventId: string
  /** personal은 사용자가 직접 추가한 일정이다. */
  source: 'schedule' | 'notices' | 'personal'
  kind: EventKind
  title: string
  startDate: string | null
  endDate: string
  url: string | null
  reminder: SavedReminder
}

const isStep = (value: unknown): value is ReminderStep => REMINDER_STEPS.includes(value as ReminderStep)

/** 서버 순서와 상관없이 7 → 1 → 0 순서로 정리한다. */
export const normalizeSteps = (steps: readonly unknown[]): ReminderStep[] =>
  REMINDER_STEPS.filter((step) => steps.includes(step))

export const normalizeSavedEvent = (raw: unknown): SavedEvent | null => {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const kind = value.kind
  const source = value.source
  if (typeof value.eventId !== 'string' || !value.eventId || typeof value.title !== 'string' || !isYmd(value.endDate)) return null
  if (kind !== 'academic' && kind !== 'exam' && kind !== 'notice' && kind !== 'personal') return null
  if (source !== 'schedule' && source !== 'notices' && source !== 'personal') return null
  // 개인 일정은 개인 종류로만, 공식 일정은 공식 종류로만 받는다.
  if ((source === 'personal') !== (kind === 'personal')) return null
  const reminder = (value.reminder ?? {}) as Record<string, unknown>
  const steps = Array.isArray(reminder.daysBefore) ? normalizeSteps(reminder.daysBefore.filter(isStep)) : []
  return {
    eventId: value.eventId,
    source,
    kind,
    title: value.title,
    startDate: isYmd(value.startDate) ? value.startDate : null,
    endDate: value.endDate,
    url: typeof value.url === 'string' && /^https?:\/\//i.test(value.url) ? value.url : null,
    reminder: { enabled: reminder.enabled === true && steps.length > 0, daysBefore: steps },
  }
}

export type SavedListResult = { signedIn: false } | { signedIn: true; events: SavedEvent[] }

/** 로그인하지 않았으면(401) signedIn: false. 그 밖의 실패는 오류로 던진다. */
export const fetchSavedEvents = async (signal?: AbortSignal): Promise<SavedListResult> => {
  try {
    const response = await apiFetch<unknown>('/calendar/saved', { method: 'GET', signal })
    const events = (Array.isArray(response) ? response : []).map(normalizeSavedEvent).filter((event): event is SavedEvent => event !== null)
    return { signedIn: true, events }
  } catch (error) {
    if ((error as ApiError | null)?.status === 401) return { signedIn: false }
    throw error
  }
}

/** 저장(처음이면 서버가 공식 일정을 다시 찾는다). 이미 저장했으면 알림 설정만 바뀐다. */
export const saveEvent = async (event: { id: string; endDate: string }, reminder: SavedReminder = DEFAULT_REMINDER) => {
  const response = await apiFetch<unknown>('/calendar/saved', {
    method: 'PUT',
    json: { eventId: event.id, endDate: event.endDate, reminder: { enabled: reminder.enabled, daysBefore: normalizeSteps(reminder.daysBefore) } },
  })
  return expectSaved(response)
}

export interface PersonalEventInput {
  title: string
  /** 하루짜리면 null(또는 endDate와 같음). */
  startDate: string | null
  endDate: string
  reminder: SavedReminder
}

const personalBody = (input: PersonalEventInput) => ({
  title: input.title.trim(),
  startDate: input.startDate && input.startDate !== input.endDate ? input.startDate : null,
  endDate: input.endDate,
  reminder: { enabled: input.reminder.enabled, daysBefore: normalizeSteps(input.reminder.daysBefore) },
})

const expectSaved = (response: unknown) => {
  const saved = normalizeSavedEvent(response)
  if (!saved) {
    const error: ApiError = new Error('저장 응답 형식이 올바르지 않습니다.')
    error.status = 502
    throw error
  }
  return saved
}

export const createPersonalEvent = async (input: PersonalEventInput) =>
  expectSaved(await apiFetch<unknown>('/calendar/saved/personal', { method: 'POST', json: personalBody(input) }))

export const updatePersonalEvent = async (eventId: string, input: PersonalEventInput) =>
  expectSaved(await apiFetch<unknown>('/calendar/saved/personal', { method: 'PUT', json: { eventId, ...personalBody(input) } }))

export const MAX_PERSONAL_TITLE = 100
const MAX_PERSONAL_SPAN_DAYS = 366

/** 입력값 검사. 서버도 같은 규칙으로 다시 검사한다. */
export const validatePersonalInput = (input: PersonalEventInput): string | null => {
  const title = input.title.trim()
  if (!title) return '제목을 입력해 주세요.'
  if (title.length > MAX_PERSONAL_TITLE) return `제목은 ${MAX_PERSONAL_TITLE}자까지 쓸 수 있어요.`
  if (!input.endDate) return '날짜를 골라 주세요.'
  if (input.startDate && input.startDate > input.endDate) return '종료일은 시작일과 같거나 뒤여야 해요.'
  if (input.startDate && diffDays(input.startDate, input.endDate) > MAX_PERSONAL_SPAN_DAYS) return `기간은 최대 ${MAX_PERSONAL_SPAN_DAYS}일까지 정할 수 있어요.`
  return null
}

export const isPersonalId = (id: string) => id.startsWith('personal:')

/** 개인 일정을 캘린더 일정 모양으로. 근거·원문이 없고 날짜는 사용자가 정한 값이다. */
export const personalToEvent = (saved: SavedEvent): CalendarEvent => ({
  id: saved.eventId,
  source: 'personal',
  kind: 'personal',
  title: saved.title,
  startDate: saved.startDate,
  endDate: saved.endDate,
  url: null,
  snippet: null,
  evidence: null,
  dateStatus: 'confirmed',
  department: null,
  publishedAt: null,
})

export const unsaveEvent = (eventId: string) =>
  apiFetch<void>(`/calendar/saved?${new URLSearchParams({ eventId })}`, { method: 'DELETE' })

const details = (error: unknown) => {
  const value = (error as ApiError | null)?.details
  return value && typeof value === 'object' ? (value as { error?: unknown; message?: unknown }) : undefined
}
const errorCode = (error: unknown) => details(error)?.error

export const savedErrorMessage = (error: unknown) => {
  const status = (error as ApiError | null)?.status
  const code = errorCode(error)
  if (status === 401) return '로그인하면 일정을 저장할 수 있어요.'
  if (code === 'date_unverified') return '날짜를 확실히 읽지 못한 일정이라 저장할 수 없어요. 원문에서 날짜를 확인해 주세요.'
  if (code === 'event_not_found') return '이 일정을 찾을 수 없어요. 캘린더를 새로고침해 주세요.'
  if (code === 'invalid_title') return '제목을 1~100자로 입력해 주세요.'
  if (code === 'invalid_date') return (details(error)?.message as string | undefined) ?? '날짜를 다시 확인해 주세요.'
  if (code === 'too_many_saved') return '저장할 수 있는 일정 수를 넘었어요. 지난 일정을 정리해 주세요.'
  if (status === 429) return '요청이 많아요. 잠시 후 다시 시도해 주세요.'
  if (status === 503) return '일정 정보를 잠시 불러올 수 없어요. 잠시 후 다시 시도해 주세요.'
  if (typeof status === 'number') return `저장하지 못했어요. (오류 ${status})`
  return '네트워크 연결을 확인해 주세요.'
}
