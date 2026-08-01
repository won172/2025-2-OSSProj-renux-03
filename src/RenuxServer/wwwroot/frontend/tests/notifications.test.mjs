import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildNotificationQuestion,
  countUnreadGroups,
  formatDDay,
  getDDay,
  getUrgency,
  groupNotifications,
} from '../src/chat/notifications.ts'

// 기준 시각을 고정해 D-day 판정이 실행 시점에 흔들리지 않게 한다.
const NOW = new Date(2026, 6, 30, 14, 0, 0) // 2026-07-30 14:00 로컬
const at = (year, month, day, hour = 0) => new Date(year, month - 1, day, hour).toISOString()

let seq = 0
const makeNotification = (overrides = {}) => {
  seq += 1
  return {
    id: `n${seq}`,
    topic: 'scholarship',
    topicLabel: '장학',
    source: 'notices',
    sourceId: 'a-100',
    title: '국가장학금 2차 신청',
    body: '',
    targetDate: at(2026, 8, 1),
    reminderDate: at(2026, 7, 30),
    reminderDaysBefore: 1,
    url: null,
    isRead: false,
    createdTime: at(2026, 7, 30, 9),
    ...overrides,
  }
}

test('D-day는 시간 차가 아니라 자정 기준으로 센다', () => {
  // 오늘 23시 마감은 9시간 뒤지만 'D-day'여야 한다.
  assert.equal(getDDay(at(2026, 7, 30, 23), NOW), 0)
  // 내일 01시 마감은 11시간 뒤지만 'D-1'이어야 한다.
  assert.equal(getDDay(at(2026, 7, 31, 1), NOW), 1)
  assert.equal(getDDay(at(2026, 7, 29), NOW), -1)
  assert.equal(getDDay('날짜아님', NOW), null)
})

test('긴급도는 남은 일수 구간으로 나눈다', () => {
  assert.equal(getUrgency(-1), 'overdue')
  assert.equal(getUrgency(0), 'today')
  assert.equal(getUrgency(3), 'urgent')
  assert.equal(getUrgency(4), 'soon')
  assert.equal(getUrgency(7), 'soon')
  assert.equal(getUrgency(8), 'later')
})

test('D-day 라벨 표기', () => {
  assert.equal(formatDDay(0), 'D-day')
  assert.equal(formatDDay(5), 'D-5')
  assert.equal(formatDDay(-2), 'D+2')
})

test('같은 마감의 여러 리마인드를 한 항목으로 접는다', () => {
  // 서버는 7일 전·1일 전·당일 리마인드를 각각 별도 알림으로 만든다.
  const notifications = [
    makeNotification({ id: 'r7', reminderDaysBefore: 7, createdTime: at(2026, 7, 25) }),
    makeNotification({ id: 'r1', reminderDaysBefore: 1, createdTime: at(2026, 7, 31) }),
    makeNotification({ id: 'r0', reminderDaysBefore: 0, createdTime: at(2026, 8, 1) }),
  ]

  const groups = groupNotifications(notifications, { now: NOW })

  assert.equal(groups.length, 1, '세 건이 한 줄로 접혀야 합니다')
  // 대표는 가장 임박한 리마인드(당일)여야 한다.
  assert.equal(groups[0].primary.id, 'r0')
  assert.deepEqual(groups[0].ids.sort(), ['r0', 'r1', 'r7'])
})

test('출처나 마감 날짜가 다르면 별개의 마감으로 본다', () => {
  const groups = groupNotifications([
    makeNotification({ sourceId: 'a-100' }),
    makeNotification({ sourceId: 'a-200' }),
    makeNotification({ sourceId: 'a-100', targetDate: at(2026, 8, 5) }),
    makeNotification({ sourceId: 'a-100', source: 'schedule' }),
  ], { now: NOW })

  assert.equal(groups.length, 4)
})

test('묶인 알림 중 하나라도 읽지 않았으면 그룹은 읽지 않은 상태다', () => {
  const groups = groupNotifications([
    makeNotification({ id: 'x1', reminderDaysBefore: 7, isRead: true }),
    makeNotification({ id: 'x2', reminderDaysBefore: 1, isRead: false }),
  ], { now: NOW })

  assert.equal(groups[0].hasUnread, true)
  assert.equal(countUnreadGroups(groups), 1)
})

test('모두 읽었으면 그룹도 읽은 상태이고 배지에서 빠진다', () => {
  const groups = groupNotifications([
    makeNotification({ id: 'y1', reminderDaysBefore: 7, isRead: true }),
    makeNotification({ id: 'y2', reminderDaysBefore: 1, isRead: true }),
  ], { now: NOW })

  assert.equal(groups[0].hasUnread, false)
  assert.equal(countUnreadGroups(groups), 0)
})

test('지난 마감은 기본적으로 감추고 옵션으로만 보여준다', () => {
  const notifications = [
    makeNotification({ id: 'past', sourceId: 'past', targetDate: at(2026, 7, 28) }),
    makeNotification({ id: 'upcoming', sourceId: 'upcoming', targetDate: at(2026, 8, 3) }),
  ]

  assert.deepEqual(
    groupNotifications(notifications, { now: NOW }).map((group) => group.primary.id),
    ['upcoming'],
  )
  assert.equal(groupNotifications(notifications, { now: NOW, includePast: true }).length, 2)
})

test('생성 시각이 아니라 마감이 임박한 순으로 정렬한다', () => {
  const groups = groupNotifications([
    // 가장 최근에 만들어졌지만 마감은 가장 멀다.
    makeNotification({ id: 'far', sourceId: 'far', targetDate: at(2026, 8, 20), createdTime: at(2026, 7, 30, 13) }),
    makeNotification({ id: 'today', sourceId: 'today', targetDate: at(2026, 7, 30), createdTime: at(2026, 7, 20) }),
    makeNotification({ id: 'mid', sourceId: 'mid', targetDate: at(2026, 8, 5), createdTime: at(2026, 7, 25) }),
  ], { now: NOW })

  assert.deepEqual(groups.map((group) => group.primary.id), ['today', 'mid', 'far'])
  assert.deepEqual(groups.map((group) => group.dDayLabel), ['D-day', 'D-6', 'D-21'])
})

test('같은 날 마감이면 읽지 않은 것을 위로 올린다', () => {
  const groups = groupNotifications([
    makeNotification({ id: 'read', sourceId: 'read', title: '가 공지', isRead: true }),
    makeNotification({ id: 'unread', sourceId: 'unread', title: '나 공지', isRead: false }),
  ], { now: NOW })

  assert.deepEqual(groups.map((group) => group.primary.id), ['unread', 'read'])
})

test('날짜를 해석할 수 없는 알림은 목록에서 제외한다', () => {
  const groups = groupNotifications([
    makeNotification({ id: 'broken', sourceId: 'broken', targetDate: '알 수 없음' }),
    makeNotification({ id: 'ok', sourceId: 'ok' }),
  ], { now: NOW })

  assert.deepEqual(groups.map((group) => group.primary.id), ['ok'])
})

test('알림 제목으로 챗봇 질문을 만든다', () => {
  assert.equal(
    buildNotificationQuestion(makeNotification({ title: '  국가장학금 2차 신청  ' })),
    '국가장학금 2차 신청 자세히 알려줘',
  )
  // 제목이 비어 있어도 물어볼 수 있는 문장이 나와야 한다.
  assert.equal(buildNotificationQuestion(makeNotification({ title: '   ' })), '다가오는 마감 일정 알려줘')
})
