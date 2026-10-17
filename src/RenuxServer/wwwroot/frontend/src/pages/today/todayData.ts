import type { BriefingMeal, BriefingNotice, HomeBriefing } from '../../types/briefing.ts'
import type { CalendarEvent } from '../calendar/calendarApi.ts'
import { WEEKDAYS, addDays, eventStart, monthOf, occursOn, weekdayOf } from '../calendar/calendarDates.ts'

/** 홈 '다가오는 일정'이 조회하는 기간(오늘 포함 일수). 캘린더 API 상한(92일) 안이다. */
export const UPCOMING_RANGE_DAYS = 45

/** "10월 17일 토요일" */
export const todayLabel = (today: string) => {
  const { month } = monthOf(today)
  return `${month}월 ${Number(today.slice(8, 10))}일 ${WEEKDAYS[weekdayOf(today)]}요일`
}

/**
 * 확정된 일정 중 끝나지 않은 것을 가까운 순서로. 시작 전이면 시작일, 진행 중이면 마감일 기준이다.
 * 날짜 확인이 필요한(unverified) 일정은 홈에 띄우지 않는다(캘린더에서만 따로 보여준다).
 */
export const upcomingEvents = (events: CalendarEvent[], today: string, limit: number) => {
  const anchor = (event: CalendarEvent) => (eventStart(event) > today ? eventStart(event) : event.endDate)
  return events
    .filter((event) => event.dateStatus === 'confirmed' && event.endDate >= today)
    .sort((a, b) => anchor(a).localeCompare(anchor(b)) || a.endDate.localeCompare(b.endDate) || a.title.localeCompare(b.title, 'ko'))
    .slice(0, limit)
}

export interface TodayRestaurant {
  name: string
  corners: BriefingMeal[]
}

/** 식당별 코너. 식당 목록이 없는 이전 서버 응답이면 기존 대표 코너를 한 묶음으로 보여준다. */
export const restaurantsFromBriefing = (briefing: HomeBriefing | null): TodayRestaurant[] => {
  if (!briefing) return []
  const restaurants = (Array.isArray(briefing.restaurants) ? briefing.restaurants : [])
    .filter((restaurant) => typeof restaurant?.name === 'string' && restaurant.name.trim() && Array.isArray(restaurant.corners))
    .map((restaurant) => ({
      name: restaurant.name.trim(),
      corners: restaurant.corners.filter((corner) => corner?.corner && corner?.menu),
    }))
    .filter((restaurant) => restaurant.corners.length > 0)
  if (restaurants.length > 0) return restaurants
  const meals = (Array.isArray(briefing.meals) ? briefing.meals : []).filter((meal) => meal?.corner && meal?.menu)
  return meals.length > 0 ? [{ name: '오늘 학식', corners: meals }] : []
}

const httpUrl = (value: unknown) => (typeof value === 'string' && /^https?:\/\//i.test(value) ? value : null)

/** 원문 링크는 http(s)만 남긴다. 제목이 없는 공지는 버린다. */
export const briefingNotices = (briefing: HomeBriefing | null): (BriefingNotice & { url: string | null })[] =>
  (Array.isArray(briefing?.notices) ? briefing.notices : [])
    .filter((notice) => typeof notice?.title === 'string' && notice.title.trim())
    .map((notice) => ({ ...notice, title: notice.title.trim(), url: httpUrl(notice.url), board: notice.board?.trim() || null }))

export interface WeekDay {
  date: string
  label: string
  isToday: boolean
  weekday: number
  events: CalendarEvent[]
}

/** 오늘부터 7일. 각 날짜에 걸친 확정 일정을 담는다. */
export const nextSevenDays = (events: CalendarEvent[], today: string): WeekDay[] =>
  Array.from({ length: 7 }, (_, index) => {
    const date = addDays(today, index)
    const { month } = monthOf(date)
    return {
      date,
      label: `${month}.${Number(date.slice(8, 10))} ${WEEKDAYS[weekdayOf(date)]}`,
      isToday: index === 0,
      weekday: weekdayOf(date),
      events: events.filter((event) => event.dateStatus === 'confirmed' && occursOn(event, date)),
    }
  })

/** 일정 카드에서 캘린더 상세로 가는 주소. 상세가 데이터를 다시 받을 때 쓸 달을 함께 넘긴다. */
export const eventDetailPath = (event: CalendarEvent, today: string) => {
  const day = eventStart(event) > today ? eventStart(event) : today
  return `/calendar/event/${encodeURIComponent(event.id)}?d=${day > event.endDate ? event.endDate : day}`
}
