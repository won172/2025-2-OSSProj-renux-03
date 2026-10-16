/**
 * 캘린더 날짜 계산. 서버 날짜는 모두 KST 달력 날짜('YYYY-MM-DD')이므로
 * 기기 시간대와 무관하게 날짜 문자열 ↔ UTC 기준 일수로만 계산한다.
 */

export type EventKind = 'academic' | 'exam' | 'notice'

/** 날짜 계산에 필요한 최소 필드. startDate가 null이면 하루짜리(마감) 항목이다. */
export interface DatedItem {
  kind: EventKind
  startDate: string | null
  endDate: string
}

const DAY_MS = 86_400_000
const KST_OFFSET_MS = 9 * 60 * 60 * 1000
export const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'] as const

const pad = (value: number) => String(value).padStart(2, '0')

export const isYmd = (value: unknown): value is string => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  return fromDayNumber(toDayNumber(value)) === value
}

/** 'YYYY-MM-DD' → 1970-01-01 기준 일수. */
export const toDayNumber = (ymd: string) => {
  const [year, month, day] = ymd.split('-').map(Number)
  return Math.floor(Date.UTC(year, month - 1, day) / DAY_MS)
}

export const fromDayNumber = (dayNumber: number) => {
  const date = new Date(dayNumber * DAY_MS)
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

export const addDays = (ymd: string, days: number) => fromDayNumber(toDayNumber(ymd) + days)
export const diffDays = (from: string, to: string) => toDayNumber(to) - toDayNumber(from)

/** 기기 시간대와 무관한 KST 오늘 날짜. */
export const kstToday = (now: number = Date.now()) => fromDayNumber(Math.floor((now + KST_OFFSET_MS) / DAY_MS))

/** 0 = 일요일. */
export const weekdayOf = (ymd: string) => (((toDayNumber(ymd) + 4) % 7) + 7) % 7
export const weekdayLabel = (ymd: string) => WEEKDAYS[weekdayOf(ymd)]

export const eventStart = (item: DatedItem) => item.startDate ?? item.endDate

export interface MonthRef { year: number; month: number } // month: 1–12

export const monthOf = (ymd: string): MonthRef => ({ year: Number(ymd.slice(0, 4)), month: Number(ymd.slice(5, 7)) })
export const shiftMonth = ({ year, month }: MonthRef, delta: number): MonthRef => {
  const index = year * 12 + (month - 1) + delta
  return { year: Math.floor(index / 12), month: (index % 12) + 1 }
}
export const firstOfMonth = ({ year, month }: MonthRef) => `${year}-${pad(month)}-01`
export const lastOfMonth = (ref: MonthRef) => addDays(firstOfMonth(shiftMonth(ref, 1)), -1)
export const monthTitle = ({ year, month }: MonthRef) => `${year}년 ${month}월`

/** 일요일 시작 달력 칸. 이전·다음 달 날짜로 주를 채우며 4~6주가 된다. */
export const monthGrid = (ref: MonthRef) => {
  const first = firstOfMonth(ref)
  const last = lastOfMonth(ref)
  const start = addDays(first, -weekdayOf(first))
  const end = addDays(last, 6 - weekdayOf(last))
  const weeks: string[][] = []
  for (let day = start; day <= end; day = addDays(day, 7)) {
    weeks.push(Array.from({ length: 7 }, (_, index) => addDays(day, index)))
  }
  return { from: start, to: end, weeks }
}

/** 기간이 [from, to]와 겹치는지. */
export const overlaps = (item: DatedItem, from: string, to: string) => eventStart(item) <= to && item.endDate >= from
export const occursOn = (item: DatedItem, day: string) => overlaps(item, day, day)

/** 하루짜리 일정(시작일이 없거나 시작=종료). */
export const isSingleDay = (item: DatedItem) => item.startDate === null || item.startDate === item.endDate

/**
 * 카드에 쓰는 D-day 문구.
 * 시작일 없는 항목은 마감으로 본다. 기간 일정은 시작 전엔 시작까지, 진행 중엔 마감까지 센다.
 */
export const dDayText = (item: DatedItem, today: string) => {
  const end = diffDays(today, item.endDate)
  if (end < 0) return '종료'
  if (item.startDate === null) return end === 0 ? '오늘 마감' : `마감 D-${end}`
  const start = diffDays(today, item.startDate)
  if (isSingleDay(item)) return start === 0 ? '오늘' : `D-${start}`
  if (start > 0) return `시작 D-${start}`
  return end === 0 ? '오늘 마감' : `마감 D-${end}`
}

/** 상세 화면의 큰 D-day 숫자와 그 설명. */
export const dDayBig = (item: DatedItem, today: string) => {
  const end = diffDays(today, item.endDate)
  if (end < 0) return { value: '종료', caption: '지난 일정' }
  const start = diffDays(today, eventStart(item))
  if (item.startDate !== null && start > 0) return { value: `D-${start}`, caption: isSingleDay(item) ? '' : '시작까지' }
  if (end === 0) return { value: 'D-DAY', caption: item.startDate === null || !isSingleDay(item) ? '오늘 마감' : '오늘' }
  return { value: `D-${end}`, caption: item.startDate === null ? '마감까지' : '종료까지' }
}

const shortDate = (ymd: string, today: string) => {
  const { year, month } = monthOf(ymd)
  const prefix = year !== monthOf(today).year ? `${year}.` : ''
  return `${prefix}${month}.${Number(ymd.slice(8, 10))}(${weekdayLabel(ymd)})`
}

/** "10.19(월) – 10.23(금)" 또는 하루짜리 "10.19(월)". 올해가 아니면 연도를 붙인다. */
export const formatPeriod = (item: DatedItem, today: string) => isSingleDay(item)
  ? shortDate(item.endDate, today)
  : `${shortDate(eventStart(item), today)} – ${shortDate(item.endDate, today)}`

export const kindOrder: Record<EventKind, number> = { academic: 0, notice: 1, exam: 2 }

/** 끝나지 않은 일정을 정렬해 '곧 마감' → '다음 주' → 월별로 묶는다. */
export const groupUpcoming = <T extends DatedItem>(items: T[], today: string) => {
  const soon: T[] = []
  const nextWeek: T[] = []
  const months = new Map<string, T[]>()
  const nextWeekStart = addDays(today, 7 - weekdayOf(today))
  const nextWeekEnd = addDays(nextWeekStart, 6)
  const key = (item: T) => (eventStart(item) > today ? eventStart(item) : today)
  const sorted = items
    .filter((item) => item.endDate >= today)
    .sort((a, b) => key(a).localeCompare(key(b)) || a.endDate.localeCompare(b.endDate))

  for (const item of sorted) {
    const anchor = key(item)
    if (diffDays(today, item.endDate) <= 7) soon.push(item)
    else if (anchor >= nextWeekStart && anchor <= nextWeekEnd) nextWeek.push(item)
    else {
      const bucket = anchor.slice(0, 7)
      months.set(bucket, [...(months.get(bucket) ?? []), item])
    }
  }

  const groups: { id: string; label: string; items: T[] }[] = []
  if (soon.length) groups.push({ id: 'soon', label: '곧 마감', items: soon })
  if (nextWeek.length) groups.push({ id: 'next-week', label: '다음 주', items: nextWeek })
  for (const [bucket, bucketItems] of months) {
    const ref = monthOf(`${bucket}-01`)
    const label = ref.year === monthOf(today).year ? `${ref.month}월` : monthTitle(ref)
    groups.push({ id: bucket, label, items: bucketItems })
  }
  return groups
}

export interface WeekSegment<T> { item: T; startCol: number; span: number; lane: number; continues: boolean }

/**
 * 한 주(일~토) 안에서 여러 날 일정을 이어진 막대로 배치한다.
 * 겹치지 않게 lane을 고르며, 막대 글자는 구간의 첫 칸(시작일 또는 일요일)에만 둔다.
 */
export const layoutWeek = <T extends DatedItem>(items: T[], week: string[]) => {
  const from = week[0]
  const to = week[6]
  const placed = items
    .filter((item) => overlaps(item, from, to))
    .sort((a, b) => eventStart(a).localeCompare(eventStart(b))
      || diffDays(eventStart(b), b.endDate) - diffDays(eventStart(a), a.endDate)
      || kindOrder[a.kind] - kindOrder[b.kind])
  const lanes: number[] = [] // lane별 마지막으로 차지한 열
  return placed.map((item): WeekSegment<T> => {
    const startCol = Math.max(0, diffDays(from, eventStart(item)))
    const endCol = Math.min(6, diffDays(from, item.endDate))
    let lane = lanes.findIndex((lastCol) => lastCol < startCol)
    if (lane === -1) lane = lanes.length
    lanes[lane] = endCol
    return { item, startCol, span: endCol - startCol + 1, lane, continues: eventStart(item) < from }
  })
}
