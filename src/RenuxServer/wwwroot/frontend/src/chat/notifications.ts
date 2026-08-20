import type { UserNotification } from '../types/notification'

/**
 * 알림함 표시 로직.
 *
 * 서버는 리마인드 시점마다 별도 알림을 만든다(7일 전·1일 전·당일 = 3건).
 * 사용자에게 그건 하나의 마감이므로, 화면에서는 같은 마감을 한 줄로 접고
 * 가장 임박한 리마인드를 대표로 보여준다.
 */

export type NotificationUrgency = 'overdue' | 'today' | 'urgent' | 'soon' | 'later'

export interface NotificationGroup {
  /** 대표 알림(가장 임박한 리마인드). 클릭·읽음 처리의 기준. */
  primary: UserNotification
  /** 같은 마감으로 묶인 모든 알림 id — '모두 읽음' 시 함께 처리한다. */
  ids: string[]
  /** 묶인 알림 중 하나라도 읽지 않았으면 이 그룹은 읽지 않은 것으로 본다. */
  hasUnread: boolean
  dDay: number
  urgency: NotificationUrgency
  dDayLabel: string
}

const startOfLocalDay = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

/**
 * 마감까지 남은 날짜. 시간 차가 아니라 '자정 기준 며칠'로 세야
 * 사용자가 말하는 "내일 마감"과 일치한다.
 */
export const getDDay = (targetDate: string, now = new Date()): number | null => {
  const target = new Date(targetDate)
  if (Number.isNaN(target.getTime())) return null
  return Math.round((startOfLocalDay(target) - startOfLocalDay(now)) / 86_400_000)
}

export const getUrgency = (dDay: number): NotificationUrgency => {
  if (dDay < 0) return 'overdue'
  if (dDay === 0) return 'today'
  if (dDay <= 3) return 'urgent'
  if (dDay <= 7) return 'soon'
  return 'later'
}

export const formatDDay = (dDay: number): string => {
  if (dDay === 0) return 'D-day'
  if (dDay > 0) return `D-${dDay}`
  return `D+${Math.abs(dDay)}`
}

/** 같은 마감인지 판별하는 키. 출처와 대상 날짜가 같으면 하나의 마감으로 본다. */
const groupKey = (notification: UserNotification) => {
  const targetDay = notification.targetDate?.slice(0, 10) ?? ''
  return `${notification.source}::${notification.sourceId}::${targetDay}`
}

export interface GroupOptions {
  /** 마감이 지난 알림도 포함할지. 기본은 감춘다. */
  includePast?: boolean
  now?: Date
}

/**
 * 알림을 마감 단위로 묶고 임박한 순으로 정렬한다.
 *
 * 정렬 기준은 생성 시각이 아니라 D-day다 — 알림함의 목적은
 * "언제 받았는가"가 아니라 "무엇이 급한가"이기 때문이다.
 */
export const groupNotifications = (
  notifications: UserNotification[],
  { includePast = false, now = new Date() }: GroupOptions = {},
): NotificationGroup[] => {
  const buckets = new Map<string, UserNotification[]>()

  notifications.forEach((notification) => {
    const key = groupKey(notification)
    const bucket = buckets.get(key)
    if (bucket) bucket.push(notification)
    else buckets.set(key, [notification])
  })

  const groups: NotificationGroup[] = []

  buckets.forEach((bucket) => {
    const dDay = getDDay(bucket[0].targetDate, now)
    if (dDay === null) return
    if (!includePast && dDay < 0) return

    // 대표는 '가장 임박한 리마인드' = reminderDaysBefore가 가장 작은 것.
    // 같으면 나중에 만들어진 쪽(최신 문구)을 쓴다.
    const sorted = [...bucket].sort((a, b) => {
      if (a.reminderDaysBefore !== b.reminderDaysBefore) {
        return a.reminderDaysBefore - b.reminderDaysBefore
      }
      return new Date(b.createdTime).getTime() - new Date(a.createdTime).getTime()
    })

    groups.push({
      primary: sorted[0],
      ids: sorted.map((notification) => notification.id),
      hasUnread: sorted.some((notification) => !notification.isRead),
      dDay,
      urgency: getUrgency(dDay),
      dDayLabel: formatDDay(dDay),
    })
  })

  return groups.sort((a, b) => {
    if (a.dDay !== b.dDay) return a.dDay - b.dDay
    // 같은 날 마감이면 읽지 않은 것을 위로 올려 눈에 띄게 한다.
    if (a.hasUnread !== b.hasUnread) return a.hasUnread ? -1 : 1
    return a.primary.title.localeCompare(b.primary.title, 'ko')
  })
}

/** 배지에 쓰는 '읽지 않은 마감 수'. 알림 건수가 아니라 마감 건수를 센다. */
export const countUnreadGroups = (groups: NotificationGroup[]) =>
  groups.filter((group) => group.hasUnread).length

/**
 * 알림에서 챗봇에게 물어볼 질문을 만든다.
 * 제목이 그대로 좋은 질문이 되는 경우가 많아 제목을 살리고 의도만 덧붙인다.
 */
export const buildNotificationQuestion = (notification: UserNotification): string => {
  const title = notification.title.trim()
  if (!title) return '다가오는 마감 일정 알려줘'
  return `${title} 자세히 알려줘`
}
