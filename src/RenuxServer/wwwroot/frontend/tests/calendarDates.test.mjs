import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addDays,
  dDayBig,
  dDayText,
  diffDays,
  formatPeriod,
  groupUpcoming,
  isYmd,
  kstToday,
  layoutWeek,
  monthGrid,
  occursOn,
  overlaps,
  shiftMonth,
  weekdayLabel,
} from '../src/pages/calendar/calendarDates.ts'

const ev = (startDate, endDate, extra = {}) => ({ kind: 'academic', startDate, endDate, ...extra })

test('kstToday uses KST regardless of the device time zone', () => {
  // 2026-10-15 15:00Z = 2026-10-16 00:00 KST
  assert.equal(kstToday(Date.UTC(2026, 9, 15, 15, 0)), '2026-10-16')
  assert.equal(kstToday(Date.UTC(2026, 9, 15, 14, 59)), '2026-10-15')
  // 연말 경계
  assert.equal(kstToday(Date.UTC(2026, 11, 31, 15, 0)), '2027-01-01')
})

test('date strings: validation, arithmetic and weekday labels', () => {
  assert.equal(isYmd('2026-10-19'), true)
  assert.equal(isYmd('2026-02-30'), false)
  assert.equal(isYmd('2026-1-01'), false)
  assert.equal(isYmd(null), false)
  assert.equal(addDays('2026-12-31', 1), '2027-01-01')
  assert.equal(addDays('2028-03-01', -1), '2028-02-29')
  assert.equal(diffDays('2026-10-16', '2026-10-21'), 5)
  assert.deepEqual(['2026-10-18', '2026-10-19', '2026-10-24'].map(weekdayLabel), ['일', '월', '토'])
  assert.deepEqual(shiftMonth({ year: 2026, month: 1 }, -1), { year: 2025, month: 12 })
  assert.deepEqual(shiftMonth({ year: 2026, month: 12 }, 1), { year: 2027, month: 1 })
})

test('monthGrid starts on Sunday, covers whole weeks and stays within the 92-day API limit', () => {
  const grid = monthGrid({ year: 2026, month: 10 })
  assert.equal(grid.from, '2026-09-27')
  assert.equal(grid.to, '2026-10-31')
  assert.equal(grid.weeks.length, 5)
  assert.ok(grid.weeks.every((week) => week.length === 7 && weekdayLabel(week[0]) === '일'))
  // 6주가 필요한 달
  const may = monthGrid({ year: 2026, month: 8 })
  assert.equal(may.weeks.length, 6)
  assert.ok(diffDays(may.from, may.to) + 1 <= 92)
})

test('overlap with the visible month and single-day items (startDate null = endDate)', () => {
  assert.equal(overlaps(ev('2026-09-28', '2026-10-02'), '2026-10-01', '2026-10-31'), true)
  assert.equal(overlaps(ev('2026-09-01', '2026-09-30'), '2026-10-01', '2026-10-31'), false)
  assert.equal(overlaps(ev(null, '2026-10-01'), '2026-10-01', '2026-10-31'), true)
  assert.equal(occursOn(ev(null, '2026-10-20'), '2026-10-20'), true)
  assert.equal(occursOn(ev(null, '2026-10-20'), '2026-10-19'), false, 'deadline items occupy only their end date')
  assert.equal(occursOn(ev('2026-10-19', '2026-10-23'), '2026-10-21'), true)
})

test('D-day text in KST calendar days', () => {
  const today = '2026-10-16'
  assert.equal(dDayText(ev('2026-10-19', '2026-10-23'), today), '시작 D-3')
  assert.equal(dDayText(ev('2026-10-12', '2026-10-21'), today), '마감 D-5')
  assert.equal(dDayText(ev(null, '2026-10-21'), today), '마감 D-5')
  assert.equal(dDayText(ev(null, '2026-10-16'), today), '오늘 마감')
  assert.equal(dDayText(ev('2026-10-10', '2026-10-16'), today), '오늘 마감')
  assert.equal(dDayText(ev('2026-10-01', '2026-10-15'), today), '종료')
  assert.equal(dDayText(ev('2026-10-20', '2026-10-20'), today), 'D-4')
  assert.equal(dDayText(ev('2026-10-16', '2026-10-16'), today), '오늘')
  assert.deepEqual(dDayBig(ev('2026-10-19', '2026-10-23'), today), { value: 'D-3', caption: '시작까지' })
  assert.deepEqual(dDayBig(ev(null, '2026-10-21'), today), { value: 'D-5', caption: '마감까지' })
  assert.deepEqual(dDayBig(ev(null, '2026-10-16'), today), { value: 'D-DAY', caption: '오늘 마감' })
  assert.deepEqual(dDayBig(ev('2026-10-01', '2026-10-02'), today), { value: '종료', caption: '지난 일정' })
})

test('period labels match the mockup and add the year only outside the current year', () => {
  const today = '2026-10-16'
  assert.equal(formatPeriod(ev('2026-10-19', '2026-10-23'), today), '10.19(월) – 10.23(금)')
  assert.equal(formatPeriod(ev(null, '2026-10-21'), today), '10.21(수)')
  assert.equal(formatPeriod(ev('2026-12-28', '2027-01-04'), today), '12.28(월) – 2027.1.4(월)')
})

test('groupUpcoming: 곧 마감 (ends within 7 days), 다음 주, then by month; ended items are dropped', () => {
  const today = '2026-10-16' // 금요일 → 다음 주는 10.18(일)–10.24(토)
  const items = [
    ev('2026-10-01', '2026-10-10', { id: 'ended' }),
    ev(null, '2026-10-20', { id: 'deadline' }),
    ev('2026-10-12', '2026-10-23', { id: 'ongoing' }),
    ev('2026-10-22', '2026-10-30', { id: 'next-week' }),
    ev('2026-11-02', '2026-11-06', { id: 'nov' }),
    ev('2027-01-04', '2027-01-08', { id: 'jan' }),
  ]
  const groups = groupUpcoming(items, today)
  assert.deepEqual(groups.map((group) => [group.label, group.items.map((item) => item.id)]), [
    ['곧 마감', ['ongoing', 'deadline']],
    ['다음 주', ['next-week']],
    ['11월', ['nov']],
    ['2027년 1월', ['jan']],
  ])
})

test('layoutWeek draws multi-day events as continuous bars in non-overlapping lanes', () => {
  const week = monthGrid({ year: 2026, month: 10 }).weeks[3] // 10.18(일)–10.24(토)
  const long = ev('2026-10-12', '2026-10-21', { id: 'long' })
  const mid = ev('2026-10-20', '2026-10-27', { id: 'mid' })
  const dot = ev(null, '2026-10-19', { id: 'dot', kind: 'notice' })
  const segments = layoutWeek([mid, dot, long], week)
  const byId = Object.fromEntries(segments.map((segment) => [segment.item.id, segment]))
  assert.deepEqual([byId.long.startCol, byId.long.span, byId.long.continues], [0, 4, true])
  assert.deepEqual([byId.dot.startCol, byId.dot.span], [1, 1])
  assert.deepEqual([byId.mid.startCol, byId.mid.span, byId.mid.continues], [2, 5, false])
  assert.notEqual(byId.long.lane, byId.dot.lane)
  assert.notEqual(byId.long.lane, byId.mid.lane)
  assert.equal(layoutWeek([ev('2026-11-01', '2026-11-02')], week).length, 0)
})
